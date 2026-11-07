"""Run a single Codex turn."""
import glob
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from . import store


def binary():
    override = os.environ.get('CODEX_BIN')
    found = override or shutil.which('codex')
    if not found:
        matches = sorted(glob.glob(str(Path.home() / '.nvm/versions/node/*/bin/codex')))
        found = matches[-1] if matches else None
    if not found:
        raise FileNotFoundError('codex executable not found')
    return str(Path(found).expanduser().resolve())


def main(job_id):
    path = store.job_path(job_id)
    with store.locked(job_id):
        request = store.read_json(path / 'request.json')
        status = store.read(job_id)
        if status['status'] not in ('queued', 'running') or status.get('finished_at'):
            return
        status.update(status='running', pid=os.getpid(), started_at=store.now(), attempts=request['attempt'])
        store.write_json(path / 'status.json', status)
        store.atomic(path / 'pid', str(os.getpid()) + '\n')
        store.event(job_id, 'started')
    error = None
    completed = False
    response = None
    usage = None
    thread_id = request.get('thread_id')
    try:
        exe = binary()
        sandbox = 'read-only' if request['mode'] == 'analyze' else 'workspace-write'
        if thread_id:
            argv = [exe, 'exec', 'resume', thread_id, '--json', '--skip-git-repo-check',
                    '-c', 'sandbox_mode="' + sandbox + '"', '-c', 'approval_policy="never"',
                    '-c', 'model_reasoning_effort=low', request['task']]
        else:
            argv = [exe, 'exec', '--json', '-C', request['repo'], '-s', sandbox,
                    '--skip-git-repo-check', '-c', 'model_reasoning_effort=low', request['task']]
        env = os.environ.copy()
        env['PATH'] = str(Path(exe).parent) + os.pathsep + env.get('PATH', '')
        # A repository-local file avoids pipe deadlock when stderr is verbose.
        with tempfile.TemporaryFile(mode='w+', encoding='utf-8', dir=path) as stderr:
            proc = subprocess.Popen(argv, cwd=request['repo'], env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=stderr, text=True, errors='replace')
            for line in proc.stdout:
                store.append(path / 'worker.log', line)
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        continue
                except (ValueError, TypeError):
                    continue
                kind = obj.get('type')
                if kind == 'thread.started' and obj.get('thread_id'):
                    thread_id = obj['thread_id']
                    with store.locked(job_id):
                        current = store.read(job_id)
                        if current['status'] == 'running' and current['pid'] == os.getpid():
                            current['thread_id'] = thread_id
                            store.write_json(path / 'status.json', current)
                elif kind == 'item.completed' and isinstance(obj.get('item'), dict) and obj['item'].get('type') == 'agent_message':
                    response = obj['item'].get('text')
                elif kind == 'turn.completed':
                    completed = True
                    usage = obj.get('usage')
                elif kind in ('turn.failed', 'error'):
                    error = json.dumps(obj, ensure_ascii=False)
            code = proc.wait()
            stderr.seek(0)
            tail = stderr.read()[-4000:]
            if tail:
                store.append(path / 'worker.log', tail)
            if code != 0 or not completed or error:
                error = '; '.join(x for x in (error, tail.strip(), 'codex exited with code %s' % code if code else None,
                                             'missing turn.completed' if not completed else None) if x)
    except Exception as exc:
        error = str(exc)
    with store.locked(job_id):
        status = store.read(job_id)
        if status['status'] == 'running' and status['pid'] == os.getpid():
            status.update(status='failed' if error else 'succeeded', finished_at=store.now(),
                          thread_id=thread_id, final_response=response, usage=usage, error=error)
            store.write_json(path / 'status.json', status)


if __name__ == '__main__':
    main(sys.argv[1])
