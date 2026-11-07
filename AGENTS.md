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
- `socket.bind()` is blocked inside your sandbox, so `tests/test_web.py` fails with
  `PermissionError` during your run. That is expected — do not try to work around it, and do not
  delete or skip those tests. The operator re-runs the suite outside the sandbox.

## Definition of done

Run `python3 -m unittest discover -s tests -v`. All non-web tests must pass; web tests are
expected to error with `PermissionError` on bind and nothing else. Quote the output verbatim in
your summary. Never claim success without having run it.

## What the operator verifies afterwards

- the full suite outside the sandbox (all 16+ green);
- the page served on `127.0.0.1` against real jobs, checked visually;
- `git diff` review, then commit and push.
