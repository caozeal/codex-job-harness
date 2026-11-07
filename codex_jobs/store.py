"""Job storage and submission."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import uuid
from contextlib import contextmanager

PROJECT = Path(__file__).resolve().parent.parent
ENV_KEYS = ('PATH HOME CODEX_HOME LANG LC_ALL TMPDIR HTTP_PROXY HTTPS_PROXY NO_PROXY '
            'CODEX_JOB_DIR CODEX_ALLOWED_ROOT CODEX_ALLOW_EDITS CODEX_BIN '
            'CODEX_MAX_ATTEMPTS CODEX_JOB_TIMEOUT_SEC').split()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def root():
    return Path(os.environ.get('CODEX_JOB_DIR', str(Path.home() / '.hermes' / 'codex-jobs'))).expanduser().resolve()


def job_path(job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('invalid job_id')
    return root() / job_id


def atomic(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_json(path, value):
    atomic(path, json.dumps(value, ensure_ascii=False) + '\n')


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def read(job_id):
    return read_json(job_path(job_id) / 'status.json')


@contextmanager
def locked(job_id):
    path = job_path(job_id) / '.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def append(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a', encoding='utf-8') as f:
        f.write(text)


def event(job_id, name):
    append(job_path(job_id) / 'events.jsonl', json.dumps({'event': name, 'at': now()}) + '\n')


def spawn(job_id):
    path = job_path(job_id)
    env = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
    fd = os.open(path / 'worker.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a') as log:
        process = subprocess.Popen([sys.executable, '-m', 'codex_jobs.worker', job_id], cwd=str(PROJECT),
                                   env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    threading.Thread(target=process.wait, daemon=True).start()
    return process


def validate(repo, mode, task):
    allowed = os.environ.get('CODEX_ALLOWED_ROOT')
    if not allowed:
        raise ValueError('CODEX_ALLOWED_ROOT is required')
    base = Path(allowed).expanduser().resolve()
    path = Path(repo).expanduser().resolve()
    if not path.is_relative_to(base):
        raise ValueError('repo is outside CODEX_ALLOWED_ROOT')
    if not path.is_dir() or not (path / '.git').exists():
        raise ValueError('repo must be a directory containing .git')
    if mode not in ('analyze', 'edit'):
        raise ValueError('mode must be analyze or edit')
    if mode == 'edit' and os.environ.get('CODEX_ALLOW_EDITS', '').lower() != 'true':
        raise ValueError('edit mode requires CODEX_ALLOW_EDITS=true')
    if not isinstance(task, str) or not task.strip() or len(task.strip()) > 12000:
        raise ValueError('task must contain 1 to 12000 characters')
    return str(path), task.strip()


def submit(task, repo, mode='analyze', thread_id=None):
    repo, task = validate(repo, mode, task)
    job_id = uuid.uuid4().hex
    base = root()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = job_path(job_id)
    path.mkdir(mode=0o700)
    created = now()
    request = dict(job_id=job_id, task=task, repo=repo, mode=mode, thread_id=thread_id, created_at=created, attempt=1)
    status = dict(status='queued', pid=None, thread_id=thread_id, final_response=None, error=None,
                  created_at=created, started_at=None, finished_at=None, attempts=1, usage=None, notified=False)
    write_json(path / 'request.json', request)
    write_json(path / 'status.json', status)
    event(job_id, 'submitted')
    try:
        spawn(job_id)
    except Exception as exc:
        status.update(status='failed', error=str(exc), finished_at=now())
        write_json(path / 'status.json', status)
        raise
    return {'job_id': job_id, 'status': 'queued'}


def continue_job(job_id, task):
    status = read(job_id)
    if status['status'] != 'succeeded' or not status.get('thread_id'):
        raise ValueError('job must have succeeded and have a thread_id')
    request = read_json(job_path(job_id) / 'request.json')
    return submit(task, request['repo'], request['mode'], status['thread_id'])


def list_jobs(status=None):
    if not root().exists():
        return []
    results = []
    for path in sorted(root().iterdir()):
        if not path.is_dir() or not re.fullmatch(r'[0-9a-f]{32}', path.name):
            continue
        try:
            item = read(path.name)
            if status is None or item['status'] == status:
                results.append(dict(job_id=path.name, **item))
        except (OSError, ValueError):
            continue
    return results
