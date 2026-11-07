import contextlib
import http.client
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from codex_jobs import cli, web


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'CODEX_JOB_DIR': str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.old, self.new = 'a' * 32, 'b' * 32
        self.fixture(self.old, 'succeeded', '2025-01-01T00:00:00+00:00', '2025-01-01T00:01:00+00:00')
        self.fixture(self.new, 'running', '2025-01-02T00:00:00+00:00', None)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def fixture(self, job_id, status, created, finished):
        path = self.root / job_id
        path.mkdir()
        (path / 'request.json').write_text(json.dumps({'repo': '/some/path/project', 'mode': 'analyze', 'task': 'x' * 200}))
        (path / 'status.json').write_text(json.dumps({'status': status, 'created_at': created, 'started_at': created,
            'finished_at': finished, 'attempts': 1, 'notified': False, 'usage': {'input_tokens': 42},
            'final_response': 'y' * 500, 'error': 'z' * 400}))
        (path / 'events.jsonl').write_text('{"event":"start"}\ninvalid\n{"event":"end"}\n')
        (path / 'worker.log').write_text(''.join('line %d\n' % i for i in range(70)))

    def request(self, path, method='GET'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        self.addCleanup(conn.close)
        conn.request(method, path)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_health_and_headers(self):
        status, headers, body = self.request('/healthz')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'ok': True})
        self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_listing_and_corrupt_status(self):
        corrupt = self.root / ('c' * 32)
        corrupt.mkdir()
        (corrupt / 'request.json').write_text('{}')
        (corrupt / 'status.json').write_text('{')
        status, _, body = self.request('/api/jobs')
        self.assertEqual(status, 200)
        items = json.loads(body)
        self.assertEqual([item['job_id'] for item in items], [self.new, self.old])
        self.assertEqual(set(items[0]), set('job_id status repo mode task attempts created_at started_at finished_at duration_s notified usage final_response error'.split()))
        self.assertEqual(items[0]['repo'], 'project')
        self.assertEqual(len(items[0]['task']), 160)
        self.assertEqual(len(items[0]['final_response']), 400)
        self.assertEqual(len(items[0]['error']), 300)
        self.assertIsInstance(items[0]['duration_s'], float)
        self.assertEqual(items[1]['duration_s'], 60.0)

    def test_detail(self):
        status, _, body = self.request('/api/jobs/' + self.old)
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertEqual(len(value['status']['final_response']), 500)
        self.assertEqual(value['events'], [{'event': 'start'}, {'event': 'end'}])
        self.assertEqual(value['log_tail'].splitlines(), ['line %d' % i for i in range(10, 70)])

    def test_invalid_and_unknown_ids(self):
        for job_id in ('../x', 'a' * 31, 'd' * 32):
            status, _, body = self.request('/api/jobs/' + job_id)
            self.assertEqual(status, 404)
            self.assertEqual(json.loads(body), {'error': 'unknown job'})

    def test_html_and_head(self):
        status, headers, body = self.request('/', 'HEAD')
        self.assertEqual((status, body), (200, b''))
        self.assertEqual(headers['Content-Type'], 'text/html; charset=utf-8')
        _, _, body = self.request('/')
        self.assertNotIn(b'http://', body)
        self.assertNotIn(b'https://', body)
        self.assertIn(b'textContent', body)
        self.assertNotIn(b'innerHTML', body)

    def test_other_methods_refused(self):
        self.assertEqual(self.request('/api/jobs', 'POST')[0], 501)

    def test_remote_refused(self):
        with self.assertRaisesRegex(ValueError, '--allow-remote'):
            web.serve('0.0.0.0', 0)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            cli.main(['serve', '--host', '0.0.0.0'])
        self.assertEqual(exc.exception.code, 1)


if __name__ == '__main__':
    unittest.main()
