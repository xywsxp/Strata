# OSWorld 调试基础设施 —— 方案讨论（Phase 1）

> 用途：在正式进入 OSWorld 题目编写与批量评测之前，先把"状态目录收敛 + 轨迹录制 + LLM I/O 落盘 + 题目编写/执行协议"四件事一次性设计好，作为后续所有评测的共用底座。
>
> 本文档**仅做架构讨论**（无实现代码），等你回复「确认」后再落地为 `FV_EXECUTION_PLAN.md`。

---

## 1. 背景与目标

目前 agent 已能真实跑通简单目标（`scripts/agent_e2e.py` 3/3 通过），但**失败复盘能力薄弱**：

1. **状态文件散落在 `~/.strata/`**——checkpoint / audit / trash / context snapshot 全落在用户 HOME，手工清理易误伤，跨 run 隔离靠 `STRATA_STATE_DIR` 环境变量但无统一约定。
2. **没有可视轨迹**——OSWorld GUI 是容器内 Xvfb 虚拟屏，agent 失败时我们只能靠 stdout + audit.jsonl 推理，拿不到"图像层面出了什么"。
3. **LLM 调用全在黑箱**——发了什么 prompt、塞了哪张图、回了什么 JSON，这三份一手证据现在完全不存档；一旦 planner/vision 给出荒诞决策，我们无法离线复盘 prompt。
4. **OSWorld 题目编写无约定**——今天的评测靠在 `agent_e2e.py` 尾巴上硬编码 `DEFAULT_GOALS = ("...", "...")`；scale 到几十道题就散架。

**目标**：
- **G1**：所有运行产物（state/audit/trash/llm/screenshots/video/logs）统一落在工作区 `./.strata-run/`，一个目录 ignore 掉即可，清理 = `rm -rf .strata-run`。
- **G2**：每个 goal run 产出可回放的**轨迹包**——包含 OSWorld 屏幕录像（mp4）+ 每一步的 LLM prompt/response/image + audit.jsonl。
- **G3**：OSWorld 题目从代码常量升级为**声明式文件**（每题一个 `tasks/<id>.toml`），支持批量执行、局部重跑、结果聚合。
- **G4**：对现有生产代码**最小化侵入**——录制、落盘、题目解析都做成可选观测层，关掉后行为与今天完全一致。

---

## 2. 现状拓扑

### 2.1 状态文件分布（现状）

| 产物 | 现路径 | 生成处 |
|---|---|---|
| checkpoint.json | `~/.strata/state/checkpoint.json` | `PersistenceManager` via `_default_state_dir()`（`orchestrator.py:657`）|
| audit.jsonl | `~/.strata/audit.jsonl`（`config.audit_log`）| `AuditLogger`（`harness/context.py:138`）|
| trash/ | `~/.strata/trash`（`config.trash_dir`）| 未实现，仅占位字段 |
| context_snapshots/ | `~/.strata/context_snapshots/` | `ContextManager.compress()`（`harness/context.py:111`）|
| 沙箱工作区 | `~/strata-sandbox/` | `SandboxGuard`（当前测试已关闭） |

三处"路径生产"散布在：`core/config.py` 的默认值、`orchestrator._default_state_dir`、`context.ContextManager.compress`。想统一就得把"默认根路径"升格为一等配置项。

### 2.2 LLM I/O 调用链

```
Planner/Adjuster/VisionLocator
  └─> LLMRouter.chat(role, messages)
        └─> OpenAICompatProvider.chat(messages, ...)
              └─> _message_to_openai(msg)  ← 这里图片才 base64 化
                    └─> client.chat.completions.create(...)
```

要落盘的正确拦截点是 **`OpenAICompatProvider.chat` 的输入（仍是 `ChatMessage` + raw bytes 图片）和输出（`ChatResponse`）**。在 `_message_to_openai` 之后拦截会拿到已 base64 的巨串，既占磁盘又不可读。

### 2.3 OSWorld GUI 适配器

- `OSWorldGUIAdapter.capture_screen()` 已经把 `GET /screenshot` 封装好了，返回 PNG bytes。
- 没有 **frame-rate 驱动的录屏**——目前只有 `VisionLocator` 主动截图，间隔不定，时间精度很差。
- 录制必须**独立于 agent 决策流**——agent 卡住 30s 在想 LLM 时，屏幕没事件但我们需要的是"这 30 秒 OSWorld 桌面的连续帧"来判断是否有弹窗/闪屏。

### 2.4 现有测试触点（将被动 broken 的点）

