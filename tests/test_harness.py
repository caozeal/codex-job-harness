import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from codex_jobs import store, watcher

FAKE = Path(__file__).resolve().parent / 'fake_codex.py'


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        (self.repo / '.git').mkdir()
        self.jobs = self.base / 'jobs'
        self.jobs.mkdir()
        self.env = patch.dict(os.environ, {'CODEX_JOB_DIR': str(self.jobs), 'CODEX_ALLOWED_ROOT': str(self.base),
                                           'CODEX_BIN': str(FAKE), 'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'CODEX_MAX_ATTEMPTS': '2',
                                           'CODEX_JOB_TIMEOUT_SEC': '1800'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config('success')

    def config(self, mode, **kwargs):
        (self.jobs / 'fake-config.json').write_text(json.dumps(dict(mode=mode, **kwargs)))

    def wait(self, job_id, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = store.read(job_id)
            if predicate(result):
                return result
            time.sleep(.02)
        self.fail('job did not reach expected state: ' + repr(store.read(job_id)))

    def submit(self):
        return store.submit('test task', str(self.repo))['job_id']

    def test_validation(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            store.submit('x', str(self.base.parent))
        with self.assertRaisesRegex(ValueError, 'containing .git'):
            store.submit('x', str(self.base))
        with patch.dict(os.environ, {'CODEX_ALLOWED_ROOT': ''}):
            with self.assertRaisesRegex(ValueError, 'required'):
                store.submit('x', str(self.repo))
        with self.assertRaisesRegex(ValueError, 'CODEX_ALLOW_EDITS'):
            store.submit('x', str(self.repo), 'edit')
        with self.assertRaises(ValueError):
            store.submit(' ' * 2, str(self.repo))
        with self.assertRaises(ValueError):
            store.submit('x' * 12001, str(self.repo))
        with patch.dict(os.environ, {'CODEX_ALLOW_EDITS': 'true'}):
            job = store.submit('x', str(self.repo), 'edit')['job_id']
            self.wait(job, lambda s: s['status'] == 'succeeded')
        for invalid in ('../x', 'A' * 32, 'x' * 32, 'a' * 31):
            with self.assertRaises(ValueError):
                store.read(invalid)

    def test_success_and_continue(self):
        job = self.submit()
        status = self.wait(job, lambda s: s['status'] == 'succeeded')
        self.assertEqual((status['thread_id'], status['final_response'], status['usage']['input_tokens']), ('thread-123', 'done', 42))
        resumed = store.continue_job(job, 'follow up')['job_id']
        self.wait(resumed, lambda s: s['status'] == 'succeeded')
        argv = json.loads((store.job_path(resumed) / 'argv.json').read_text())
        self.assertEqual(argv[:3], ['exec', 'resume', 'thread-123'])
        self.assertIn('sandbox_mode="read-only"', argv)
        self.assertIn('approval_policy="never"', argv)
        self.assertNotIn('-C', argv)
        self.assertIn('hook chatter', (store.job_path(job) / 'worker.log').read_text())
        self.assertEqual(store.job_path(job).stat().st_mode & 0o777, 0o700)
        for name in ('request.json', 'status.json', 'events.jsonl', 'worker.log', 'pid'):
            self.assertEqual((store.job_path(job) / name).stat().st_mode & 0o777, 0o600)

    def test_failure(self):
        self.config('failure')
        job = self.submit()
        status = self.wait(job, lambda s: s['status'] == 'failed')
        self.assertIn('failure stderr', status['error'])
        with self.assertRaises(ValueError):
            store.continue_job(job, 'x')

    def test_retry(self):
        self.config('retry')
        job = self.submit()
        self.wait(job, lambda s: s['status'] == 'running')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with contextlib.redirect_stdout(io.StringIO()):
                watcher.sweep()
            status = store.read(job)
            if status['status'] == 'succeeded':
                break
            time.sleep(.05)
        self.assertEqual(status['status'], 'succeeded')
        self.assertEqual(status['attempts'], 2)
        self.assertIn('retried', (store.job_path(job) / 'events.jsonl').read_text())

    def test_timeout_and_notification_once(self):
        self.config('hang', seconds=30)
        job = self.submit()
        self.wait(job, lambda s: s['status'] == 'running')
        with patch.dict(os.environ, {'CODEX_JOB_TIMEOUT_SEC': '0'}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                watcher.sweep()
                watcher.sweep()
        self.assertEqual(store.read(job)['status'], 'timeout')
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(out.getvalue())['status'], 'timeout')

    def test_atomic_reads(self):
        job = self.submit()
        self.wait(job, lambda s: s['status'] == 'succeeded')
        path = store.job_path(job) / 'status.json'
        errors = []
        def writer():
            for n in range(400):
                store.write_json(path, {'n': n, 'payload': 'x' * 8000})
        thread = threading.Thread(target=writer)
        thread.start()
        while thread.is_alive():
            try:
                store.read_json(path)
            except Exception as exc:
                errors.append(exc)
        thread.join()
        self.assertEqual(errors, [])

    def command(self, *args):
        return subprocess.run([os.sys.executable, '-m', 'codex_jobs', *args],
                              capture_output=True, text=True, timeout=10)

    def test_wait_terminal_and_failure(self):
        job = self.submit()
        self.wait(job, lambda s: s['status'] == 'succeeded')
        result = self.command('wait', job)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['final_response'], 'done')
        self.config('failure')
        failed = self.submit()
        self.wait(failed, lambda s: s['status'] == 'failed')
        result = self.command('wait', job, failed)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stdout.splitlines()), 2)

    def test_wait_running_and_timeout(self):
        self.config('hang', seconds=0.7)
        job = self.submit()
        self.wait(job, lambda s: s['status'] == 'running')
        started = time.monotonic()
        result = self.command('wait', job, '--timeout', '5')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(time.monotonic() - started, 0.2)
        self.config('hang', seconds=30)
        job = self.submit()
        self.wait(job, lambda s: s['status'] == 'running')
        try:
            result = self.command('wait', job, '--timeout', '1')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['status'], 'running')
        finally:
            with patch.dict(os.environ, {'CODEX_JOB_TIMEOUT_SEC': '0'}):
                with contextlib.redirect_stdout(io.StringIO()):
                    watcher.sweep()

    def test_pending_and_independent_notification(self):
        job = self.submit()
        self.wait(job, lambda s: s['status'] == 'succeeded')
        self.config('hang', seconds=30)
        running = self.submit()
        self.wait(running, lambda s: s['status'] == 'running')
        try:
            path = store.job_path(job) / 'status.json'
            old = store.read(job)
            del old['agent_notified']
            store.write_json(path, old)
            result = self.command('pending', '--peek', '--json')
            self.assertEqual(result.returncode, 0)
            self.assertEqual([item['job_id'] for item in json.loads(result.stdout)], [job])
            self.assertNotIn('agent_notified', store.read(job))
            with contextlib.redirect_stdout(io.StringIO()):
                watcher.sweep()
            self.assertTrue(store.read(job)['notified'])
            self.assertFalse(store.read(job).get('agent_notified', False))
            result = self.command('pending', '--mark')
            self.assertIn(job, result.stdout)
            self.assertTrue(store.read(job)['agent_notified'])
            self.assertTrue(store.read(job)['notified'])
            self.assertEqual(self.command('pending', '--peek').stdout, '')
            before = path.read_bytes()
            self.assertEqual(self.command('pending', '--mark', '--json').stdout, '')
            self.assertEqual(path.read_bytes(), before)
        finally:
            with patch.dict(os.environ, {'CODEX_JOB_TIMEOUT_SEC': '0'}):
                with contextlib.redirect_stdout(io.StringIO()):
                    watcher.sweep()

    def test_plugin_registration(self):
        path = Path(__file__).resolve().parent.parent / 'plugin/codex-worker/__init__.py'
        spec = importlib.util.spec_from_file_location('adapter', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        class Context:
            def __init__(self): self.tools = {}
            def register_tool(self, **kwargs): self.tools[kwargs['name']] = kwargs
        ctx = Context()
        module.register(ctx)
        self.assertEqual(set(ctx.tools), {'codex_submit', 'codex_status', 'codex_continue'})
        entry = ctx.tools['codex_status']
        # Hermes dispatches handler(args_dict, **kwargs) positionally.
        self.assertIn('error', json.loads(entry['handler']({'job_id': 'bad'})))
        self.assertEqual(entry['schema']['parameters']['required'], ['job_id'])
        self.assertEqual(entry['schema']['name'], 'codex_status')


class ReapGroupTests(unittest.TestCase):
    """A killed worker can leave a live child in its process group; the watcher must reap it
    before retrying, or the retry collides with the orphan (real codex reports
    "thread <id> already has an active writer")."""

    def test_reap_group_kills_orphans(self):
        proc = subprocess.Popen(['/bin/sh', '-c', 'sleep 30 & sleep 30'],
                                start_new_session=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        time.sleep(0.3)
        pgid = os.getpgid(proc.pid)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        self.assertTrue(watcher.reap_group(pgid))
        with self.assertRaises(ProcessLookupError):
            os.killpg(pgid, 0)

    def test_reap_group_missing_group(self):
        self.assertTrue(watcher.reap_group(None))
        self.assertTrue(watcher.reap_group(999999999))
