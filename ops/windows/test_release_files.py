"""Exercise deployment and rollback entirely in synthetic temporary directories."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

import release_files as deploy


class ReleaseFileTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.run(
            ['git', '-c', 'user.name=Synthetic Test', '-c', 'user.email=test@example.invalid', '-C', str(root), *args],
            check=True, capture_output=True,
        ).stdout.decode().strip()

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / 'app'
        self.stage = self.root / deploy.RELEASE_ID
        self.candidate = self.root / 'candidate'
        self.config = self.root / 'private' / 'production.json'
        self.app.mkdir()
        self.stage.mkdir()
        (self.app / 'mysite').mkdir()
        self.git(self.app, 'init', '-b', 'main')
        (self.app / 'main.py').write_text('old source\n')
        (self.app / 'local.txt').write_text('old local file\n')
        (self.app / 'obsolete.py').write_text('old unused file\n')
        (self.app / 'runtime.txt').write_text('synthetic runtime bytes\n')
        with sqlite3.connect(self.app / 'mysite' / 'db.sqlite3') as db:
            db.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, value TEXT)')
            db.execute("INSERT INTO events(value) VALUES ('synthetic initial row')")
        self.git(self.app, 'add', '.')
        self.git(self.app, 'commit', '-qm', 'Synthetic baseline')
        self.before_head = self.git(self.app, 'rev-parse', 'HEAD')
        self.constant = patch.object(deploy, 'EXPECTED_HEAD', self.before_head)
        self.constant.start()
        self.addCleanup(self.constant.stop)
        subprocess.run(['git', 'clone', '--quiet', str(self.app), str(self.candidate)], check=True, capture_output=True)
        # A staged and an unstaged server modification must survive rollback.
        (self.app / 'main.py').write_text('existing server tweak\n')
        (self.app / 'local.txt').write_text('existing staged change\n')
        self.git(self.app, 'add', 'local.txt')
        self.before_status = self.git(self.app, 'status', '--porcelain')
        self.git(self.candidate, 'switch', '-c', 'release-20261111')
        (self.candidate / 'main.py').write_text('existing server tweak\nnew fix\n')
        (self.candidate / 'local.txt').write_text('existing staged change\n')
        (self.candidate / 'new.py').write_text('new functionality\n')
        (self.candidate / 'requirements-deploy.txt').write_text('waitress==3.0.2\nwhitenoise==6.12.0\n')
        (self.candidate / '.gitignore').write_text('/runtime.txt\n/mysite/db.sqlite3\n')
        self.git(self.candidate, 'rm', '--cached', 'runtime.txt', 'mysite/db.sqlite3')
        self.git(self.candidate, 'rm', 'obsolete.py')
        self.git(self.candidate, 'add', '.')
        self.git(self.candidate, 'commit', '-qm', 'Synthetic release')
        self.commit = self.git(self.candidate, 'rev-parse', 'HEAD')
        self.git(self.candidate, 'bundle', 'create', str(self.stage / 'git.bundle'), deploy.BUNDLE_REF)
        files = self.git(self.candidate, 'ls-files').splitlines()
        with zipfile.ZipFile(self.stage / 'source.zip', 'w') as archive:
            for name in files:
                archive.write(self.candidate / name, name)
        for wheel in deploy.WHEELS:
            path = self.stage / wheel
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'synthetic wheel; never installed')
        self.manifest = {
            'schema': 1, 'release_id': deploy.RELEASE_ID, 'expected_head': self.before_head,
            'commit': self.commit, 'bundle_ref': deploy.BUNDLE_REF,
            'files': [{'path': name, 'before': deploy.existing_hash(self.app / name), 'after': deploy.digest(self.candidate / name)} for name in files],
            'delete_paths': [{'path': 'obsolete.py', 'before': deploy.digest(self.app / 'obsolete.py')}],
            'preserve_untracked_deleted_paths': ['runtime.txt', 'mysite/db.sqlite3'],
            'payloads': {name: deploy.digest(self.stage / name) for name in deploy.PAYLOADS},
        }
        self.save_manifest()
        self.config.parent.mkdir()
        self.config.write_text(json.dumps({'SECRET_KEY': 'synthetic old key', 'CORS_ALLOWED_ORIGINS': ['https://existing.invalid']}))
        self.old_config = self.config.read_bytes()
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def save_manifest(self):
        (self.stage / 'manifest.json').write_text(json.dumps(self.manifest))

    def release(self):
        return deploy.Release(self.stage, self.app, self.config)

    def test_full_update_and_rollback_preserve_local_modifications_and_new_database_rows(self):
        release = self.release()
        release.prepare()
        release.mark_prepared()
        release.backup_cutover()
        release.apply_source()
        release.write_config()
        self.assertEqual(self.git(self.app, 'rev-parse', 'HEAD'), self.commit)
        self.assertEqual((self.app / 'runtime.txt').read_text(), 'synthetic runtime bytes\n')
        self.assertFalse((self.app / 'obsolete.py').exists())
        config = json.loads(self.config.read_text())
        self.assertGreaterEqual(len(config['SECRET_KEY']), 32)
        self.assertIn('https://existing.invalid', config['CORS_ALLOWED_ORIGINS'])
        self.assertIn('capacitor://localhost', config['CORS_ALLOWED_ORIGINS'])
        self.assertNotIn('*', config['ALLOWED_HOSTS'])
        with sqlite3.connect(self.app / 'mysite' / 'db.sqlite3') as db:
            db.execute("INSERT INTO events(value) VALUES ('synthetic row accepted after cutover')")
        release.verify_after()
        release.restore()
        self.assertEqual(self.git(self.app, 'rev-parse', 'HEAD'), self.before_head)
        restored_status = self.git(self.app, 'status', '--porcelain')
        source_status = '\n'.join(line for line in restored_status.splitlines() if not line.endswith('mysite/db.sqlite3'))
        self.assertEqual(source_status, self.before_status)
        self.assertIn(' M mysite/db.sqlite3', restored_status)  # intentionally preserved new records
        self.assertEqual((self.app / 'main.py').read_text(), 'existing server tweak\n')
        self.assertEqual((self.app / 'local.txt').read_text(), 'existing staged change\n')
        self.assertTrue((self.app / 'obsolete.py').exists())
        self.assertFalse((self.app / 'new.py').exists())
        self.assertEqual(self.config.read_bytes(), self.old_config)
        with sqlite3.connect(self.app / 'mysite' / 'db.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 2)

    def test_partial_source_write_failure_can_restore_staged_and_unstaged_changes(self):
        release = self.release()
        release.prepare()
        release.mark_prepared()
        release.backup_cutover()
        actual_copy = deploy.copy_atomic
        calls = 0
        def fail_second_copy(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('synthetic disk write failure')
            return actual_copy(source, destination)
        with patch.object(deploy, 'copy_atomic', side_effect=fail_second_copy):
            with self.assertRaises(OSError):
                release.apply_source()
        release.restore()
        self.assertEqual(self.git(self.app, 'status', '--porcelain'), self.before_status)
        self.assertEqual((self.app / 'main.py').read_text(), 'existing server tweak\n')
        self.assertFalse((self.app / '.gitignore').exists())

    def test_rollback_removes_new_config_when_no_original_config_existed(self):
        self.config.unlink()
        release = self.release()
        release.prepare()
        release.mark_prepared()
        release.backup_cutover()
        release.apply_source()
        release.write_config()
        self.assertTrue(self.config.exists())
        release.restore()
        self.assertFalse(self.config.exists())
        with sqlite3.connect(self.app / 'mysite' / 'db.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)

    def test_baseline_drift_blocks_preparation(self):
        (self.app / 'main.py').write_text('another manual edit\n')
        with self.assertRaises(deploy.ReleaseError):
            self.release().prepare()
        self.assertFalse((self.stage / 'source').exists())
        self.assertEqual(self.git(self.app, 'rev-parse', 'HEAD'), self.before_head)

    def test_before_null_rejects_an_unexpected_existing_file(self):
        (self.app / 'new.py').write_text('untracked user work\n')
        with self.assertRaises(deploy.ReleaseError):
            self.release().verify_before()

    def test_payload_hash_mismatch_blocks_extraction(self):
        with (self.stage / 'source.zip').open('ab') as file:
            file.write(b'tamper')
        with self.assertRaises(deploy.ReleaseError):
            self.release().prepare()
        self.assertFalse((self.stage / 'source').exists())

    def test_zip_entries_must_exactly_match_manifest(self):
        with zipfile.ZipFile(self.stage / 'source.zip', 'a') as archive:
            archive.writestr('../outside.py', 'unsafe')
        self.manifest['payloads']['source.zip'] = deploy.digest(self.stage / 'source.zip')
        self.save_manifest()
        with self.assertRaises(deploy.ReleaseError):
            self.release().prepare()
        self.assertFalse((self.root / 'outside.py').exists())

    def test_preserved_path_cannot_be_mutated(self):
        self.manifest['preserve_untracked_deleted_paths'].append('main.py')
        self.save_manifest()
        with self.assertRaises(deploy.ReleaseError):
            self.release()

    def test_recovery_tamper_is_rejected(self):
        release = self.release()
        release.prepare()
        release.mark_prepared()
        release.backup_cutover()
        (release.backup / 'files' / 'main.py').write_text('tampered')
        with self.assertRaises(deploy.ReleaseError):
            release.restore()
        self.assertEqual(self.git(self.app, 'rev-parse', 'HEAD'), self.before_head)

    def test_windows_unsafe_paths_are_rejected(self):
        for value in ('../x', '/x', 'C:/x', 'a\\x', '.GIT/config', 'x/NUL.txt', 'x/name.', 'x/a:stream', 'x/a\n.txt', 'x/SECRET~1.TXT'):
            with self.subTest(path=value), self.assertRaises(deploy.ReleaseError):
                deploy.relative_path(value)


if __name__ == '__main__':
    unittest.main()