- `tests/test_harness/test_orchestrator.py:38` 用 `monkeypatch.setenv("STRATA_STATE_DIR", ...)` 做隔离——**继续兼容**，只是新配置项加一个环境变量覆盖即可。
- `tests/test_core/test_config.py:91` 校验 `audit_log` 字段解析——需要在 `StrataConfig` 里把 `audit_log` 保留为兼容字段或迁移到 `[paths]`。
- `tests/test_env/test_gui_osworld.py`——HTTP client 已用 `urllib` 做了 mock，录屏功能若基于 `capture_screen` 就不会引入新的测试矩阵。

---

## 3. 演进方案

### 3.1 统一产物根：`./.strata-run/`

**配置层**：`StrataConfig` 新增 `[paths]` 段：

```toml
[paths]
run_root = "./.strata-run"          # 所有动态产物的根
keep_last_runs = 5                  # 自动滚动清理策略；0=无限保留
```

派生路径（**构造一次、注入依赖**，不散在多处）：

```
.strata-run/
├── current -> runs/2026-04-16T21-05-00_<goal-hash>/   # symlink 到本次
└── runs/
    └── 2026-04-16T21-05-00_<goal-hash>/
        ├── checkpoint.json
        ├── audit.jsonl
        ├── context_snapshots/
        ├── llm/
        │   ├── 0001_planner_req.json      # messages (images 抽离)
        │   ├── 0001_planner_resp.json
        │   ├── 0001_planner_img_0.png
        │   └── ...
        ├── screenshots/
        │   └── step_0001_pre.png
        ├── recordings/
        │   └── osworld.mp4
        ├── logs/
        │   ├── agent.stdout.log
        │   └── agent.stderr.log
        └── manifest.json                  # goal / config snapshot / 结果摘要
```

`trash_dir` 保持在 HOME（那是用户级 "回收站"，不是 run 级）——不混进 run 目录。

**兼容策略**：若 `[paths]` 缺省，fallback 到旧默认（`~/.strata`），单元测试生态不炸。

### 3.2 轨迹录像：**放弃 ffmpeg+Xvfb 本地方案**，采用 OSWorld 原生帧流

原因：
- 本机 `ffmpeg` / `Xvfb` 都未安装，agent 本身是 headless，**本地根本没有屏幕可录**。
- OSWorld 容器内已经有 Xvfb，真正要录的是"容器里 agent 操作的那块屏"。
- `GET /screenshot` 已经稳定返回 PNG——最小阻力方案是**在 agent 进程里起一个后台线程**，按固定帧率（默认 2 fps）轮询截图，写入 `recordings/frames/XXXX.png`，run 结束时用 `ffmpeg -r 2 -i frames/%06d.png ... osworld.mp4` 合成。

**两级产物**：
- **必选**：`recordings/frames/` PNG 序列（不依赖 ffmpeg，永远能得到）。
- **可选**：若检测到系统 `ffmpeg`，落 `recordings/osworld.mp4`（方便回放）；没有就打一行 warning。

**新组件**：`TrajectoryRecorder`
- 协议：`start()` / `stop()` / `note_event(kind: str, payload: dict)`。
- 线程：`threading.Thread(daemon=True)` 循环 `capture_screen` + `time.sleep(1/fps)`。
- 事件注入：Orchestrator 在 `task state transition` 时调 `note_event("task", {"id":..,"state":..})`，写到 `events.jsonl`，供回放叠字幕。

**接入点**：`AgentOrchestrator.__init__` 构造；`run_goal` 入口 `start()`、出口 `stop()`；不改 `TaskExecutor`（已有 audit）。

**熔断**：若 `capture_screen` 连续失败 3 次，录制器自动 disable，主流程继续（观测层不能拖垮主流程——与 `AuditLogger` 的 "I/O 失败降级 warning" 策略一致）。

### 3.3 LLM I/O 落盘：`ChatTranscriptSink`

**协议**（新增到 `strata/llm/provider.py` 或 `strata/llm/transcript.py`）：

```text
ChatTranscriptSink(Protocol):
    def record(role: str, messages: Sequence[ChatMessage],
               response: ChatResponse | None,
               error: Exception | None) -> None
```

**实现**：`FileChatTranscriptSink(run_dir: Path)` —— 写 JSON + 图片单独存 PNG。

**集成**：`OpenAICompatProvider.__init__` 可选接收 `sink`；在 `chat()` 的两处出口（成功/失败）各调一次。`LLMRouter` 构造 provider 时把 sink 传入。`AgentOrchestrator` 构造 router 时注入 `FileChatTranscriptSink(current_run.llm_dir)`。

