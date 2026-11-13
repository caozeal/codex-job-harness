"""Read-only local HTTP view of the job store."""
import datetime
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
from urllib.parse import urlsplit

from . import store


PAGE = '''<!doctype html>
<html lang="zh-Cn"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codex 任务</title><style>
:root{color-scheme:dark}body{font:14px system-ui,sans-serif;background:#101318;color:#e3e7ed;margin:0}header,main{padding:16px 24px}header{border-bottom:1px solid #343b46}h1{display:inline;font-size:20px;margin-right:20px}.chip{display:inline-block;background:#29313d;border-radius:12px;padding:3px 9px;margin:4px}label{margin-left:16px}.table-wrap{overflow-x:auto}table{width:100%;min-width:1200px;table-layout:fixed;border-collapse:collapse;text-align:left}th,td{padding:10px;border-bottom:1px solid #303742;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}th{white-space:nowrap}.status{display:inline-flex;align-items:center;gap:6px}.status::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}.status.succeeded{color:#69d69a}.status.failed{color:#ff7676}.status.running{color:#f2d46b}.status.timeout{color:#ffab63}tr[data-job]{cursor:pointer}tr[data-job]:hover{background:#222b37}tr.running{animation:pulse 2s infinite}@keyframes pulse{50%{background:#253541}}#detail{position:fixed;inset:0;background:#000a;display:none;overflow:auto}#detail.open{display:block}.panel{background:#1c222c;max-width:900px;margin:4vh auto;padding:24px;border-radius:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}button{float:right}small{color:#aab4c3}
</style></head><body><header><h1>Codex 任务</h1><span id="counts"></span><small id="refresh"></small><label><input id="auto" type="checkbox" checked> 自动刷新</label></header><main><div class="table-wrap"><table><colgroup><col style="width:10%"><col style="width:11%"><col style="width:36%"><col style="width:5%"><col style="width:13%"><col style="width:6%"><col style="width:19%"></colgroup><thead><tr><th>状态</th><th>仓库</th><th>任务</th><th>尝试</th><th>开始</th><th>耗时</th><th>token</th></tr></thead><tbody id="jobs"></tbody></table></div></main><div id="detail"><div class="panel"><button id="close">关闭</button><h2 id="title"></h2><h3>最终回复</h3><pre id="response"></pre><h3>错误</h3><pre id="error"></pre><h3>token</h3><div id="usage"></div><h3>事件</h3><div id="events"></div><h3>worker.log</h3><pre id="log"></pre></div></div><script>
const $ = id => document.getElementById(id);
let opened = false, generation = 0;
function text(node, value) { node.textContent = value == null ? '' : String(value); }
function cell(row, value) { const td = document.createElement('td'); text(td, value); row.appendChild(td); return td; }
function formatUsage(usage) {
 if (!usage || typeof usage !== 'object') return '—';
 const parts = [];
 for (const [key, label] of [['input_tokens', 'in'], ['cached_input_tokens', 'cached'], ['output_tokens', 'out']]) {
  const value = usage[key];
  if (typeof value === 'number' && Number.isFinite(value)) parts.push(label + ' ' + (value >= 1000 ? (value / 1000).toFixed(1) + 'k' : value));
 }
 return parts.length ? parts.join(' · ') : '—';
}
function formatTime(value) {
 if (!value) return '—';
 const date = new Date(value); if (Number.isNaN(date.getTime())) return value;
 const pad = n => String(n).padStart(2, '0');
 return (date.getFullYear() !== new Date().getFullYear() ? date.getFullYear() + '-' : '') +
  pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds());
}
async function refresh() {
 if (opened) return;
 const version = generation;
 try {
  const res = await fetch('/api/jobs'); if (!res.ok) throw Error(res.status);
  const jobs = await res.json(); if (opened || version !== generation) return;
  $('jobs').replaceChildren(); $('counts').replaceChildren();
  const counts = {};
  for (const job of jobs) {
   counts[job.status] = (counts[job.status] || 0) + 1;
   const row = document.createElement('tr'); row.dataset.job = job.job_id;
   if (job.status === 'running') row.classList.add('running');
   const statusCell = cell(row, ''); const status = document.createElement('span');
   status.className = 'status'; if (['succeeded', 'failed', 'running', 'timeout'].includes(job.status)) status.classList.add(job.status);
   text(status, job.status); statusCell.appendChild(status);
   cell(row, job.repo); const task = cell(row, job.task); task.title = job.task || '';
   if (typeof job.task === 'string' && job.task.length >= 160) {
    fetch('/api/jobs/' + job.job_id).then(res => res.ok ? res.json() : null).then(detail => {
     if (detail && typeof detail.request.task === 'string') task.title = detail.request.task;
    }).catch(() => {});
   }
   for (const value of [job.attempts, formatTime(job.started_at), job.duration_s == null ? '' : job.duration_s + 's', formatUsage(job.usage)]) cell(row, value);
   row.addEventListener('click', () => show(job.job_id)); $('jobs').appendChild(row);
  }
  for (const [status, count] of Object.entries(counts)) { const chip = document.createElement('span'); chip.className = 'chip'; text(chip, status + ' ' + count); $('counts').appendChild(chip); }
  text($('refresh'), '更新于 ' + new Date().toLocaleTimeString());
 } catch (err) { text($('refresh'), '刷新失败: ' + err); }
}
async function show(id) {
 opened = true; const version = ++generation; $('detail').classList.add('open'); text($('title'), id);
 for (const id of ['response', 'error', 'log', 'usage']) text($(id), ''); $('events').replaceChildren();
 try {
  const res = await fetch('/api/jobs/' + id); if (!res.ok) throw Error(res.status);
  const job = await res.json(); if (!opened || version !== generation) return;
  text($('response'), job.status.final_response); text($('error'), job.status.error); text($('log'), job.log_tail); text($('usage'), formatUsage(job.status.usage));
  for (const event of job.events) { const entry = document.createElement('div'); text(entry, (event.event || '—') + ' · ' + formatTime(event.at)); $('events').appendChild(entry); }
 } catch (err) { if (opened && version === generation) text($('error'), '加载失败: ' + err); }
}
function closeDetail() { opened = false; generation++; $('detail').classList.remove('open'); refresh(); }
$('close').addEventListener('click', closeDetail); document.addEventListener('keydown', event => { if (event.key === 'Escape' && opened) closeDetail(); });
refresh(); setInterval(() => { if ($('auto').checked && !opened) refresh(); }, 3000);
</script></body></html>'''


