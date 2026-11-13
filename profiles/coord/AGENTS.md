# AGENTS.md

**You are the implementation agent for this repository.** An operator (Hermes) writes the
`SPEC-*.md`; you implement it directly in the working tree.

## Rules

- Write the code yourself. **Do not spawn another `codex` process and do not delegate to another
  agent** — a nested run cannot start inside your sandbox (`Operation not permitted`), and this
  file does not ask you to.
- Never `git commit`, `git push`, or rewrite history. The operator reviews, verifies and commits.
- Stay inside this repository. Never touch `~/.hermes/`, `~/.codex/`, or user configuration.
- Python 3.9+ standard library only. No third-party packages, no npm.
- No `shell=True`; build argv lists.
- The web layer is read-only (`GET`/`HEAD`), binds loopback by default, and renders untrusted job
  text with `textContent` (never `innerHTML`).
- `socket.bind()` is blocked by default in the `workspace-write` sandbox, so `tests/test_web.py`
  fails with `PermissionError` unless network access is enabled. Run the suite with:

      codex exec -s workspace-write -c sandbox_workspace_write.network_access=true \
        --skip-git-repo-check "<prompt: run the tests and fix failures>"

  With that flag the whole suite passes inside the sandbox (verified: 7/7 web tests green).
  Do not delete or skip tests to work around a bind failure, and do not reach for
  `danger-full-access` — the narrow `network_access` switch is enough.

## Definition of done

Run `python3 -m unittest discover -s tests -v` with the `network_access=true` flag. **All tests
must pass** — 16 at the time of writing, including the 7 web tests. Quote the output verbatim in
your summary. Never claim success without having run it.

## What the operator verifies afterwards

- the full suite outside the sandbox (all 16+ green);
- the page served on `127.0.0.1` against real jobs, checked visually;
- `git diff` review, then commit and push.
