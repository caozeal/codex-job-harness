"""Command-line and plugin entry points."""
import argparse
import json
import time
from . import store, watcher

submit = store.submit
status = store.read
continue_job = store.continue_job


def main(argv=None):
    parser = argparse.ArgumentParser(prog='codex_jobs')
    subs = parser.add_subparsers(dest='command', required=True)
    p = subs.add_parser('submit')
    p.add_argument('--repo', required=True)
    p.add_argument('--task', required=True)
    p.add_argument('--mode', choices=('analyze', 'edit'), default='analyze')
    p = subs.add_parser('status')
    p.add_argument('job_id')
    p = subs.add_parser('continue')
    p.add_argument('job_id')
    p.add_argument('--task', required=True)
    p = subs.add_parser('list')
    p.add_argument('--status')
    p = subs.add_parser('watch')
    group = p.add_mutually_exclusive_group()
    group.add_argument('--once', action='store_true')
    group.add_argument('--loop', action='store_true')
    p.add_argument('--interval', type=float, default=60)
    p = subs.add_parser('serve')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8770)
    p.add_argument('--open', action='store_true')
    p.add_argument('--allow-remote', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'submit':
            result = submit(args.task, args.repo, args.mode)
        elif args.command == 'status':
            result = status(args.job_id)
        elif args.command == 'continue':
            result = continue_job(args.job_id, args.task)
        elif args.command == 'list':
            result = store.list_jobs(args.status)
        elif args.command == 'serve':
            from . import web
            web.serve(args.host, args.port, args.open, args.allow_remote)
            return
        else:
            if args.interval <= 0:
                raise ValueError('interval must be positive')
            while True:
                watcher.sweep()
                if not args.loop:
                    return
                time.sleep(args.interval)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
