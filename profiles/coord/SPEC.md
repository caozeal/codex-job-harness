# Spec: codex-job-harness

Build a small, dependency-free Python harness that lets an external agent (Hermes)
submit a coding task to the local `codex` CLI, poll it, and continue the same Codex
thread later — with crash retry handled by a watcher instead of by the agent.

Hard constraints:

- Python 3 stdlib only (no pip packages, no Node, no npm install).
- Do NOT modify `~/.hermes/plugins/`, `~/.codex/config.toml`, `~/.codex/auth.json`,
  or anything outside this repository. The only runtime writes are under
  `$CODEX_JOB_DIR` (default `~/.hermes/codex-jobs`) and this repo's tests tmpdirs.
- No `shell=True` anywhere. Build argv lists.
- Everything must be verified by tests you write and run before you finish.

## Verified facts about the local codex CLI (0.167.0) — do not guess, use these

Binary: `~/.nvm/versions/node/v24.21.0/bin/codex` (nvm install; a non-login shell
does not have it on PATH). Resolve with `shutil.which("codex")`, else glob
`~/.nvm/versions/node/*/bin/codex`; put its directory first on the child's PATH.

Fresh turn:

    codex exec --json -C <repo> -s <read-only|workspace-write> \
      --skip-git-repo-check -c model_reasoning_effort=low "<task>"

Resume a thread — IMPORTANT: `codex exec resume` has NO `-C` and NO `-s` flags on
0.167.0. You must pass the working directory as the subprocess cwd and override
sandbox/approval through `-c`:

    cwd=<repo>
    codex exec resume <thread_id> --json --skip-git-repo-check \
      -c sandbox_mode="<read-only|workspace-write>" \
      -c approval_policy="never" -c model_reasoning_effort=low "<task>"

Without those overrides a resume inherits `sandbox_mode = "danger-full-access"`
from the user's config, which must never happen implicitly.

