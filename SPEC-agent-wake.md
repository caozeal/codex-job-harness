# Spec: 任务回来找我（wait / pending）

后台任务的价值在于「我派出去，结果回来找我」，而不是我盯着等。现在缺两个机制：

1. **等待一组任务**：派发后挂一个等待者，全部到终态就返回结论 —— 让我能被唤醒继续协调。
2. **待复核队列**：任务到终态后先进入「待复核」，我处理完再标记（用户侧的微信通知是另一条独立通道，不能被重复使用）。

## 1. `codex_jobs wait <job_id> [job_id ...]`

- 轮询 `status.json`，直到所有给定任务进入 `succeeded|failed|timeout`（或 `--timeout` 秒，默认 900，超时退出码 2）。
- 每个任务输出一行 JSON：`{"job_id", "status", "attempts", "thread_id", "final_response"(前 2000 字符), "error", "usage"}`
- 全部终态且无 failed/timeout → 退出码 0；有失败 → 退出码 1；超时 → 2。
- 该命令自身不做重试、不写状态。

## 2. `codex_jobs pending [--peek|--mark] [--json]`

- 语义：**终态但尚未被 agent 复核**的任务。
- 状态里新增独立字段 `agent_notified`（boolean，`status.json` 内，默认 false）。
  **不要复用现有的 `notified`** —— 那个字段属于 cron 发给用户的微信通知，已由 watcher 置位。
- `--peek`（默认）：列出所有终态且 `agent_notified == false` 的任务摘要，一行一条：
  `<job_id> <status> <repo> attempts=<n> <final_response 前 160 字符或 error>`
- `--mark`：把当前列出的这些任务置为 `agent_notified = true`（原子写，加锁，幂等：重复执行无副作用）。
- `--json`：输出 JSON 数组而不是文本行。
- 没有待复核任务时输出为空、退出码 0（空输出=无事可做，方便当 cron 的 monitor）。

## 3. 修改点

- `codex_jobs/store.py`：`status` 初始结构加 `agent_notified=False`；新增 `pending_jobs()` 与 `mark_agent_notified(job_id)`；读旧状态缺该字段时按 false 处理（兼容已存在的任务目录）。
- `codex_jobs/cli.py`：注册 `wait`、`pending` 两个子命令。
- `README.md`：补两段用法。
- 不要动 `watcher.py` 里 `notified` 的语义与 cron 的微信通知路径。

## 4. 测试（`tests/test_harness.py` 追加，或用 `tests/test_wait_pending.py`）

- `wait` 对已经终态的任务立即返回、退出码正确；
- `wait` 对 running 任务会阻塞，等 stub 任务转 succeeded 后返回（用 fake_codex 的短 sleep 模式，超时 10s 内完成）；
- `wait` 有 failed 任务 → 退出码 1；
- 超时 → 退出码 2（用 1s 超时 + 长睡 stub）；
- `pending --peek` 只列终态任务，且不修改 `agent_notified`；
- `pending --mark` 之后 `--peek` 为空，重复 mark 无副作用；
- 旧任务目录（status.json 无 `agent_notified` 字段）按未复核处理；
- `notified` 与 `agent_notified` 相互独立：watcher sweep 只改前者。
- 全量 `python3 -m unittest discover -s tests -v` 必须全绿（用 network_access 开关跑）。

约束不变：纯 stdlib、无 shell=True、不 commit/push、不碰仓库外文件。
