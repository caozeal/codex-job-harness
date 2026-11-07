#!/usr/bin/env python3
import json
import os
from pathlib import Path
import signal
import sys
import time

root = Path(os.environ['CODEX_JOB_DIR'])
config = json.loads((root / 'fake-config.json').read_text())
for path in root.iterdir():
    if path.is_dir() and (path / 'status.json').exists():
        status = json.loads((path / 'status.json').read_text())
        if status.get('pid') == os.getppid():
            (path / 'argv.json').write_text(json.dumps(sys.argv[1:]))
            break
mode = config.get('mode', 'success')
if mode == 'retry':
    counter = root / 'counter'
    if not counter.exists():
        counter.write_text('1')
        os.kill(os.getppid(), signal.SIGKILL)
        sys.exit(0)
if mode == 'hang':
    time.sleep(config.get('seconds', 30))
if mode == 'failure':
    print(json.dumps({'type': 'error', 'message': 'fake failure'}), flush=True)
    print('failure stderr', file=sys.stderr, flush=True)
    sys.exit(1)
print('hook chatter', flush=True)
print(json.dumps({'type': 'thread.started', 'thread_id': 'thread-123'}), flush=True)
print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}}), flush=True)
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 42}}), flush=True)
