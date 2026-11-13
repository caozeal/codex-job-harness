import json
import os
from pathlib import Path
import shlex
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_jobs import store


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'repo with space'
        self.repo.mkdir()
        (self.repo / '.git').mkdir()
        self.jobs = self.base / 'jobs'
        self.jobs.mkdir()
        (self.jobs / 'fake-config.json').write_text('{"mode":"success"}')
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        ssh = self.bin / 'ssh'
        ssh.write_text('#!/usr/bin/env python3\nimport os, sys\nfrom pathlib import Path\nPath(os.environ["CODEX_JOB_DIR"], "remote-command").write_text(sys.argv[-1])\nos.execv("/bin/sh", ["/bin/sh", "-c", sys.argv[-1]])\n')
        ssh.chmod(0o755)
        self.fake = Path(__file__).resolve().parent / 'fake_codex.py'
        self.hosts = self.base / 'hosts.json'
        self.config = {'ssh_host': 'fake', 'codex_bin': str(self.fake), 'allowed_root': str(self.base)}
        self.write_config()
        env = {'PATH': str(self.bin) + os.pathsep + os.environ.get('PATH', '/usr/bin:/bin'),
               'CODEX_JOB_DIR': str(self.jobs), 'CODEX_HOSTS_FILE': str(self.hosts),
               'CODEX_ALLOW_EDITS': 'true'}
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def write_config(self):
        self.hosts.write_text(json.dumps({'remote': self.config}))

    def wait(self, job):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = store.read(job)
            if status['status'] in store.TERMINAL:
                return status
            time.sleep(.02)
        self.fail('job did not finish: ' + repr(store.read(job)))

    def test_success_resume_and_injection(self):
        marker = self.base / 'injected'
        task = "hello; touch " + str(marker) + " $(touch " + str(marker) + ") 'quoted'"
        job = store.submit(task, str(self.repo), host='remote')['job_id']
        status = self.wait(job)
        self.assertEqual(status['status'], 'succeeded', status)
        self.assertEqual((status['thread_id'], status['final_response'], status['usage']['input_tokens']), ('thread-123', 'done', 42))
        command = (self.jobs / 'remote-command').read_text()
        self.assertIn('cd ' + shlex.quote(str(self.repo)), command)
        self.assertIn('test -d .git', command)
        self.assertIn('-s read-only', command)
        self.assertIn('model_reasoning_effort=low', command)
        self.assertIn('export PATH=' + shlex.quote(str(self.fake.parent)) + ':$PATH', command)
        self.assertIn('approval_policy="never"', shlex.split(command))
        self.assertEqual(shlex.split(command)[-1], task)
        self.assertFalse(marker.exists())
        self.assertEqual(store.pending_jobs()[0]['host'], 'remote')
        resumed = store.continue_job(job, 'follow up')['job_id']
        status = self.wait(resumed)
        self.assertEqual(status['status'], 'succeeded', status)
        self.assertEqual(status['host'], 'remote')
        self.assertEqual(store.read_json(store.job_path(resumed) / 'request.json')['host'], 'remote')
        command = (self.jobs / 'remote-command').read_text()
        self.assertIn('exec resume thread-123', command)
        tokens = shlex.split(command)
        self.assertIn('sandbox_mode="read-only"', tokens)
        self.assertIn('approval_policy="never"', tokens)
        self.assertNotIn('-C', tokens)
        self.assertNotIn('-s', tokens)

    def test_failure(self):
        (self.jobs / 'fake-config.json').write_text('{"mode":"failure"}')
        job = store.submit('task', str(self.repo), host='remote')['job_id']
        status = self.wait(job)
        self.assertEqual(status['status'], 'failed')
        self.assertIn('failure stderr', status['error'])

    def test_validation(self):
        self.assertIn('CODEX_HOSTS_FILE', store.ENV_KEYS)
        for host in ('unknown', '', '../bad'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                store.submit('x', str(self.repo), host=host)
        for key, value in [('codex_bin', 'relative'), ('allowed_root', 'relative'), ('ssh_host', '-bad'),
                           ('connect_timeout_sec', True), ('connect_timeout_sec', 61), ('allow_edits', 'true')]:
            with self.subTest(key=key, value=value):
                old = self.config[key] if key in self.config else None
                self.config[key] = value
                self.write_config()
                with self.assertRaises(ValueError):
                    store.submit('x', str(self.repo), host='remote')
                if old is None:
                    del self.config[key]
                else:
                    self.config[key] = old
                self.write_config()
        for repo in (str(self.base) + '-evil/repo', str(self.base / '../escape'), 'relative'):
            with self.subTest(repo=repo), self.assertRaises(ValueError):
                store.submit('x', repo, host='remote')
        with self.assertRaises(ValueError):
            store.submit('x', str(self.repo), 'edit', host='remote')
        self.config['allow_edits'] = True
        self.write_config()
        job = store.submit('x', str(self.repo), 'edit', host='remote')['job_id']
        self.assertEqual(self.wait(job)['status'], 'succeeded')
        self.assertIn('-s workspace-write', (self.jobs / 'remote-command').read_text())
        self.assertEqual(store.validate(str(self.base / 'not-present'), 'analyze', 'x', 'remote')[0], str(self.base / 'not-present'))
        self.hosts.write_text('{')
        with self.assertRaises(ValueError):
            store.submit('x', str(self.repo), host='remote')
