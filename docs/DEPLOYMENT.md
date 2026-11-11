# Windows 部署与版本恢复

应用目录与 Python 环境分别位于 `D:\RuijinNurse_20260616_Update` 和
`D:\RuijinNurse\venv`。发布不得合并或替换这两个目录。

## 运行方式

Windows 任务 `RuijinNurse-Django` 使用既有虚拟环境执行 `mysite/serve.py`。
该入口使用 Waitress 单进程、16 个工作线程，并以文件锁防止重复启动。
端口绑定成功后，启动程序会将上次进程遗留的计时标为中断，再开始接受请求。
独立的 Windows Ollama 与 WSL Ollama 开机任务继续保留。

`mysite.production_settings` 强制关闭 DEBUG，限制主机名，使用 WhiteNoise 提供
静态文件、Unity 隔离响应头和范围请求。为兼容历史上运行时生成的音频，静态查找
保留刷新；旧回答文本、配置和可执行文件不能经静态 URL 读取。

生产密钥与站点设置保存在仓库外的
`%LOCALAPPDATA%\RuijinNurse\production.json`，或者由 `RUIJIN_CONFIG_FILE` 指定。
其内容包含 `SECRET_KEY`、`ALLOWED_HOSTS` 和 `CORS_ALLOWED_ORIGINS`，仅允许运行账号、
SYSTEM 和管理员访问。不要提交此文件。更换密钥会要求网页与 App 重新登录。
当前内网 HTTP 接入方式不等同于 HTTPS 部署。

`WAITRESS_HOST`、`WAITRESS_PORT`、`WAITRESS_THREADS` 可调整监听设置。
`OLLAMA_READ_TIMEOUT_SECONDS` 默认为 900 秒，连接超时为 10 秒。
`python serve.py --check` 只做配置检查，不占用端口、不修改计时。

## 保留的运行数据

数据库、`runtime_sessions`、媒体文件，以及静态目录里原有的 `role.txt`、
`knowledge.txt`、`knowledge_updated.txt`、`single_model_config.json` 和 `input.txt`
留在服务器，不随公开代码发布。角色和知识文件不存在时，程序读取仓库中的
`.example.txt`；现场文件存在但为空时，尊重其空值。

旧版本可能混合多个患者的历史文件完整保留。新问答仅使用按患者和模型隔离的
新历史，不自动导入旧混合上下文。

## 2026-11-11 修复

- 隔离不同患者的问答上下文，严格验证 App 会话所有权。
- 中断、失败、空回答及取消均有明确状态，不加入后续模型上下文。
- 每次真实提问分别记录，取消按问题文本进行的 90 秒去重。
- 统一页面筛选与 AI 分析口径；患者人数按患者去重。
- Web 与 App 共用问答计时。客户端未上报的数据保持缺失，不以后台耗时代替。
- App 断线后由用户明确点击重试；停止只操作明确的请求编号。
- 本地加载 Markdown 渲染库；失败时安全降级文本并继续上报计时。

现有 App 在服务端升级后即可获得后台计时。新增客户端显示耗时与重试逻辑需要
在 macOS/Xcode 编译、签名并安装新版 iOS App；Ubuntu 检查不等于 iOS 构建验证。

## 验证

在项目 `mysite` 目录，使用安装了项目依赖及 `requirements-deploy.txt` 的 Python：

```sh
DJANGO_SETTINGS_MODULE=mysite.test_settings python manage.py test --exclude-tag integration --noinput
DJANGO_SETTINGS_MODULE=mysite.test_settings python manage.py makemigrations --check --dry-run
python promotions/tests/browser/chat_ui.py --production
```

浏览器测试需要 Node.js、Playwright 和 Chromium，可通过 `NODE_BINARY`、
`PLAYWRIGHT_MODULE`、`CHROMIUM_EXECUTABLE` 指定位置。测试使用临时合成数据和本机模拟
模型服务。标记 `integration` 的 Whisper/Piper 测试要求真实语音服务，独立执行。

## 离线发布和回退

先在 Ubuntu 合并、测试，再生成带提交编号和逐文件 SHA-256 的版本包及 Git bundle。
通过 SSH/SFTP 上传，服务器无需访问 GitHub。Unity 的 `.data`、`.framework.js`
和 `.wasm` 必须来自同一构建，不能只更新其中一个。

`ops/windows` 脚本分准备和切换阶段：先核对旧版本和文件校验值、离线准备依赖、
使用临时测试数据库验证；通过后才停止 Django，备份切换时数据库、受影响文件、
Git 元数据、配置和任务设置，再应用更新并检查 HTTP 与静态服务。失败时恢复原代码、
Git 状态和任务后启动原服务。没有数据库结构变化时不自动覆盖生产数据库，避免
丢弃切换后已接收的数据；一致性备份仍保留用于人工恢复。

GitHub 主分支只在候选版本通过服务器检查后推进，最后比较服务器提交编号及全部
发布文件的校验值。数据库和现场配置不属于代码一致性检查范围。
