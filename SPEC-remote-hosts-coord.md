# Spec 2: schema polish + coord profile harness port

Two deliverables. Both are code changes inside **this repository** — the operator
prepared a working copy of the coord profile's private harness at `profiles/coord/`.

## 1. Fix the main plugin's `repo` description

`plugin/codex-worker/__init__.py`, `codex_submit`'s `repo` property still says
"Absolute path to a Git repo inside CODEX_ALLOWED_ROOT." That is wrong when `host`
is set. Rewrite it so it covers both cases: local path inside `CODEX_ALLOWED_ROOT`
when `host` is omitted, absolute POSIX path inside that host's `allowed_root` when
`host` is given. Keep the existing `host` property and its description. Do not touch
the handler signature, `_handler`'s allowed-key filtering, or `register()`'s shape —
Hermes dispatches `handler(args_dict, **kwargs)` positionally.

Add a small assertion in the existing plugin-registration test that the `repo`
description mentions both the local and remote cases, so this cannot silently
regress. Run the full suite afterwards.

## 2. Port remote-host support into `profiles/coord/`

The copy at `profiles/coord/` is the coord bot's private harness. It deliberately
differs from the main one — **preserve all of these**:

- package name `codex_jobs_coord` (a multiplex gateway shares one process; two copies
  importing the same package name would write the first bot's queue)
- `store.root()` default `PROJECT / 'codex-jobs'` (static, not env-driven)
- `plugin/codex-worker/__init__.py`'s hardcoded `_harness_root()` pointing at
  `~/.hermes/profiles/coord/codex-harness`
- coord's own `notify.py`, watcher cron entry and any coord-specific text in README/tests

Port the remote-host feature from the already-verified main `codex_jobs/` code:

- `codex_jobs_coord/store.py`: `host_config(name)`, `validate(..., host=None)`,
  `submit(..., host=None)`, `request.json`/`status.json` `host` field,
  `continue_job()` forwarding the original host, `pending_jobs()` carrying host,
  and `CODEX_HOSTS_FILE` added to `ENV_KEYS`. Keep coord's `root()` default.
  Validation rules are identical to the main implementation: host-name allowlist,
  `ssh_host` rejecting option-looking values, absolute `codex_bin`/`allowed_root`,
  remote repo absolute + inside `allowed_root` (no local resolve/stat),
  `allow_edits` gating on both the host flag and `CODEX_ALLOW_EDITS=true`.
- `codex_jobs_coord/worker.py`: the ssh remote branch, `shlex.quote` on every remote
  token, literal `$PATH` prefix from `codex_bin`'s parent, `cd && test -d .git`,
  explicit sandbox (`-s` fresh / `-c sandbox_mode=...` resume), `approval_policy="never"`,
  `model_reasoning_effort=low`, `ServerAliveInterval`/`CountMax`, `cwd=PROJECT` for the
  ssh process, and a `failed` status with stderr tail on a non-zero ssh exit. Never
  inherit the remote host's `danger-full-access` or `xhigh`.
- `codex_jobs_coord/cli.py`: `submit --host NAME`, passing `host=args.host`.
- `profiles/coord/plugin/codex-worker/__init__.py`: add the `host` property and the same
  corrected `repo` description. Keep coord's hardcoded `_harness_root()`.
- `profiles/coord/README.md`: the Remote hosts section (config schema, `submit --host`,
  remote repo semantics, explicit sandbox/effort overrides, timeout limitation).
- `profiles/coord/tests/`: adapt `tests/test_remote.py` from the main suite to import
  `codex_jobs_coord` and run all its assertions (success/resume/injection/validation/
  ENV_KEYS/failure). Keep every existing coord test unchanged and green.
- `profiles/coord/install.sh`: an idempotent deploy script that syncs this copy to
  `~/.hermes/profiles/coord/codex-harness` and copies `plugin/codex-worker` to
  `~/.hermes/profiles/coord/plugins/codex-worker`. It must **not** delete the runtime
  `codex-jobs/` data directory. **Write the script; do NOT run it.** The operator runs it.

## Hard constraints

- Python 3.9+ stdlib only. No `shell=True`; build argv lists.
- Stay inside this repository. Never touch `~/.hermes/`, `~/.codex/`, user config, or any
  real remote host. Tests must use a local fake ssh executable, never `ssh macbook`.
- Never `git commit`, `git push`, or rewrite history.
- Write the code yourself. Do not spawn another `codex` and do not delegate.

## Verification (quote verbatim output in your summary)

Run both suites inside the sandbox with network access enabled (the web tests bind a
socket, which `workspace-write` blocks by default):

```
python3 -m unittest discover -s tests -v
cd profiles/coord && python3 -m unittest discover -s tests -v
```

All tests green in both. Also confirm the coord plugin still registers exactly three
tools and that `codex_submit`'s schema contains `host` with the corrected `repo`
description. Do not delete or skip socket-binding tests; do not escalate to
`danger-full-access`. Report any deviation from this spec explicitly.
