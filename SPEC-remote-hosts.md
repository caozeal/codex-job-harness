# Spec: remote Codex hosts for the async job harness

Implement support for running a job's Codex turn on a **pre-configured remote SSH
host** (first host: `macbook`), while job storage, status, watcher, notifications and
`codex_continue` stay on this machine. The default (no host) path must behave
**exactly** as it does today — every existing test must pass unchanged.

## Goal

`codex_submit(host="macbook", repo="/Users/caozeal/projects/github/postme", task="...")`
submits a job whose `codex exec` runs on the MacBook with that Mac's own Codex
login/session store. `codex_status` / `codex_continue` / watcher / cron work unchanged.
`codex_continue` must resume the thread **on the same remote host**.

Non-goals: no new cron jobs, no changes to `~/.hermes/` or `~/.codex/`, no config
sync between machines, no remote file transfer, no interactive TUI, no new
dependencies.

## Verified facts — do not guess, do not re-derive

Local harness (`~/projects/codex-job-harness`, codex-cli 0.167.0 at
`~/.nvm/versions/node/v24.21.0/bin/codex`):

- Fresh turn argv: `codex exec --json -C <repo> -s <read-only|workspace-write>
  --skip-git-repo-check -c model_reasoning_effort=low "<task>"`
- Resume argv: `cwd=<repo>` and `codex exec resume <thread_id> --json
  --skip-git-repo-check -c sandbox_mode="<read-only|workspace-write>"
  -c approval_policy="never" -c model_reasoning_effort=low "<task>"`.
  `exec resume` accepts **neither `-C` nor `-s`**; the sandbox MUST be set through
  `-c`, otherwise the host config is inherited.
- `--json` emits JSONL; events handled in `worker.py`: `thread.started`,
  `item.completed`/`agent_message`, `turn.completed`/`usage`, `turn.failed`, `error`.
- `store.spawn()` passes only the `ENV_KEYS` allowlist to the worker process.

Remote host (`macbook`, SSH alias already passwordless):

- `ssh -o BatchMode=yes -o ConnectTimeout=8 macbook` works; user `caozeal`,
  macOS 26.5.2, remote home `/Users/caozeal`.
- `codex` is **not** on the non-login shell PATH. Binary:
  `/Users/caozeal/.nvm/versions/node/v24.20.0/bin/codex` (codex-cli 0.168.0).
  Node/git/rg also live under that nvm bin, so prepend it to the remote PATH.
- Remote `~/.codex/auth.json` has a valid ChatGPT OAuth session (`auth_mode=chatgpt`).
  Use it as-is; **never** read, print or copy its values.
- Remote `~/.codex/config.toml` has `approval_policy = "never"`,
  `sandbox_mode = "danger-full-access"`, `model_reasoning_effort = "xhigh"` and
  several hooks. Therefore **every** remote argv must explicitly pass the sandbox
  (`-s` for fresh, `-c sandbox_mode=...` for resume), `-c approval_policy="never"`
  for resume, and `-c model_reasoning_effort=low` for both. Never let a remote turn
  inherit `danger-full-access` or `xhigh`.
- Remote verified git repo for an end-to-end probe:
  `/Users/caozeal/projects/github/postme`.
- Remote `codex exec --help` confirms `-s`, `-c`, `--json`, `--skip-git-repo-check`.

## Host configuration

New env var `CODEX_HOSTS_FILE`; default `~/.hermes/codex-hosts.json`. JSON object,
keyed by a short host name. Each entry:

```json
{
  "macbook": {
    "ssh_host": "macbook",
    "codex_bin": "/Users/caozeal/.nvm/versions/node/v24.20.0/bin/codex",
    "allowed_root": "/Users/caozeal/projects",
    "connect_timeout_sec": 8,
    "allow_edits": false
  }
}
```

Validation rules (raise `ValueError` with a clear message):

- Host name must exist in the file; unknown/empty/malformed names are refused.
  Accept only `[A-Za-z0-9_-]+` for host names.
- `ssh_host` must match `[A-Za-z0-9._-]+` and must NOT start with `-` (it is passed
  as an argv element to `ssh`, so an option-looking value must be rejected).
