# Spec: 任务可视化页面（codex_jobs serve）

Add a read-only web view over the job store so a human can watch Codex jobs without
reading JSON files by hand.

Hard constraints (unchanged from the main SPEC):

- Python 3 stdlib only — `http.server`, `json`, `pathlib`; no Flask, no npm, no CDN assets.
- No `shell=True`. GET/HEAD only; no write endpoints, no file mutation.
- Never bind a non-loopback address unless the operator passes an explicit flag, and print a
  loud warning when they do. Default is `127.0.0.1:8770`.
- Do not change existing job semantics, job-dir layout, or the CLI's current subcommands.

## New module: `codex_jobs/web.py`

    codex_jobs/web.py        # handler + page + serve()
    README.md                # new "任务可视化" section
    tests/test_web.py        # tests (stdlib unittest)

### Routes (GET/HEAD only)

    /                       single-page HTML (inline CSS + JS, no external URLs)
    /api/jobs               JSON array, newest first
    /api/jobs/<job_id>      JSON object: request, status, events[], log_tail
    /healthz                {"ok": true}

- `<job_id>` must match `^[0-9a-f]{32}$`; anything else → 404 JSON. No path traversal.
- `/api/jobs` item shape:
  `{job_id, status, repo, mode, task, attempts, created_at, started_at, finished_at,
    duration_s, notified, usage, final_response(trimmed 400 chars), error(trimmed 300 chars)}`
  `repo` is the basename; `task` is trimmed to 160 chars. `duration_s` = finished-start or
  now-start for running jobs (float, 1 decimal).
- `/api/jobs/<id>` returns the full `final_response` and the last 60 lines of `worker.log`
  (`log_tail`), plus `events` parsed from `events.jsonl` (skip malformed lines).
- Missing job dir → 404 JSON `{"error": "unknown job"}`. Bad JSON files must not 500 the whole
  listing: skip that job.
- Responses: `Content-Type: application/json; charset=utf-8` / `text/html; charset=utf-8`,
  `Cache-Control: no-store`. HEAD returns headers only.

### Page

Dark, compact, no build step. Must include:

- Header: title, job counts by status (chips), last refresh time, auto-refresh toggle.
- Table: 状态 / 仓库 / 任务 / 尝试 / 开始 / 耗时 / token；`running` rows pulse.
- Click a row → detail panel: full final_response (pre-wrap), error, events timeline,
  `worker.log` tail. Close with Esc.
- Refresh every 3s via `fetch('/api/jobs')` (no full page reload), paused while the detail
  panel is open.
- All dynamic text rendered with `textContent` (never `innerHTML`) — job content is untrusted.
- Works offline: no `<script src>`, no `<link href>` to any http(s) URL.

### CLI

    python -m codex_jobs serve [--host 127.0.0.1] [--port 8770] [--open]

- `--open` opens the default browser after bind (macOS `open`, Linux `xdg-open`) using
  `subprocess.run([...])` without a shell.
- Refuse hosts outside {`127.0.0.1`, `localhost`, `::1`} unless `--allow-remote`; with
  `--allow-remote` print `WARNING: bound to a non-loopback address, no authentication`.
- Print the URL on start; `KeyboardInterrupt` exits cleanly.

## Revision 2 — 表格可读性（实测问题）

第一次渲染在真实数据下不可读，逐条修：

1. **token 列**：不要 `JSON.stringify(usage)`。渲染成 `in 85.1k · cached 62.3k · out 299`
   （≥1000 时用 k 保留一位小数；缺字段就省略该项；没有 usage 显示 `—`）。
2. **开始列**：ISO 字符串太丑，转本地时间 `MM-DD HH:MM:SS`；跨年才带年份。
3. **任务列**：单行、超宽省略号，`title` 属性放全文；宽度约占表格 40%。
4. **表头**：`尝试` 不能被拆成两行（`white-space: nowrap`），该列窄。
5. **表格布局**：`table-layout: fixed` + `<colgroup>` 指定列宽，避免长 JSON 把其他列挤成一字一行；
   窄屏时整表可横向滚动而不是挤压。
6. **状态列**：给状态加颜色（succeeded 绿 / failed 红 / running 黄 / timeout 橙），圆点 + 文字。
7. 详情面板同理：`usage` 不要裸 JSON，按上面格式；`events` 每行显示 `事件 · 时间`。

验收：用真实 `~/.hermes/codex-jobs` 数据（含 succeeded 与 running 各一条）在 1440 宽下表格不换行、
不出现一字一行、token 列不溢出。测试里补一条：`/` 页面 HTML 不含 `JSON.stringify` 之外无约束——
至少断言 `/api/jobs` 的 `usage` 原样返回（前端格式化，不在服务端丢数据）。


### Tests (`tests/test_web.py`, unittest)

Start the server on port 0 in a thread with a temporary `CODEX_JOB_DIR`, then assert:

- `/healthz` → 200 `{"ok": true}`
- `/api/jobs` shape and ordering (two fixture jobs: one succeeded, one running with no
  finished_at), `repo` is a basename, `task` truncated
- `/api/jobs/<id>` returns events + log_tail, and a malformed `events.jsonl` line is skipped
- bad id (`../x`, 31 hex chars) → 404
- unknown id → 404
- a job dir with corrupt `status.json` is skipped by `/api/jobs`, not fatal
- HEAD `/` returns no body
- `/` HTML contains no `http://` or `https://` asset references
- non-loopback bind is refused without `--allow-remote`

Run `python3 -m unittest discover -s tests -v` — the existing 9 tests plus the new ones must all
pass. Do not commit or push; leave the working tree for review.