`--json` emits one JSON object per line (JSONL). Events to handle:

    {"type":"thread.started","thread_id":"..."}
    {"type":"turn.started"}
    {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
    {"type":"turn.completed","usage":{"input_tokens":N,"cached_input_tokens":N,...}}
    {"type":"turn.failed", ...}   /  {"type":"error", ...}

Non-JSON lines (hook chatter, warnings) go to `worker.log` verbatim, never crash.

## Layout

    codex-job-harness/
    ├── README.md
    ├── codex_jobs/
    │   ├── __init__.py
    │   ├── store.py        # job dir CRUD, atomic writes, repo validation
    │   ├── worker.py       # spawns codex, parses JSONL, updates status
    │   ├── watcher.py      # retry / timeout / notify sweep
    │   └── cli.py          # python -m codex_jobs <cmd>
    ├── plugin/
    │   └── codex-worker/
    │       ├── plugin.yaml
    │       └── __init__.py # register(ctx) -> codex_submit / codex_status / codex_continue
    └── tests/
        ├── fake_codex.py   # stub CLI for tests (driven by env vars)
        └── test_*.py

The plugin is a thin adapter: it imports `codex_jobs.store`/`cli` entry points, it
must not duplicate logic. Register with the real Hermes API signature:

    ctx.register_tool(name="codex_submit", toolset="codex_worker",
                      schema={...}, handler=fn)

`plugin.yaml` fields: `name`, `version`, `description`, `provides_tools: [...]`.
Handlers return a JSON string.

## Job directory

    $CODEX_JOB_DIR/<job_id>/
    ├── request.json   # {job_id, task, repo, mode, thread_id, created_at, attempt}
    ├── status.json    # {status, pid, thread_id, final_response, error,
    │                  #  created_at, started_at, finished_at, attempts, usage, notified}
    ├── worker.log
    ├── events.jsonl   # append-only: submitted / started / retried / failed / timeout / succeeded
    └── pid

- job_id = `uuid.uuid4().hex` (32 lowercase hex). Reject any other shape on read.
- Directory 0700, files 0600.
- All writes atomic: write `<name>.<pid>.tmp` then `os.replace`.
- Status values: `queued` → `running` → `succeeded` | `failed` | `timeout`.

## Behaviour

`submit(task, repo, mode="analyze", thread_id=None)`:
1. Validate `repo`: realpath, inside `$CODEX_ALLOWED_ROOT` (required env; refuse if unset),
   is a directory, contains `.git`; otherwise raise a clear ValueError.
2. `mode` in {analyze, edit}; `edit` requires `CODEX_ALLOW_EDITS=true`.
3. `task` is a non-empty string, max 12000 chars (count after strip).
4. Create the job dir, write request.json, spawn `python -m codex_jobs.worker <job_id>`
   with `start_new_session=True`, stdin=DEVNULL, stdout/stderr → worker.log,
   env allowlist (`PATH`, `HOME`, `CODEX_HOME`, `LANG`, `LC_ALL`, `TMPDIR`,
   `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `CODEX_JOB_DIR`, `CODEX_ALLOWED_ROOT`,
   `CODEX_ALLOW_EDITS`, `CODEX_BIN`, `CODEX_MAX_ATTEMPTS`, `CODEX_JOB_TIMEOUT_SEC`).
5. Return `{job_id, status: "queued"}`.

`worker.main(job_id)`:
- flip to `running` with pid + started_at, append `started` event.
- run codex (fresh or resume per request.thread_id), stream stdout line by line,
  append raw lines to worker.log, parse JSON, record thread_id as soon as
  `thread.started` arrives, keep last `agent_message` text and the final `usage`.
- on exit code 0 with a turn.completed → `succeeded` + final_response + thread_id.
- non-zero exit / turn.failed / exception → `failed` with stderr tail in `error`.
- never leaves a job in `running` when the process exits.

`continue_job(job_id, task)`:
- only from `succeeded`; requires a stored thread_id; new job dir, request.thread_id
  set to the previous one, so the new worker resumes that thread.

`watcher.sweep(once)`:
- `running` + pid not alive + no finished_at → attempt < CODEX_MAX_ATTEMPTS (default 2)
  → bump attempt, re-spawn worker with the same request (and thread_id if known),
  append `retried` event; else mark `failed` ("worker died, attempts exhausted").
- `running` older than `CODEX_JOB_TIMEOUT_SEC` (default 1800) → kill the process group
  (`os.killpg`, SIGTERM then SIGKILL after grace), mark `timeout`.
- `succeeded|failed|timeout` with `notified` false → set notified true, append the
  terminal event, and print one JSON line to stdout for the caller to consume.
- `--loop --interval N` repeats the sweep; `--once` does a single pass.

`cli`:
    python -m codex_jobs submit --repo R --task T [--mode analyze|edit]
    python -m codex_jobs status JOB_ID
    python -m codex_jobs continue JOB_ID --task T
    python -m codex_jobs list [--status S]
    python -m codex_jobs watch [--once] [--interval N]

## Tests (must all pass before you finish)

`tests/fake_codex.py` is a stub selected through `CODEX_BIN`; it must be able to
simulate: success (emits thread.started + item.completed + turn.completed), failure
(exit 1 after an error event), hang (sleep N seconds), and a "die on first attempt,
succeed on the second" mode keyed on a counter file — so retry is tested for real,
not mocked.

Cover at least:
- repo validation: outside allowed root, missing `.git`, unset `CODEX_ALLOWED_ROOT`
- edit mode refused unless `CODEX_ALLOW_EDITS=true`
- job_id validation on read
- success path: status.json ends `succeeded`, thread_id captured, final_response set
- failure path: `failed`, error text present
- retry: first worker dies without terminal status → watcher re-spawns → succeeded,
  attempts == 2, `retried` event recorded
- timeout: hanging worker is killed and marked `timeout`
- continue: a `succeeded` job resumes with `resume <thread_id>` in argv (assert by
  making the stub log its argv into the job dir)
- atomic write: status.json is always valid JSON (no partial reads)

Run `python3 -m unittest discover -s tests -v` and include the final output in your
summary. Do not claim success without it.

## README

Short: what it is, the env vars, the four CLI commands, how the watcher is meant to
run under launchd/cron (`--once` every minute), and the security model (allowed root,
read-only default, no implicit resume sandbox).
