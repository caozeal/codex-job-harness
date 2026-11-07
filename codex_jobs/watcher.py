"""Sweep abandoned and timed out jobs and emit terminal notifications."""
import datetime
import json
import os
import signal
import time
from . import store


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate(pid):
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(0.2)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        if alive(pid):
            raise


def reap_group(pid):
    """Kill leftover children of a dead worker that share its process group.

    Returns True once the group is gone, False if it could not be cleared.
    """
    if not pid:
        return True
    for sig, grace in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        deadline = time.time() + grace
        while time.time() < deadline:
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.1)
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    return False


def sweep(once=True):
    del once
    for item in store.list_jobs():
        job_id = item['job_id']
        path = store.job_path(job_id)
        kill_pid = None
        notification = None
        with store.locked(job_id):
            status = store.read(job_id)
            if status['status'] == 'running' and not status.get('finished_at'):
                started = datetime.datetime.fromisoformat(status['started_at'])
                age = (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds()
                if age > float(os.environ.get('CODEX_JOB_TIMEOUT_SEC', '1800')):
                    kill_pid = status['pid']
                    status.update(status='timeout', error='worker timed out', finished_at=store.now())
                elif not alive(status['pid']):
                    request = store.read_json(path / 'request.json')
                    # The dead worker may have left a live codex child in the same process
                    # group holding the thread-store lock; respawning without reaping it
                    # makes the retry fail with "thread ... already has an active writer".
                    if not reap_group(status['pid']):
                        status.update(status='failed', error='worker died and its process group could not be reaped',
                                      finished_at=store.now())
                    elif request['attempt'] < int(os.environ.get('CODEX_MAX_ATTEMPTS', '2')):
                        request['attempt'] += 1
                        request['thread_id'] = status.get('thread_id') or request.get('thread_id')
                        store.write_json(path / 'request.json', request)
                        status.update(status='queued', pid=None, attempts=request['attempt'], started_at=None)
                        store.write_json(path / 'status.json', status)
                        store.event(job_id, 'retried')
                        try:
                            store.spawn(job_id)
                        except Exception as exc:
                            status.update(status='failed', error=str(exc), finished_at=store.now())
                    else:
                        status.update(status='failed', error='worker died, attempts exhausted', finished_at=store.now())
            if status['status'] in ('succeeded', 'failed', 'timeout') and not status.get('notified'):
                status['notified'] = True
                store.event(job_id, status['status'])
                notification = dict(job_id=job_id, **status)
            store.write_json(path / 'status.json', status)
        if kill_pid:
            terminate(kill_pid)
        if notification:
            print(json.dumps(notification, ensure_ascii=False), flush=True)