def _duration(status):
    try:
        start = datetime.datetime.fromisoformat(status['started_at'].replace('Z', '+00:00'))
        end_value = status.get('finished_at')
        if not end_value and status.get('status') != 'running':
            return None
        end = (datetime.datetime.fromisoformat(end_value.replace('Z', '+00:00')) if end_value
               else datetime.datetime.now(datetime.timezone.utc))
        return round((end - start).total_seconds(), 1)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _trim(value, limit):
    return value[:limit] if isinstance(value, str) else value


def jobs():
    result = []
    root = store.root()
    if not root.exists():
        return result
    for path in root.iterdir():
        if not path.is_dir() or not re.fullmatch(r'[0-9a-f]{32}', path.name):
            continue
        try:
            request = store.read_json(path / 'request.json')
            status = store.read_json(path / 'status.json')
            if not isinstance(request, dict) or not isinstance(status, dict):
                continue
            item = {key: status.get(key) for key in ('status', 'attempts', 'created_at', 'started_at',
                    'finished_at', 'notified', 'usage')}
            item.update(job_id=path.name, repo=Path(request['repo']).name, mode=request.get('mode'),
                        task=_trim(request.get('task'), 160), duration_s=_duration(status),
                        final_response=_trim(status.get('final_response'), 400), error=_trim(status.get('error'), 300))
            result.append(item)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            continue
    result.sort(key=lambda item: item.get('created_at') or '', reverse=True)
    return result


def detail(job_id):
    path = store.job_path(job_id)
    if not path.is_dir():
        return None
    request = store.read_json(path / 'request.json')
    status = store.read_json(path / 'status.json')
    events = []
    try:
        with (path / 'events.jsonl').open(encoding='utf-8') as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    try:
        with (path / 'worker.log').open(encoding='utf-8', errors='replace') as f:
            log_tail = ''.join(deque(f, maxlen=60))
    except FileNotFoundError:
        log_tail = ''
    return dict(request=request, status=status, events=events, log_tail=log_tail)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._respond(False)

    def do_HEAD(self):
        self._respond(True)

    def _respond(self, head):
        path = urlsplit(self.path).path
        code, content_type = 200, 'application/json; charset=utf-8'
        if path == '/':
            body, content_type = PAGE.encode('utf-8'), 'text/html; charset=utf-8'
        else:
            if path == '/healthz':
                value = {'ok': True}
            elif path == '/api/jobs':
                value = jobs()
            elif path.startswith('/api/jobs/'):
                job_id = path[len('/api/jobs/'):]
                try:
                    value = detail(job_id)
                except (ValueError, OSError):
                    value = None
                if value is None:
                    code, value = 404, {'error': 'unknown job'}
            else:
                code, value = 404, {'error': 'not found'}
            body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)


def serve(host='127.0.0.1', port=8770, open_browser=False, allow_remote=False):
    if host not in ('127.0.0.1', 'localhost', '::1') and not allow_remote:
        raise ValueError('non-loopback host requires --allow-remote')
    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ':' in host else socket.AF_INET
    with Server((host, port), Handler) as server:
        if host not in ('127.0.0.1', 'localhost', '::1'):
            print('WARNING: bound to a non-loopback address, no authentication', file=sys.stderr, flush=True)
        display_host = '[' + host + ']' if ':' in host else host
        url = 'http://{}:{}/'.format(display_host, server.server_address[1])
        print(url, flush=True)
        if open_browser:
            subprocess.run(['open' if sys.platform == 'darwin' else 'xdg-open', url], check=False)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