**格式示例** `0003_vision_req.json`：

```json
{"role":"vision","model":"grok-4-1-fast-non-reasoning","ts":1713304812.1,
 "messages":[
   {"role":"system","content":"You are a visual UI locator..."},
   {"role":"user","content":"Find the 'Save' button","images":["0003_vision_img_0.png"]}
 ]}
```

图片**永远旁置 PNG**、JSON 只存相对文件名，避免 JSONL 膨胀成百 MB。

**为什么必须拦截到 bytes 而不是 base64**：人工 debug 时要能直接 `xdg-open 0003_vision_img_0.png`；base64 字符串既看不见、又让 `jq` 刷屏。

### 3.4 OSWorld 题目定义：声明式 `tasks/*.toml`

**格式**（每题一个文件，放 `tasks/` 下）：

```toml
[task]
id = "create-hello-txt"               # 必填，用于 run 目录命名
goal = "Create a file at /tmp/strata_e2e_hello.txt containing the single word: hello"
tags = ["filesystem", "smoke"]
timeout_s = 120                       # 整个 goal 的墙钟上限
max_iterations = 20                   # 覆盖 config.max_loop_iterations

[setup]                               # 可选：run 前在 OSWorld 内执行
commands = [
  "rm -f /tmp/strata_e2e_hello.txt",
]

[verify]                              # 可选：run 后的外部校验（本机 shell）
command = "cat /tmp/strata_e2e_hello.txt"
expected_stdout_regex = "^hello\\n?$"
```

**执行器**：`scripts/run_tasks.py`
- 用法 1（单题）：`uv run python scripts/run_tasks.py tasks/create-hello-txt.toml`
- 用法 2（批量）：`uv run python scripts/run_tasks.py tasks/*.toml`
- 用法 3（按 tag）：`uv run python scripts/run_tasks.py --tag smoke`

**产物**：
- 每题一个 run 目录（3.1 里的结构）。
- 顶层 `reports/<YYYY-MM-DD_HHMM>.json` 聚合：每题 id / goal / verdict / duration / run_dir / error。
- 控制台输出 pytest 风格摘要。

**与 `agent_e2e.py` 的关系**：保留 `agent_e2e.py` 作为"临时单次 smoke"——其实质上只是 `run_tasks.py` 的极简子集；不删旧脚本，不新增测试负担。

---

## 4. 验证策略（分层）

| 层级 | 本次新增 | 捕获的错误 |
|---|---|---|
| **L0 类型** | `RunDirLayout` frozen dataclass（路径派生）；`TrajectoryRecorder` Protocol；`ChatTranscriptSink` Protocol；`TaskFile` dataclass | 路径字段漏传、接口契约漂移 |
| **L1 契约** | `RunDirLayout.__post_init__`：`run_root` 必须绝对路径；`TaskFile.load`：`goal` 非空、`id` 符合文件名字符集 | 运行时配置谬误 |
| **L2 属性** | 无新增 Hypothesis（本次都是 I/O 编排，不存在"通用命题"）| — |
| **L3 示例** | `test_paths/test_run_layout.py`：run_dir 创建幂等、symlink 指向正确、`keep_last_runs` GC 单调；`test_llm/test_transcript_sink.py`：消息序列化/图片旁置/失败时仍记录 | 具体场景回归 |
| **L4 lint** | `ruff check` / `ruff format --check` | 风格与常见陷阱 |

**关键不测的东西**（刻意放弃）：
- `TrajectoryRecorder` 线程调度不做 CI 测试——起线程、轮询 `capture_screen`、合成 mp4 都强烈依赖 OSWorld live server，已归入手工 smoke（`scripts/run_tasks.py tasks/*.toml` 跑通即验收）。
- `run_tasks.py` 的 `verify` shell 执行不做 mock 单测——它就是 `subprocess.run` + regex，单测价值极低。

---

## 5. 爆炸半径

**会改的文件**（预计 ~10 个）：

