"""Job storage and submission."""
import datetime
import fcntl
import json
import os
import posixpath
import math
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
            'CODEX_MAX_ATTEMPTS CODEX_JOB_TIMEOUT_SEC CODEX_HOSTS_FILE').split()


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


def host_config(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('invalid host name')
    filename = os.environ.get('CODEX_HOSTS_FILE', str(Path.home() / '.hermes' / 'codex-hosts.json'))
    try:
        with open(os.path.expanduser(filename), encoding='utf-8') as f:
            hosts = json.load(f)
    except (OSError, ValueError) as exc:
        raise ValueError('cannot load CODEX_HOSTS_FILE: ' + str(exc)) from exc
    if not isinstance(hosts, dict):
        raise ValueError('CODEX_HOSTS_FILE must contain an object')
    if name not in hosts:
        raise ValueError('unknown host: ' + name)
    config = hosts[name]
    if not isinstance(config, dict):
        raise ValueError('host configuration must be an object')
    ssh_host = config.get('ssh_host')
    if not isinstance(ssh_host, str) or not re.fullmatch(r'[A-Za-z0-9._-]+', ssh_host) or ssh_host.startswith('-'):
        raise ValueError('invalid ssh_host')
    for key in ('codex_bin', 'allowed_root'):
        value = config.get(key)
        if not isinstance(value, str) or not value.startswith('/') or '\x00' in value:
            raise ValueError(key + ' must be an absolute POSIX path')
    timeout = config.get('connect_timeout_sec', 8)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError('connect_timeout_sec must be positive and at most 60')
    edits = config.get('allow_edits', False)
    if not isinstance(edits, bool):
        raise ValueError('allow_edits must be a boolean')
    return dict(config, connect_timeout_sec=timeout, allow_edits=edits)


def validate(repo, mode, task, host=None):
    if host is not None:
        config = host_config(host)
        if not isinstance(repo, str) or not repo.startswith('/') or '\x00' in repo:
            raise ValueError('remote repo must be an absolute POSIX path')
        path = posixpath.normpath(repo)
        base = posixpath.normpath(config['allowed_root'])
        if posixpath.commonpath((base, path)) != base:
            raise ValueError('remote repo is outside allowed_root')
        if mode not in ('analyze', 'edit'):
            raise ValueError('mode must be analyze or edit')
        if mode == 'edit' and os.environ.get('CODEX_ALLOW_EDITS', '').lower() != 'true':
            raise ValueError('edit mode requires CODEX_ALLOW_EDITS=true')
        if mode == 'edit' and not config['allow_edits']:
            raise ValueError('edit mode is disabled for host')
        if not isinstance(task, str) or not task.strip() or len(task.strip()) > 12000:
            raise ValueError('task must contain 1 to 12000 characters')
        return path, task.strip()
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


def submit(task, repo, mode='analyze', thread_id=None, host=None):
    repo, task = validate(repo, mode, task, host)
    job_id = uuid.uuid4().hex
    base = root()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = job_path(job_id)
    path.mkdir(mode=0o700)
    created = now()
    request = dict(job_id=job_id, task=task, repo=repo, mode=mode, thread_id=thread_id, created_at=created, attempt=1, host=host)
    status = dict(status='queued', host=host, pid=None, thread_id=thread_id, final_response=None, error=None,
                  created_at=created, started_at=None, finished_at=None, attempts=1, usage=None, notified=False, agent_notified=False)
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
    return submit(task, request['repo'], request['mode'], status['thread_id'], request.get('host'))


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


TERMINAL = frozenset(('succeeded', 'failed', 'timeout'))


def pending_jobs():
    results = []
    for item in list_jobs():
        if item['status'] in TERMINAL and not item.get('agent_notified', False):
            try:
                request = read_json(job_path(item['job_id']) / 'request.json')
                results.append(dict(item, repo=request['repo'], **({'host': request['host']} if 'host' in request else {})))
            except (OSError, ValueError, KeyError):
                continue
    return results


def mark_agent_notified(job_id):
    with locked(job_id):
        status = read(job_id)
        if status['status'] in TERMINAL and not status.get('agent_notified', False):
            status['agent_notified'] = True
            write_json(job_path(job_id) / 'status.json', status)
