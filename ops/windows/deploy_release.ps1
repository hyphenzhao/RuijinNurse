[CmdletBinding()]
param(
    [ValidateSet('prepare','deploy','rollback')]
    [string]$Mode='prepare',
    [string]$Stage=(Join-Path $env:LOCALAPPDATA 'RuijinNurse\releases\fixes-20261111')
)

$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$expectedStage=Join-Path $env:LOCALAPPDATA 'RuijinNurse\releases\fixes-20261111'
if([IO.Path]::GetFullPath($Stage) -ine [IO.Path]::GetFullPath($expectedStage)){throw 'Unexpected release staging directory'}
$expectedScripts=Join-Path $Stage 'ops\windows'
if([IO.Path]::GetFullPath($PSScriptRoot) -ine [IO.Path]::GetFullPath($expectedScripts)){
    throw 'Run the external staged ops/windows script, not the source checkout copy'
}
$app='D:\RuijinNurse_20260616_Update'
$python='D:\RuijinNurse\venv\Scripts\python.exe'
$site=Join-Path $app 'mysite'
$sourceSite=Join-Path $Stage 'source\mysite'
$helper=Join-Path $PSScriptRoot 'release_files.py'
$privateRoot=Join-Path $env:LOCALAPPDATA 'RuijinNurse'
$privateConfig=Join-Path $privateRoot 'production.json'
$taskName='RuijinNurse-Django'
$taskPath='\'
$logs=Join-Path $Stage 'logs'
$backup=Join-Path $Stage 'backup-cutover'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONUTF8='1'
$env:GIT_TERMINAL_PROMPT='0'
$stamp=[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
$currentSid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$cutoverStarted=$false
$sourceMutationStarted=$false
$backupComplete=$false
$oldTaskXml=$null

function Set-PrivateDirectoryAcl([string]$Path) {
    [IO.Directory]::CreateDirectory($Path) | Out-Null
    $acl=New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner($currentSid)
    foreach($sid in @($currentSid, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'), [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))){
        $rule=[Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function ConvertTo-NativeArgument([string]$Value) {
    if($Value -notmatch '[\s"]' -and $Value.Length -gt 0){return $Value}
    $escaped=[regex]::Replace($Value,'(\\*)"','$1$1\"')
    $escaped=[regex]::Replace($escaped,'(\\+)$','$1$1')
    return '"'+$escaped+'"'
}

function Invoke-CheckedProcess([string]$Label,[string]$Executable,[string[]]$Arguments,[string]$WorkingDirectory=$Stage) {
    $stdout=Join-Path $logs "$stamp-$Label.stdout.log"
    $stderr=Join-Path $logs "$stamp-$Label.stderr.log"
    $quoted=@($Arguments | ForEach-Object {ConvertTo-NativeArgument $_})
    $process=Start-Process -FilePath $Executable -ArgumentList ($quoted -join ' ') -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -Wait -PassThru -NoNewWindow
    if($process.ExitCode -ne 0){throw "$Label failed with exit code $($process.ExitCode); details are in the private release logs"}
}

function Invoke-ReleaseOperation([string]$Operation) {
    Invoke-CheckedProcess $Operation $python @('-B',$helper,'--stage',$Stage,$Operation)
}

function Get-PortOwner {
    $owners=@(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
    if($owners.Count -gt 1){throw 'Multiple independent processes listen on port 80'}
    if($owners.Count -eq 1){return [int]$owners[0]}
    return $null
}

function Assert-DjangoOwner([int]$ProcessId) {
    $process=Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    if(!$process -or $process.CommandLine -notmatch '(?i)(manage\.py"?\s+runserver\s+0\.0\.0\.0:80|serve\.py)(?:\s|$)'){
        throw 'Port 80 is not owned by the expected Django command'
    }
    $ancestor=$process
    $expectedPython=$false
    for($i=0;$i -lt 8 -and $ancestor;$i++){
        if($ancestor.ExecutablePath -ieq $python){$expectedPython=$true;break}
        $ancestor=Get-CimInstance Win32_Process -Filter "ProcessId=$($ancestor.ParentProcessId)" -ErrorAction SilentlyContinue
    }
    if(!$expectedPython){throw 'Django listener is not launched by the existing virtual environment'}
    return $process
}

function Stop-DjangoSafely {
    $owner=Get-PortOwner
    if($owner){Assert-DjangoOwner $owner | Out-Null}
    $currentTask=Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath
    if($currentTask.State -eq 'Running' -or $currentTask.State -eq 'Queued'){
        Stop-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop
    }
    $deadline=[DateTime]::UtcNow.AddSeconds(15)
    while((Get-PortOwner) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 250}
    $remaining=Get-PortOwner
    if($remaining){
        if($remaining -ne $owner){throw 'Port owner changed during service stop; refusing to kill another process'}
        Assert-DjangoOwner $remaining | Out-Null
        & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /PID $remaining /T /F | Out-Null
        if($LASTEXITCODE -ne 0){throw 'Could not terminate the verified Django process'}
        Start-Sleep -Milliseconds 500
    }
    if(Get-PortOwner){throw 'Port 80 has not been released'}
}

function Get-HttpResult([string]$Path,[string]$Method='GET') {
    $request=[Net.HttpWebRequest]::Create('http://127.0.0.1'+$Path)
    $request.Method=$Method
    $request.Proxy=$null
    $request.AllowAutoRedirect=$false
    $request.Timeout=4000
    $response=$null
    try{$response=$request.GetResponse()}
    catch [Net.WebException] {if($_.Exception.Response){$response=$_.Exception.Response}else{return @{Status=0}}}
    try{
        return @{Status=[int]$response.StatusCode;COOP=$response.Headers['Cross-Origin-Opener-Policy'];COEP=$response.Headers['Cross-Origin-Embedder-Policy']}
    } finally{if($response){$response.Close()}}
}

function Wait-Django([bool]$NewRelease) {
    $deadline=[DateTime]::UtcNow.AddSeconds(90)
    while([DateTime]::UtcNow -lt $deadline){
        $owner=Get-PortOwner
        if($owner){
            Assert-DjangoOwner $owner | Out-Null
            $task=Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath
            $process=Get-Process -Id $owner
            if($task.State -eq 'Running' -and $process.SessionId -eq 0 -and (Get-HttpResult '/accounts/login/').Status -eq 200){
                if(!$NewRelease){return $owner}
                $static=Get-HttpResult '/static/promotions/vendor/markdown-it-13.0.1/markdown-it.min.js' 'HEAD'
                $unity=Get-HttpResult '/static/promotions/unity/Build/Build/Build.wasm' 'HEAD'
                if($static.Status -eq 200 -and $unity.Status -eq 200 -and $unity.COOP -eq 'same-origin' -and $unity.COEP -eq 'require-corp' -and `
                   (Get-HttpResult '/api/v1/health/').Status -eq 200 -and (Get-HttpResult '/api/v1/sessions/').Status -eq 401 -and `
                   (Get-HttpResult '/static/promotions/input.txt').Status -eq 404){return $owner}
            }
        }
        Start-Sleep -Milliseconds 500
    }
    throw 'The scheduled Django service did not pass its HTTP/static/API acceptance checks'
}

function Read-TaskXml {return Export-ScheduledTask -TaskName $taskName -TaskPath $taskPath}

function Restore-TaskAction([string]$XmlText) {
    [xml]$definition=$XmlText
    $actions=@(foreach($exec in $definition.Task.Actions.Exec){
        New-ScheduledTaskAction -Execute ([string]$exec.Command) -Argument ([string]$exec.Arguments) -WorkingDirectory ([string]$exec.WorkingDirectory)
    })
    if($actions.Count -ne 1){throw 'Expected exactly one existing Django task action'}
    Set-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Action $actions | Out-Null
}

function Assert-PreservedTaskConfiguration([string]$BeforeXml) {
    [xml]$before=$BeforeXml
    [xml]$after=Read-TaskXml
    foreach($name in @('Triggers','Principals','Settings')){
        if($before.Task.$name.OuterXml -ne $after.Task.$name.OuterXml){throw "Django task $name changed unexpectedly"}
    }
}

function Recover-PreviousRelease {
    Stop-DjangoSafely
    Invoke-ReleaseOperation 'restore'
    $previous=[IO.File]::ReadAllText((Join-Path $backup 'django-task.xml'))
    Restore-TaskAction $previous
    Assert-PreservedTaskConfiguration $previous
    Start-ScheduledTask -TaskName $taskName -TaskPath $taskPath
    $owner=Wait-Django $false
    Write-Output "ROLLBACK_ACCEPTED HTTP=200 PID=$owner DATABASE_NOT_RESTORED"
}

Set-PrivateDirectoryAcl $privateRoot
Set-PrivateDirectoryAcl $Stage
[IO.Directory]::CreateDirectory($logs) | Out-Null
if(!(Test-Path -LiteralPath $python -PathType Leaf)){throw 'Existing virtual environment Python is missing'}

try {
    if($Mode -eq 'rollback'){
        Recover-PreviousRelease
        exit 0
    }
    if($Mode -eq 'prepare'){
        if(Test-Path -LiteralPath $backup){throw 'A cutover backup already exists; inspect deployment state instead of preparing over it'}
        Invoke-ReleaseOperation 'prepare'
        Invoke-CheckedProcess 'offline-dependencies' $python @('-B','-m','pip','install','--no-index','--only-binary=:all:','--find-links',(Join-Path $Stage 'wheels'),'-r',(Join-Path $Stage 'source\requirements-deploy.txt'))
        Invoke-CheckedProcess 'deployment-unit-tests' $python @('-B','-m','unittest','discover','-s','ops/windows','-p','test_release_files.py') (Join-Path $Stage 'source')
        Invoke-CheckedProcess 'django-regressions' $python @('-B','manage.py','test','promotions','api','test_production_serving','--settings=mysite.test_settings','--exclude-tag=integration','--noinput','--verbosity=1') $sourceSite
        Invoke-ReleaseOperation 'mark-prepared'
        Write-Output 'PREPARATION_ACCEPTED SOURCE_HASHES=OK OFFLINE_TESTS=OK LIVE_SERVICE_UNCHANGED'
        exit 0
    }

    Invoke-ReleaseOperation 'require-prepared'
    $task=Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath
    if($task.Actions.Count -ne 1 -or $task.Actions[0].Execute -ine $python -or $task.Actions[0].WorkingDirectory -ine $site){throw 'Django task action differs from the inspected deployment'}
    $taskSid=if($task.Principal.UserId -match '^S-1-'){[string]$task.Principal.UserId}else{([Security.Principal.NTAccount]::new($task.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier]).Value}
    if($taskSid -ne $currentSid.Value){throw 'Django task must run under the same inspected Windows account'}
    $owner=Get-PortOwner
    if(!$owner -or (Get-HttpResult '/accounts/login/').Status -ne 200){throw 'Current Django service is not healthy'}
    Assert-DjangoOwner $owner | Out-Null
    $oldTaskXml=Read-TaskXml
    $cutoverStarted=$true
    Stop-DjangoSafely
    Invoke-ReleaseOperation 'backup'
    [IO.File]::WriteAllText((Join-Path $backup 'django-task.xml'),$oldTaskXml,[Text.UTF8Encoding]::new($false))
    $backupComplete=$true
    $sourceMutationStarted=$true
    Invoke-ReleaseOperation 'apply'
    Invoke-ReleaseOperation 'config'
    Invoke-CheckedProcess 'production-check' $python @('-B','serve.py','--check') $site
    $action=New-ScheduledTaskAction -Execute $python -Argument 'serve.py' -WorkingDirectory $site
    Set-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Action $action | Out-Null
    Assert-PreservedTaskConfiguration $oldTaskXml
    Start-ScheduledTask -TaskName $taskName -TaskPath $taskPath
    $newOwner=Wait-Django $true
    Invoke-ReleaseOperation 'verify-after'
    [IO.File]::WriteAllText((Join-Path $Stage 'accepted.txt'),"Accepted $([DateTime]::UtcNow.ToString('o')) PID=$newOwner",[Text.UTF8Encoding]::new($false))
    Write-Output "DEPLOYMENT_ACCEPTED HTTP=200 STATIC=200 API_UNAUTHENTICATED=401 PID=$newOwner"
} catch {
    $message=$_.Exception.Message
    Write-Output ('DEPLOYMENT_FAILED '+$message)
    if($Mode -eq 'deploy' -and $cutoverStarted){
        try {
            if($sourceMutationStarted -and $backupComplete){Recover-PreviousRelease}
            else {
                # Preparation/backup failed before any code or task mutation.
                if($oldTaskXml){Restore-TaskAction $oldTaskXml}
                Start-ScheduledTask -TaskName $taskName -TaskPath $taskPath
                $oldOwner=Wait-Django $false
                Write-Output "ORIGINAL_SERVICE_RESTARTED HTTP=200 PID=$oldOwner"
            }
        } catch {
            Write-Output ('ROLLBACK_REQUIRES_ATTENTION '+$_.Exception.Message)
            exit 2
        }
    }
    exit 1
}
