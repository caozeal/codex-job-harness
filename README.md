# Codex job harness

A stdlib-only local job runner for submitting Codex CLI turns, checking their status, and continuing a thread. The Hermes plugin in `plugin/codex-worker` is a thin adapter.

Architecture diagram: [`docs/architecture.html`](docs/architecture.html) (open it in a browser — inline SVG, no build step).

Set `CODEX_ALLOWED_ROOT` to the directory containing permitted Git repositories. `CODEX_JOB_DIR` defaults to `~/.hermes/codex-jobs`. Optional settings: `CODEX_ALLOW_EDITS=true` enables edit mode; `CODEX_BIN` selects the executable; `CODEX_MAX_ATTEMPTS` (default 2) and `CODEX_JOB_TIMEOUT_SEC` (default 1800) control retries and timeout. `HOME`, `CODEX_HOME`, `PATH`, locale, temporary-directory and proxy variables are passed to workers when set.

```
python3 -m codex_jobs submit --repo /path/to/repo --task 'Review this code' [--mode analyze|edit]
python3 -m codex_jobs status JOB_ID
python3 -m codex_jobs continue JOB_ID --task 'Follow up'
python3 -m codex_jobs list [--status succeeded]
python3 -m codex_jobs watch --once
```

Run `watch --once` every minute under launchd or cron, or run `watch --loop --interval 60` as a service. It retries dead workers (reaping the killed worker's process group first, or the retry collides with the orphan holding the Codex thread lock), kills timed-out process groups and emits one JSON notification per terminal job.

Repositories must resolve inside the allowed root and contain `.git`. Analyze mode uses read-only sandboxing; edits require explicit opt-in. Resume turns explicitly set the sandbox and disable approval prompts, so they do not implicitly inherit a less restrictive sandbox from Codex configuration. Job directories and files are private to the current user.

## 任务可视化

运行 `python3 -m codex_jobs serve [--host 127.0.0.1] [--port 8770] [--open]`，在浏览器查看任务状态、事件和日志。页面每三秒自动刷新，详情打开期间暂停刷新；数据来自 `CODEX_JOB_DIR`，服务只提供只读 GET/HEAD 接口。

默认仅监听本机。监听其他地址必须显式加 `--allow-remote`；服务没有身份验证，请谨慎暴露。

## Installed layout

    ~/projects/codex-job-harness/       # source of truth (git repo) — edit here
    ~/.hermes/codex-harness/            # installed copy (CODEX_HARNESS_HOME), written by install.sh
    ~/.hermes/plugins/codex-worker/     # Hermes adapter: codex_submit / codex_status / codex_continue
    ~/.hermes/scripts/codex_watch.py    # cron entry: sweep + print (empty output = silence)
    ~/.hermes/.env                      # CODEX_HARNESS_HOME, CODEX_ALLOWED_ROOT, CODEX_ALLOW_EDITS

Edit the source repo, then `./install.sh` (idempotent rsync of library + plugin + cron entry).
Changing plugin code needs `hermes plugins disable codex-worker && hermes plugins enable codex-worker`
— `enable` alone says "already enabled" and leaves the old module loaded.

Notifications run on a Hermes cron job (`codex-watch`, every 1m, `no_agent`), which runs
`codex_watch.py` and delivers its stdout to the chat verbatim. `notify.py` is the same sweep
with a direct `hermes send` path (`CODEX_NOTIFY_MODE=send`, default) for use outside cron.
`launchctl bootstrap` is refused from inside the gateway, so a LaunchAgent is not used.

## Requirements

- Python 3.9+ (stdlib only, no dependencies)
- `codex` CLI 0.125+ (`npm i -g @openai/codex`), logged in (`codex login`) or `OPENAI_API_KEY` set
- A Git repository inside `CODEX_ALLOWED_ROOT`

## License

MIT — see [LICENSE](LICENSE).


