"""Hermes adapter for the codex job harness.

The installed harness library lives in the coord profile.
"""
import json
import os
from pathlib import Path
import sys

_HERE = Path(__file__).resolve()


def _harness_root():
    return Path.home() / ".hermes" / "profiles" / "coord" / "codex-harness"

_ROOT = _harness_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from codex_jobs_coord import cli  # noqa: E402  (import follows the sys.path setup above)


def _handler(fn, allowed):
    # Hermes dispatches as handler(args_dict, **kwargs) — kwargs carries context such as
    # task_id/agent, which must not be forwarded into the target function.
    def call(args=None, **kwargs):
        params = dict(args or {})
        params.update(kwargs)
        params = {key: value for key, value in params.items() if key in allowed}
        try:
            result = fn(**params)
        except (ValueError, OSError) as exc:
            result = {"ok": False, "error": str(exc)}
        return json.dumps(result, ensure_ascii=False)
    return call


_DEFINITIONS = [
    (
        "codex_submit",
        cli.submit,
        "Submit an asynchronous Codex job for a Git repository and return its job_id.",
        {
            "task": {"type": "string", "description": "What Codex should do."},
            "repo": {"type": "string", "description": "When host is omitted, an absolute local Git repo path inside CODEX_ALLOWED_ROOT; when host is given, an absolute POSIX path inside that host's allowed_root."},
            "host": {"type": "string", "description": "Host name from CODEX_HOSTS_FILE; repo is a path on that host."},
            "mode": {"type": "string", "enum": ["analyze", "edit"],
                     "description": "analyze = read-only (default), edit = workspace-write."},
        },
        ["task", "repo"],
    ),
    (
        "codex_status",
        cli.status,
        "Read a Codex job's status, thread_id, usage and final response.",
        {"job_id": {"type": "string", "description": "32-hex job id returned by codex_submit."}},
        ["job_id"],
    ),
    (
        "codex_continue",
        cli.continue_job,
        "Continue a succeeded Codex job's own thread with a follow-up task.",
        {
            "job_id": {"type": "string", "description": "A job whose status is succeeded."},
            "task": {"type": "string", "description": "Follow-up instruction."},
        },
        ["job_id", "task"],
    ),
]


def register(ctx):
    for name, fn, description, properties, required in _DEFINITIONS:
        ctx.register_tool(
            name=name,
            toolset="codex_worker",
            schema={
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
            handler=_handler(fn, set(properties)),
        )