- `codex_bin` and `allowed_root` must be absolute POSIX paths (remote paths — do
  not `stat()` or `resolve()` them locally).
- `connect_timeout_sec` is a positive int/float, default 8, upper bound 60.
- `allow_edits` defaults to `false`; when false, `mode="edit"` is refused for that
  host even if the global `CODEX_ALLOW_EDITS=true`.
- Malformed JSON / missing file / wrong types produce a clear `ValueError`, never a
  traceback. Load lazily at submit/worker time (do not import-time read).

## `store.validate` / `store.submit`

Signature: `submit(task, repo, mode="analyze", thread_id=None, host=None)`.

- `host is None`: **unchanged** local validation (realpath, inside
  `CODEX_ALLOWED_ROOT`, directory containing `.git`, mode/task checks, same error
  strings — existing tests assert on them).
- `host` given: `repo` is a **remote absolute path**. Do not resolve or stat it on
  this machine. Validate it is absolute, normalize `..` with
  `posixpath.normpath`/`PurePosixPath`, and require it to be inside the host's
  `allowed_root` (reject `..` escapes and prefix lookalikes like `/projects-evil`).
  Keep the same task length/mode checks. `edit` requires BOTH
  `CODEX_ALLOW_EDITS=true` and the host's `allow_edits=true`.
- `request.json` gains `host` (the name or `null`). `status.json` gains `host` too.
- `continue_job` MUST read `request["host"]` and pass it to `submit`, otherwise a
  follow-up silently runs locally and resumes a thread that only exists remotely.
- `pending_jobs()` should carry `host` through when present.
- Add `CODEX_HOSTS_FILE` to `store.ENV_KEYS` — without this the spawned worker
  cannot see the host config and every remote job fails.

## `worker` remote execution

Keep the local branch byte-for-byte equivalent in behaviour. For a remote host build
an **argv list** (never `shell=True`):

```
["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=<n>",
 "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
 <ssh_host>, <remote_command>]
```

`remote_command` is one shell string executed by the remote login shell. Build it by
`shlex.quote()`-ing **every** token (especially `task`, `repo`, `thread_id`,
`codex_bin` and the `-c` values). Never interpolate raw user text. Shape:

```
export PATH=<quoted nvm bin dir>:$PATH; cd <quoted repo> && test -d .git && \
  <quoted codex_bin> exec --json -s <sandbox> --skip-git-repo-check \
  -c model_reasoning_effort=low <quoted task>
```

- `$PATH` must stay **literal** in the remote string (do not let local Python or a
  local shell expand it). Derive the nvm bin dir from `codex_bin`'s parent.
- `cd ... && test -d .git && ...` gives a clear non-zero exit when the remote repo
  is missing, in a single SSH round trip.
- Resume: same prefix, then `exec resume <quoted thread_id> --json
  --skip-git-repo-check -c sandbox_mode="<sandbox>" -c approval_policy="never"
  -c model_reasoning_effort=low <quoted task>` — no `-C`, no `-s`.
- Sandbox is `read-only` for `mode="analyze"`, `workspace-write` for `edit`,
  derived from the request exactly as in the local branch.
- `cwd` for the local `ssh` subprocess can be any existing local directory (the
  harness repo); the real working directory is set by `cd` in the remote command.
- stdout JSONL, stderr capture, `worker.log`, `thread.started` capture,
  `turn.completed`/usage and terminal status handling stay identical. An SSH failure
  (non-zero exit plus stderr) must produce `status=failed` with the stderr tail in
  `error`, not a hanging `running` job.
- Do not call `binary()` for a remote job — use the configured `codex_bin`.
- `stdin=subprocess.DEVNULL` for the ssh process.

Known limitation to document in README (do not try to solve perfectly): killing the
local process group on timeout kills `ssh`, but the remote `codex` may briefly keep
running. `ServerAliveInterval`/`CountMax` reduce that window; document it and note
that a timed-out remote job should be checked on the host.

## CLI / plugin

- `cli.py submit` gains `--host NAME` (optional, default `None`); `cli.submit` is an
  alias of `store.submit`, so keep positional order compatible.