| 文件 | 改动类型 | 原因 |
|---|---|---|
| `strata/core/config.py` | add field | `[paths]` 段、`PathsConfig` dataclass、`StrataConfig.paths` |
| `strata/harness/orchestrator.py` | refactor init | 用 `RunDirLayout` 替代 `_default_state_dir`，注入 sinks/recorder |
| `strata/harness/persistence.py` | signature | `PersistenceManager(state_dir)` 保持不变，构造方改拿 `layout.checkpoint_path` |
| `strata/harness/context.py` | 微调 | `ContextManager.compress(snapshot_dir)` 已是参数，orchestrator 传入 `layout.context_dir` |
| `strata/llm/provider.py` | add param | `OpenAICompatProvider.__init__(..., sink=None)` |
| `strata/llm/router.py` | wire sink | 构造 provider 时透传 sink |
| `strata/paths.py` | **新建** | `RunDirLayout` + GC helper |
| `strata/observability/recorder.py` | **新建** | `TrajectoryRecorder` + `FileChatTranscriptSink` |
| `strata/observability/__init__.py` | **新建** | package |
| `scripts/run_tasks.py` | **新建** | 批量执行器 |
| `tasks/` | **新建目录** | 样例 3-5 题 |
| `.gitignore` | add line | `.strata-run/`、`reports/`（见 3.4） |
| `config.toml` | add section | `[paths]` 默认值 |

**不会改**：
- `tests/strategies.py`——所有新组件要么是 I/O（不适合 Hypothesis），要么是结构化配置（类型层就够）。
- `tests/test_harness/test_orchestrator.py` 的 `STRATA_STATE_DIR` 环境变量路径——新 layout 保留此变量作为测试隔离后门。
- `EnvironmentBundle` / `TaskExecutor` / `Planner` 的公开 API——观测层全靠构造注入，不改签名。

---

## 6. 约束与防线

### CONVENTION 硬约束

- **C1：观测层失败绝不升格为 goal 失败**。`TrajectoryRecorder.tick()` 抛异常 → 计数+1，连续 3 次自 disable + stderr warning；`FileChatTranscriptSink.record()` OSError → stderr warning + 丢弃（与现有 `AuditLogger` 完全对齐）。
- **C2：路径派生只在一处**。`RunDirLayout` 是唯一真相源；任何模块都不再 `Path.home() / ".strata"` 或 `os.environ["STRATA_STATE_DIR"]` 自行拼路径。
- **C3：图片永远旁置 PNG**。禁止把 base64 字符串塞进 JSON——debug 可读性 > 磁盘占用。
- **C4：轨迹录像是 OSWorld-only**。`config.osworld.enabled=false` 时，`TrajectoryRecorder` 直接 noop 构造；不去碰本机显示。
- **C5：题目文件是配置不是代码**。禁止在 `tasks/*.toml` 里塞 Python eval；`verify.command` 通过 subprocess 跑，regex 匹配——不 import 用户代码。

### 性能天花板

- 录屏线程默认 **2 fps**（60 帧/30s），PNG ~50 KB/帧 → 一次 30s 的 goal ≈ 3 MB。远小于 LLM transcript（一次 vision 调用一张 full-HD 截图 base64 后 ~3 MB request body）。
- `keep_last_runs=5` 默认值 → 单机磁盘占用天花板约 **100 MB**（实测 goal 级产物通常 5–20 MB/run）。

### 并发假设

- OSWorld GUI 有 `GUILock`——录屏线程**不走 GUILock**（与 `screenshot_without_lock=true` 同款策略），screenshot 是只读操作不会与 agent pyautogui 冲突。
- `FileChatTranscriptSink.record()` 用文件名里的 `{seq:04d}` 单调序号，不依赖全局锁——provider 在单进程内串行 chat，无并发写入风险。

---

## 7. Phase 划分

按依赖顺序，三个 Phase 可以流水线作业：

### Phase P1：路径统一 + 配置扩展

- P1.1：新增 `strata/paths.py` 定义 `RunDirLayout` + `create_run_dir(config) -> RunDirLayout`。
- P1.2：扩展 `StrataConfig` 的 `[paths]` 段，保持向后兼容。
- P1.3：`AgentOrchestrator.__init__` 切换到 `RunDirLayout`；`ContextManager.compress` 默认路径接入。
- P1.4：`.gitignore` 加 `.strata-run/` 与 `reports/`。
- Gate：`mypy --strict . && pytest -q`（老测试用 `STRATA_STATE_DIR` 后门必须继续通过）。

### Phase P2：观测层（transcript + recorder）

- P2.1：`FileChatTranscriptSink` 实现；`OpenAICompatProvider` 接入可选 sink。
- P2.2：`LLMRouter` 透传 sink；`AgentOrchestrator` 构造时注入。
- P2.3：`TrajectoryRecorder` 线程实现 + `ffmpeg` 可选合成。
- P2.4：Orchestrator 的 `run_goal` 入口/出口 start/stop；task state 变更调 `note_event`。
- Gate：手工跑 `scripts/agent_e2e.py`，检查 `.strata-run/runs/<latest>/` 下 `llm/`、`recordings/frames/`、`audit.jsonl` 都有内容。

### Phase P3：题目协议 + 批量执行器

