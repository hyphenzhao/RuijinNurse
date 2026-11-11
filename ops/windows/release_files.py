"""Offline, hash-verified file operations for the fixes-20261111 deployment.

This helper never starts/stops services or restores the database. The companion
PowerShell script owns cutover and rollback. It emits counts and states only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import socket
import sqlite3
import stat
import subprocess
import tempfile
import zipfile

RELEASE_ID = 'fixes-20261111'
APP = Path(r'D:\RuijinNurse_20260616_Update')
EXPECTED_HEAD = '5de01b95e1121087483b9a0bce204a2db64fdeab'
BUNDLE_REF = 'refs/heads/release-20261111'
WHEELS = {
    'wheels/waitress-3.0.2-py3-none-any.whl',
    'wheels/whitenoise-6.12.0-py3-none-any.whl',
}
PAYLOADS = {'source.zip', 'git.bundle'} | WHEELS
HASH_RE = re.compile(r'^[0-9a-f]{64}$')
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
RESERVED = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{p}{n}' for p in ('COM', 'LPT') for n in range(1, 10)}


class ReleaseError(RuntimeError):
    pass


def relative_path(value):
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value) or any(char in value for char in '\\:<>|?*'):
        raise ReleaseError('Invalid release path')
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(
        part.casefold() in {'.', '..', '.git'} or part.endswith((' ', '.'))
        or part.split('.')[0].upper() in RESERVED or re.search(r'~[0-9]', part) for part in path.parts
    ):
        raise ReleaseError('Unsafe release path')
    return path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def tree_hashes(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ReleaseError('Linked Git metadata is unsupported')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def existing_hash(path):
    path = Path(path)
    if path.is_symlink():
        raise ReleaseError('Release path is a symbolic link')
    if not path.exists():
        return None
    if not path.is_file():
        raise ReleaseError('Expected a regular release file')
    return digest(path)


def safe_destination(root, path):
    destination = Path(root).joinpath(*relative_path(path).parts)
    # Reject directory links/junctions that could redirect a deployment write.
    resolved_root = Path(root).resolve()
    try:
        destination.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise ReleaseError('Release path resolves outside its root') from exc
    for parent in destination.parents:
        if parent == Path(root):
            break
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ReleaseError('Linked release directory is not supported')
    return destination


def remove_tree(path):
    # Git object files may have the read-only attribute on Windows.
    def writable_retry(function, failing_path, error):
        os.chmod(failing_path, stat.S_IWRITE | stat.S_IREAD)
        function(failing_path)
    shutil.rmtree(path, onerror=writable_retry)


def copy_atomic(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.ruijin-', suffix='.new', dir=destination.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


class Release:
    def __init__(self, stage, app=APP, private_config=None):
        self.stage = Path(stage)
        self.app = Path(app)
        self.source = self.stage / 'source'
        self.backup = self.stage / 'backup-cutover'
        self.private_config = Path(private_config) if private_config else Path(os.environ['LOCALAPPDATA']) / 'RuijinNurse' / 'production.json'
        self.manifest_path = self.stage / 'manifest.json'
        self.manifest = json.loads(self.manifest_path.read_text(encoding='utf-8-sig'))
        self.validate_manifest()

    def validate_manifest(self):
        m = self.manifest
        if m.get('schema') != 1 or m.get('release_id') != RELEASE_ID:
            raise ReleaseError('Unexpected manifest schema or release')
        if m.get('expected_head') != EXPECTED_HEAD or m.get('bundle_ref') != BUNDLE_REF:
            raise ReleaseError('Unexpected baseline or bundle ref')
        if not SHA_RE.fullmatch(m.get('commit', '')):
            raise ReleaseError('Invalid target commit')
        if set(m.get('payloads', {})) != PAYLOADS:
            raise ReleaseError('Unexpected payload set')
        for value in m['payloads'].values():
            if not isinstance(value, str) or not HASH_RE.fullmatch(value):
                raise ReleaseError('Invalid payload digest')
        self.entries = m.get('files', [])
        self.deletions = m.get('delete_paths', [])
        self.preserved = m.get('preserve_untracked_deleted_paths', [])
        if not all(isinstance(group, list) for group in (self.entries, self.deletions, self.preserved)) or not self.entries:
            raise ReleaseError('Invalid or empty source manifest')
        seen = set()
        for index, entry in enumerate(self.entries + self.deletions):
            path = str(relative_path(entry.get('path')))
            if path.casefold() in seen:
                raise ReleaseError('Duplicate or case-colliding release path')
            seen.add(path.casefold())
            before = entry.get('before')
            if before is not None and (not isinstance(before, str) or not HASH_RE.fullmatch(before)):
                raise ReleaseError('Invalid baseline digest')
            if index < len(self.entries) and (not isinstance(entry.get('after'), str) or not HASH_RE.fullmatch(entry['after'])):
                raise ReleaseError('Invalid source digest')
            if path.casefold() == 'mysite/db.sqlite3' or 'runtime_sessions' in PurePosixPath(path.casefold()).parts:
                raise ReleaseError('Runtime data must not be deployed as source')
        for path in self.preserved:
            relative_path(path)
            if path.casefold() in seen:
                raise ReleaseError('Preserved runtime path overlaps a source mutation')
            seen.add(path.casefold())

    def git(self, *args):
        hooks = self.stage / 'empty-hooks'
        hooks.mkdir(exist_ok=True)
        env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
        result = subprocess.run(
            ['git', '-c', f'core.hooksPath={hooks}', '-c', 'core.fsmonitor=false', '-C', str(self.app), *args],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            timeout=180,
        )
        if result.returncode:
            raise ReleaseError(f'Git operation failed ({args[0]})')
        return result.stdout.decode('utf-8', errors='strict').strip()

    def verify_payloads(self):
        for relative, expected in self.manifest['payloads'].items():
            if existing_hash(safe_destination(self.stage, relative)) != expected:
                raise ReleaseError(f'Payload verification failed: {relative}')

    def verify_before(self):
        if not (self.app / '.git').is_dir() or (self.app / '.git' / 'index.lock').exists():
            raise ReleaseError('Git checkout is missing or currently locked')
        if self.git('rev-parse', 'HEAD') != EXPECTED_HEAD:
            raise ReleaseError('Server Git HEAD changed since inspection')
        for entry in self.entries + self.deletions:
            if existing_hash(safe_destination(self.app, entry['path'])) != entry.get('before'):
                raise ReleaseError(f'Baseline drift detected: {entry["path"]}')

    def verify_source(self):
        for entry in self.entries:
            if existing_hash(safe_destination(self.source, entry['path'])) != entry['after']:
                raise ReleaseError(f'Staged source verification failed: {entry["path"]}')
        requirements = self.source / 'requirements-deploy.txt'
        lines = {line.strip() for line in requirements.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')}
        if lines != {'waitress==3.0.2', 'whitenoise==6.12.0'}:
            raise ReleaseError('Unexpected offline deployment dependencies')

    def prepare(self):
        self.verify_payloads()
        self.verify_before()
        expected = {entry['path'] for entry in self.entries}
        temporary = self.stage / 'source-extracting'
        if temporary.exists():
            raise ReleaseError('An incomplete source extraction exists; inspect it before retrying')
        temporary.mkdir()
        try:
            with zipfile.ZipFile(self.stage / 'source.zip') as archive:
                names = [info.filename for info in archive.infolist()]
                if len(names) != len(set(names)) or set(names) != expected:
                    raise ReleaseError('Source archive does not exactly match the manifest')
                for info in archive.infolist():
                    relative_path(info.filename)
                    if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                        raise ReleaseError('Source archive contains a non-regular or encrypted member')
                    destination = safe_destination(temporary, info.filename)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, destination.open('wb') as target:
                        shutil.copyfileobj(source, target)
            for entry in self.entries:
                if digest(safe_destination(temporary, entry['path'])) != entry['after']:
                    raise ReleaseError('Source archive file digest mismatch')
            if self.source.exists():
                # Only a previously verified staging directory is replaceable.
                self.verify_source()
                remove_tree(self.source)
            temporary.rename(self.source)
        except Exception:
            # Preserve failed extraction privately for diagnosis; never touch app.
            raise
        self.verify_source()
        refs = subprocess.run(['git', 'bundle', 'list-heads', str(self.stage / 'git.bundle'), BUNDLE_REF],
                              check=True, capture_output=True, timeout=30).stdout.decode().split()
        if refs != [self.manifest['commit'], BUNDLE_REF]:
            raise ReleaseError('Git bundle does not contain the expected release ref')
        self.git('bundle', 'verify', str(self.stage / 'git.bundle'))
        print(f'PREPARED_SOURCE files={len(self.entries)}')

    def mark_prepared(self):
        self.verify_payloads()
        self.verify_source()
        self.verify_before()
        atomic_json(self.stage / 'prepared.json', {'manifest_sha256': digest(self.manifest_path), 'tests_passed': True})
        print('OFFLINE_TESTS_ACCEPTED')

    def require_prepared(self):
        self.verify_payloads()
        self.verify_source()
        receipt = json.loads((self.stage / 'prepared.json').read_text())
        if receipt != {'manifest_sha256': digest(self.manifest_path), 'tests_passed': True}:
            raise ReleaseError('Prepared release receipt does not match')
        self.verify_before()

    def backup_cutover(self):
        self.require_prepared()
        if self.backup.exists():
            raise ReleaseError('Cutover backup already exists; do not overwrite recovery material')
        self.backup.mkdir()
        affected = [entry for entry in self.entries if entry['before'] != entry['after']] + self.deletions
        stored = []
        for entry in affected:
            if entry['before'] is not None:
                target = safe_destination(self.backup / 'files', entry['path'])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(safe_destination(self.app, entry['path']), target)
                if digest(target) != entry['before']:
                    raise ReleaseError('Backup source digest mismatch')
            stored.append({'path': entry['path'], 'before': entry['before']})
        git_files = tree_hashes(self.app / '.git')
        shutil.copytree(self.app / '.git', self.backup / 'git')
        if tree_hashes(self.backup / 'git') != git_files:
            raise ReleaseError('Git metadata backup verification failed')
        source_db = self.app / 'mysite' / 'db.sqlite3'
        with sqlite3.connect(source_db.resolve().as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(self.backup / 'db.sqlite3') as target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ReleaseError('Database backup integrity check failed')
        config_exists = self.private_config.exists()
        if config_exists:
            shutil.copy2(self.private_config, self.backup / 'production.json')
        # Runtime files remain in place when reset --mixed untracks them.
        preserved = {path: existing_hash(safe_destination(self.app, path)) for path in self.preserved}
        atomic_json(self.backup / 'state.json', {
            'manifest_sha256': digest(self.manifest_path), 'files': stored,
            'config_existed': config_exists, 'expected_head': EXPECTED_HEAD,
            'preserved': preserved, 'database_sha256': digest(self.backup / 'db.sqlite3'),
            'git_files': git_files,
            'config_sha256': digest(self.backup / 'production.json') if config_exists else None,
        })
        print(f'CUTOVER_BACKUP_VERIFIED files={len(stored)} database=ok')

    def backup_state(self):
        state = json.loads((self.backup / 'state.json').read_text())
        if state['manifest_sha256'] != digest(self.manifest_path):
            raise ReleaseError('Backup does not belong to this manifest')
        for entry in state['files']:
            if entry['before'] is not None and existing_hash(safe_destination(self.backup / 'files', entry['path'])) != entry['before']:
                raise ReleaseError('Recovery file digest mismatch')
        if tree_hashes(self.backup / 'git') != state['git_files']:
            raise ReleaseError('Recovery Git metadata digest mismatch')
        if existing_hash(self.backup / 'production.json') != state['config_sha256']:
            raise ReleaseError('Recovery configuration digest mismatch')
        if existing_hash(self.backup / 'db.sqlite3') != state['database_sha256']:
            raise ReleaseError('Recovery database digest mismatch')
        return state

    def apply_source(self):
        self.backup_state()
        self.require_prepared()
        for entry in self.entries:
            if entry['before'] != entry['after']:
                copy_atomic(safe_destination(self.source, entry['path']), safe_destination(self.app, entry['path']))
        for entry in self.deletions:
            destination = safe_destination(self.app, entry['path'])
            if destination.exists():
                destination.unlink()
        self.git('fetch', '--no-tags', str(self.stage / 'git.bundle'), BUNDLE_REF + ':refs/ruijin/releases/' + RELEASE_ID)
        if self.git('rev-parse', 'refs/ruijin/releases/' + RELEASE_ID) != self.manifest['commit']:
            raise ReleaseError('Fetched release commit mismatch')
        # Update HEAD and index only. Never clean/reset-hard runtime or patient files.
        self.git('reset', '--mixed', self.manifest['commit'])
        self.verify_after(preserve=True)
        print('SOURCE_AND_GIT_UPDATED')

    def write_config(self):
        self.backup_state()
        value = json.loads(self.private_config.read_text(encoding='utf-8-sig')) if self.private_config.exists() else {}
        if not isinstance(value, dict):
            raise ReleaseError('Private configuration must be a JSON object')
        value['SECRET_KEY'] = secrets.token_urlsafe(50)
        hosts = ['10.8.0.10', '10.8.0.14', 'localhost', '127.0.0.1', '[::1]', socket.gethostname()]
        value['ALLOWED_HOSTS'] = list(dict.fromkeys(hosts))
        origins = value.get('CORS_ALLOWED_ORIGINS', [])
        if not isinstance(origins, list) or not all(isinstance(origin, str) for origin in origins):
            raise ReleaseError('CORS origins must be a list')
        value['CORS_ALLOWED_ORIGINS'] = list(dict.fromkeys(origins + ['capacitor://localhost', 'ionic://localhost', 'http://localhost']))
        atomic_json(self.private_config, value)
        print('PRIVATE_CONFIGURATION_WRITTEN')

    def verify_after(self, preserve=False):
        if self.git('rev-parse', 'HEAD') != self.manifest['commit']:
            raise ReleaseError('Deployed commit mismatch')
        for entry in self.entries:
            if existing_hash(safe_destination(self.app, entry['path'])) != entry['after']:
                raise ReleaseError(f'Deployed source verification failed: {entry["path"]}')
        for entry in self.deletions:
            if safe_destination(self.app, entry['path']).exists():
                raise ReleaseError('Deleted source path still exists')
        # A pure index reset must preserve all runtime data byte-for-byte before restart.
        state_path = self.backup / 'state.json'
        if preserve and state_path.exists():
            state = json.loads(state_path.read_text())
            for path, expected in state['preserved'].items():
                if existing_hash(safe_destination(self.app, path)) != expected:
                    raise ReleaseError('Preserved runtime file changed during deployment')
        self.git('diff', '--quiet')
        self.git('diff', '--cached', '--quiet')
        print(f'VERIFIED_DEPLOYMENT commit={self.manifest["commit"]} files={len(self.entries)}')

    def restore(self):
        state = self.backup_state()
        evidence = self.stage / 'rollback-evidence'
        evidence.mkdir(exist_ok=True)
        for entry in state['files']:
            target = safe_destination(self.app, entry['path'])
            if target.exists():
                saved = safe_destination(evidence / 'files', entry['path'])
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            if entry['before'] is None:
                if target.exists():
                    target.unlink()
            else:
                copy_atomic(safe_destination(self.backup / 'files', entry['path']), target)
        current_git = self.app / '.git'
        if not (evidence / 'git').exists():
            shutil.copytree(current_git, evidence / 'git')
        remove_tree(current_git)
        shutil.copytree(self.backup / 'git', current_git)
        if self.private_config.exists():
            shutil.copy2(self.private_config, evidence / 'production.json')
        if state['config_existed']:
            copy_atomic(self.backup / 'production.json', self.private_config)
        elif self.private_config.exists():
            self.private_config.unlink()
        self.verify_before()
        # Never restore db.sqlite3 automatically: new accepted clinical records survive.
        print('ORIGINAL_CODE_GIT_CONFIG_RESTORED database_untouched=true')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True)
    parser.add_argument('operation', choices=['prepare', 'mark-prepared', 'require-prepared', 'backup', 'apply', 'config', 'verify-after', 'restore'])
    args = parser.parse_args()
    expected_stage = Path(os.environ['LOCALAPPDATA']) / 'RuijinNurse' / 'releases' / RELEASE_ID
    if os.name != 'nt' or Path(args.stage).resolve() != expected_stage.resolve():
        raise ReleaseError('This release must run from its fixed Windows staging directory')
    release = Release(args.stage)
    operation = {
        'prepare': release.prepare, 'mark-prepared': release.mark_prepared,
        'require-prepared': release.require_prepared, 'backup': release.backup_cutover,
        'apply': release.apply_source, 'config': release.write_config,
        'verify-after': release.verify_after, 'restore': release.restore,
    }[args.operation]
    operation()


if __name__ == '__main__':
    main()