- `plugin/codex-worker/__init__.py`: add `host` to `codex_submit`'s schema properties
  (optional string, description must say it is a name from `CODEX_HOSTS_FILE` and
  that `repo` is then a path on that host). `_handler`'s allowed set is derived from
  the properties dict, so no other change is needed there. Do not change the
  handler signature or `register()` shape — Hermes dispatches
  `handler(args_dict, **kwargs)` positionally.
- `plugin.yaml` description may mention remote hosts; `provides_tools` stays the
  same three tools.
- README: document `CODEX_HOSTS_FILE`, the JSON schema, `submit --host`, remote repo
  semantics, the explicit sandbox/effort overrides, and the timeout limitation.

## Hard constraints

- Python 3.9+ stdlib only; no `shell=True`; no third-party packages.
- Stay inside this repository. Never touch `~/.hermes/`, `~/.codex/`, user config,
  or any remote machine. Do not run `ssh` against a real host in tests.
- Never `git commit`/`push`. The operator reviews, verifies and commits.
- Write the code yourself; do not spawn `codex` or delegate.

## Seams that MUST be handled (each is a real failure mode)

1. `CODEX_HOSTS_FILE` in `ENV_KEYS`, or the worker cannot read the config.
2. `continue_job` forwarding `host`, or follow-ups land on the wrong machine.
3. Explicit sandbox + `approval_policy` + `model_reasoning_effort=low` on **both**
   fresh and resume remote turns; remote config is `danger-full-access` + `xhigh`.
4. `shlex.quote` on every remote token — a task containing `;`, `$()` or quotes must
   not be able to alter the remote command.
5. Literal `$PATH` in the remote prefix; nvm bin dir prepended.
6. `ssh_host` rejecting option-looking values and non-absolute `codex_bin`/
   `allowed_root`.
7. No local `Path.resolve()`/`stat()` of a remote repo path.
8. Non-zero ssh exit → `failed` with stderr tail, never a stuck `running`.

## Tests (must all pass before you finish)

Keep all existing tests unchanged and green. Add real end-to-end coverage of the
remote branch using a **local fake ssh executable** (a small script written by the
test into a tmp dir, referenced as `ssh_host` in a tmp `CODEX_HOSTS_FILE`). The fake
ssh receives the argv, records the remote command string to a file the test can
assert on, and executes it locally with `['/bin/sh', '-c', <remote_command>]`
(`shell=False` at the Python level). Configure `codex_bin` so the executed command
runs `tests/fake_codex.py` (add the executable bit to that stub if needed, or use a
wrapper); reuse its `success`/`failure`/`retry` modes. Do not mock `subprocess.Popen`
— the remote branch must be exercised for real.

Cover at least:

- remote success: `status=succeeded`, `thread_id`, `final_response`, `usage`;
  recorded remote command contains `cd <repo>`, `test -d .git`, `-s read-only`,
  `model_reasoning_effort=low`, and the literal `$PATH` prefix.
- remote resume via `continue_job`: recorded command contains
  `exec resume thread-123`, `sandbox_mode="read-only"`, `approval_policy="never"`,
  no `-C`, no `-s`, and the job's `host` is still the remote name.
- injection safety: a task containing `; touch <marker>` / `$(...)` does not create
  the marker and is passed as one quoted argument.
- unknown host, host-name with bad characters, relative `codex_bin`, ssh alias
  starting with `-`, remote repo outside `allowed_root`, `..` escape, and
  `edit` refused when host `allow_edits=false` — all raise `ValueError`.
- `CODEX_HOSTS_FILE` present in `store.ENV_KEYS`.
- local (no host) behaviour unchanged — the existing suite is the proof.

Run the full suite the way `AGENTS.md` requires:

```
codex exec -s workspace-write -c sandbox_workspace_write.network_access=true \
  --skip-git-repo-check "<prompt: run the tests and fix failures>"
python3 -m unittest discover -s tests -v
```

`socket.bind()` is blocked in the sandbox unless `network_access=true`; do not delete
or skip the web tests, and do not escalate to `danger-full-access`.

## Definition of done

Full suite green (`python3 -m unittest discover -s tests -v`, quote the output
verbatim in your summary, never claim success without it), `git diff` reviewable,
no files outside this repo changed, and a short summary of which seams you handled.
