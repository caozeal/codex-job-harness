"""Sweep codex jobs once and push each terminal notification to a chat target.

Run from launchd every minute:  /usr/bin/python3 ~/.hermes/codex-harness/notify.py

Uses `hermes send` (no LLM, no agent loop). Each job is notified exactly once because
the watcher flips `notified` while holding the job lock.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from codex_jobs import store, watcher  # noqa: E402

HERMES = Path.home() / ".hermes/hermes-agent/.hermes/bin/hermes"
TARGET = os.environ.get("CODEX_NOTIFY_TARGET", "weixin")
HEAD = {"succeeded": "✅ Codex 任务完成", "failed": "❌ Codex 任务失败", "timeout": "⏱ Codex 任务超时"}


def describe(note):
    job_id = note["job_id"]
    try:
        request = store.read_json(store.job_path(job_id) / "request.json")
    except Exception:
        request = {}
    repo = Path(request.get("repo", "?")).name
    mode = request.get("mode", "?")
    status = note.get("status", "?")
    lines = [
        f"{HEAD.get(status, status)} · {repo}（{mode}）",
        f"job {job_id[:8]} · 第 {note.get('attempts', 1)} 次尝试",
    ]
    body = note.get("final_response") or note.get("error") or ""
    body = " ".join(str(body).split())
    if body:
        lines.append(body[:1200] + ("…" if len(body) > 1200 else ""))
    if note.get("usage"):
        u = note["usage"]
        lines.append(f"tokens in {u.get('input_tokens', 0)} / cached {u.get('cached_input_tokens', 0)} / out {u.get('output_tokens', 0)}")
    return "\n".join(lines)


def main():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        watcher.sweep()
    notes = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    if not notes:
        return 0
    if os.environ.get("CODEX_NOTIFY_MODE", "send") == "stdout":
        # Hermes cron (no_agent) delivers this stdout verbatim; empty means silence.
        print("\n\n".join(describe(note) for note in notes))
        return 0
    failures = 0
    for note in notes:
        text = describe(note)
        try:
            proc = subprocess.run([str(HERMES), "send", "-t", TARGET, text],
                                  capture_output=True, text=True, timeout=90)
            if proc.returncode != 0:
                failures += 1
                print(f"send failed for {note['job_id']}: {proc.stderr.strip()[:300]}", file=sys.stderr)
            else:
                print(f"sent {note['job_id'][:8]} -> {TARGET}")
        except Exception as exc:
            failures += 1
            print(f"send raised for {note['job_id']}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