- P3.1：`TaskFile` dataclass + loader，校验契约。
- P3.2：`scripts/run_tasks.py` CLI（按文件名/按 tag/按批量通配）。
- P3.3：样例题目 `tasks/create-hello-txt.toml`、`tasks/list-tmp-count.toml`、`tasks/read-hostname.toml` 从 `agent_e2e.py` 的 `DEFAULT_GOALS` 迁移过来。
- P3.4：`tasks/README.md` 写清题目格式与编写范式。
- Gate：`uv run python scripts/run_tasks.py tasks/*.toml` 三题全绿；报告 JSON 结构完整。

---

## 8. 待决议断言（需你确认）

**Q1：`.strata-run/` 的位置？**
- A) 固定 `./` 工作区根（我倾向这个——`git status` 一眼看见且 ignore 即走）。
- B) 可配置 `paths.run_root`，默认 `./.strata-run`，允许改成 `/var/log/strata/`。

**Q2：`keep_last_runs` 默认值？**
- A) `5`（磁盘友好，debug 够用）← 我的默认提议。
- B) `0`（无限保留，你自己 `rm`）。
- C) `20`（粗犷保留一周）。

**Q3：录屏格式？**
- A) 仅 PNG 序列（任何机器都能跑，合成交给用户）。
- B) PNG + 若系统有 `ffmpeg` 自动合成 mp4 ← 我的提议。
- C) 强制 mp4，没 `ffmpeg` 就报错中止。

**Q4：题目 `[setup].commands` 在哪里跑？**
- A) **本机 shell**（简单，但和 OSWorld 容器解耦了）。
- B) **OSWorld 容器内 `/run_python`**（贴近 benchmark 语义，但要我们先包一个 `exec_shell` endpoint 或用 `subprocess.run` over pyautogui）。
- C) 两者都支持，靠 `target = "host" | "osworld"` 字段区分 ← 我的提议（显式优于约定）。

**Q5：现有 `audit_log` / `trash_dir` 字段要不要迁移？**
- A) 立即迁移进 `[paths]`，旧字段在下版本移除（1 个 breaking）。
- B) 新字段并存，旧字段保留 + DeprecationWarning ← 我的提议（生产路径已有人在用）。
- C) 彻底不动，`audit_log` 继续指 HOME，不进 run 目录。

---

## 9. 执行题目的使用指南（交付物之一）

在方案落地后，你的日常工作流会是：

```bash
# 1. 写一道题
vim tasks/open-firefox-example-com.toml

# 2. 跑这道
uv run python scripts/run_tasks.py tasks/open-firefox-example-com.toml

# 3. 失败了，看录像
ls .strata-run/current/
# → checkpoint.json  audit.jsonl  llm/  recordings/  screenshots/  manifest.json
mpv .strata-run/current/recordings/osworld.mp4      # 有 ffmpeg 时
# 或直接看帧
feh .strata-run/current/recordings/frames/          # 没 ffmpeg 时

# 4. 看 LLM 到底想了什么
jq . .strata-run/current/llm/0001_planner_req.json
xdg-open .strata-run/current/llm/0003_vision_img_0.png

# 5. 看 audit（人类可读摘要）
jq -c '{task_id, action, result}' .strata-run/current/audit.jsonl

# 6. 跑批量
uv run python scripts/run_tasks.py tasks/*.toml --tag filesystem
cat reports/*.json | jq '.tasks[] | select(.verdict=="FAIL")'
```

**写题模板**（`tasks/TEMPLATE.toml`，会在 P3 生成）：

```toml
[task]
id = "<短标识，kebab-case>"                 # 必填
goal = "<自然语言目标>"                      # 必填
tags = ["<分类>"]
timeout_s = 120

[setup]                                      # 可选
target = "host"                              # "host" 或 "osworld"
commands = ["..."]

[verify]                                     # 可选
target = "host"
command = "..."
expected_stdout_regex = "..."
# 或
expected_exit_code = 0
```

---

## 总结

- 测试质量：**无需删除**，主要是少量集成缺口（`__main__`/`scheduler` 失败分支），不在本计划范畴。
- 本次变更是**纯观测层 + 协议层**的扩展，生产代码契约与公开 API 不动。
- 交付后你写一道 OSWorld 题 = 一个 TOML；调试一道题 = 看 `.strata-run/current/` 的录像 + JSON。

**以上为架构草案。如有修正请指出；若无异议（尤其是 §8 的 5 个决议点），回复「确认」，我将生成 `FV_EXECUTION_PLAN.md` 并按 P1→P2→P3 顺序落地。**
