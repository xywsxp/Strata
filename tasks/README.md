# Strata 评测题目

本目录下每个 `.toml` 文件定义一道评测题目。agent 读取自然语言 goal 后自主规划并执行。

## 题目格式

```toml
[task]
id = "kebab-case-id"          # 必填，唯一标识，只允许 a-z 0-9 和连字符
goal = "自然语言目标"           # 必填，传给 agent 的 goal
tags = ["smoke", "gui"]       # 可选，用于 --tag 过滤
timeout_s = 120               # 墙钟超时（秒），默认 120
max_iterations = 20           # 可选，覆盖 config 里的 max_loop_iterations

[setup]                       # 可选：运行前初始化
target = "host"               # "host"（本机 shell）或 "osworld"（容器内）
commands = ["rm -f /tmp/x"]

[verify]                      # 可选：运行后校验
target = "host"
command = "cat /tmp/x"
expected_stdout_regex = "^hello$"   # 至少提供一个
expected_exit_code = 0              # 至少提供一个
```

## 运行

```bash
# 单题
uv run python scripts/run_tasks.py tasks/create-hello-txt.toml

# 批量（通配）
uv run python scripts/run_tasks.py tasks/*.toml

# 按 tag 过滤
uv run python scripts/run_tasks.py --tag smoke

# 指定 config 和报告目录
uv run python scripts/run_tasks.py --config ./config.toml --report-dir reports/
```

## 调试失败的题目

每次运行产出的全部产物在 `.strata-run/current/` 下：

```
.strata-run/current/
├── recordings/osworld.mp4     # 30fps 屏幕录像（有 ffmpeg 时）
├── llm/
│   ├── 0001_planner_req.json  # LLM 请求（图片引用为 sibling PNG）
│   ├── 0001_planner_resp.json
│   └── 0002_vision_img_0.png  # 截图原始 bytes
├── screenshots/               # 任务状态边界的关键帧
├── audit.jsonl                # 逐 action 审计日志
├── events.jsonl               # 录制器事件时间轴
└── manifest.json              # 运行摘要
```

**典型调试流程**：

```bash
# 1. 看录像：agent 做了什么
mpv .strata-run/current/recordings/osworld.mp4

# 2. 看 LLM 想了什么（JSON + 截图）
jq . .strata-run/current/llm/0001_planner_req.json
xdg-open .strata-run/current/llm/0002_vision_img_0.png

# 3. 看审计日志摘要
jq -c '{task_id, action, result}' .strata-run/current/audit.jsonl

# 4. 看聚合报告（哪些题失败了）
jq '.tasks[] | select(.verdict != "PASS")' reports/*.json
```

## 写新题

1. `cp tasks/TEMPLATE.toml tasks/my-new-task.toml`
2. 编辑 `[task]` 段的 `id` 和 `goal`
3. 如果需要前置清理，加 `[setup]`
4. 如果能 deterministic 校验，加 `[verify]`
5. 跑一遍：`uv run python scripts/run_tasks.py tasks/my-new-task.toml`

## FAQ

**Q: 怎么只重跑上次失败的题？**

```bash
jq -r '.tasks[] | select(.verdict != "PASS") | .task_id' reports/*.json \
  | xargs -I{} echo tasks/{}.toml \
  | xargs uv run python scripts/run_tasks.py
```

**Q: 怎么给某题加长 timeout？**

在 `[task]` 段加 `timeout_s = 300`（注意：当前 run_tasks.py 尚未实现 per-task timeout 传递给 orchestrator，仅影响 verify 命令）。

**Q: setup/verify 的 `target = "osworld"` 怎么用？**

当前 `run_tasks.py` 仅实现 `target = "host"`（本机 shell）。`"osworld"` target 会在后续版本中通过 `/run_python` endpoint 执行。
