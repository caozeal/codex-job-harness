"""Hermes adapter for the codex job harness.

The harness library lives outside this plugin directory (repo root `codex_jobs/`).
Hermes loads plugins by path, so make the library importable first:
  1. $CODEX_HARNESS_HOME when set,
  2. the repo root this plugin sits in (dev layout),
  3. ~/.hermes/codex-harness (installed layout).
"""
import json
import os
from pathlib import Path
import sys

_HERE = Path(__file__).resolve()


def _harness_root():
    candidates = []
    env = os.environ.get("CODEX_HARNESS_HOME")
    if env:
        candidates.append(Path(env).expanduser())
    # plugin/codex-worker/__init__.py -> repo root is parents[2]
    candidates.append(_HERE.parents[2])
    candidates.append(Path.home() / ".hermes" / "codex-harness")
    for candidate in candidates:
        if (candidate / "codex_jobs" / "__init__.py").is_file():
            return candidate
    raise ImportError(
        "codex_jobs package not found; set CODEX_HARNESS_HOME to the harness repo root"
    )


_ROOT = _harness_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from codex_jobs import cli  # noqa: E402  (import follows the sys.path setup above)


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
            "repo": {"type": "string", "description": "Absolute path to a Git repo inside CODEX_ALLOWED_ROOT."},
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
