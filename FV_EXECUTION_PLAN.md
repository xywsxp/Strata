# FV 执行计划：OSWorld 调试基础设施

> 决议：
> **Q1**=`paths.run_root` 可配置，默认 `./.strata-run`｜**Q2**=`keep_last_runs=5`｜**Q3'**=C（双轨：in-container ffmpeg + task-boundary keyframe PNGs）｜**Q4**=`target="host"|"osworld"` 双支持｜**Q5**=新字段 + 旧字段并存带 `DeprecationWarning`｜**Q6**=`30` fps。
>
> 对应讨论文档：`PLAN_DISCUSSION.md`。

---

## 全局规则

- 遵循 workspace.mdc：**SPECIFY → IMPLEMENT → VERIFY**，契约先行、类型先行、Hypothesis 次之、示例兜底、ruff 过线。
- Write-Review 物理隔离；熔断 3 次交人类；Phase 完成前 Gate Check：`mypy --strict . && pytest -q && ruff check . && ruff format --check .`。
- 观测层**绝不升格为 goal 失败**——任何 I/O 错误 stderr warning + 降级（与 `AuditLogger` 同策略）。
- 新增组件默认可关，向后兼容：`config.osworld.enabled=false` → `TrajectoryRecorder` noop；`[paths]` 缺省 → fallback `~/.strata/...`。

---

## 工作区拓扑

```
strata/
├── core/
│   └── config.py              [改] 新增 PathsConfig；StrataConfig.paths；兼容旧字段
├── paths.py                   [新] RunDirLayout + gc_old_runs
├── observability/             [新 package]
│   ├── __init__.py
│   ├── transcript.py          [新] ChatTranscriptSink Protocol + FileChatTranscriptSink
│   ├── recorder.py            [新] TrajectoryRecorder（in-container ffmpeg）
│   └── null.py                [新] NullRecorder / NullSink（osworld 关闭时 noop）
├── llm/
│   ├── router.py              [改] 构造时接收 sink；每次 chat 调用回调
│   └── provider.py            [不改] 保持 provider 单一职责
└── harness/
    ├── orchestrator.py        [改] 用 RunDirLayout；注入 sink + recorder
    ├── persistence.py         [不改] 复用 state_dir 构造参数
    └── context.py             [微改] compress() 默认使用 layout.context_dir

tasks/                         [新目录] 声明式题库
├── TEMPLATE.toml
├── create-hello-txt.toml
├── list-tmp-count.toml
├── read-hostname.toml
└── README.md

scripts/
├── agent_e2e.py               [不改] 保留为单次 smoke（被 run_tasks 超集化）
└── run_tasks.py               [新] 批量题目执行器 + 报告聚合

.strata-run/                   [新目录，gitignore]  运行期产物
reports/                       [新目录，gitignore]  跨 run 汇总
```

**不可变 API 边界**（本次不碰）：
- `EnvironmentBundle` / `IGUIAdapter` / `ITerminalAdapter` / `IFileSystemAdapter` 的方法签名。
- `TaskGraph` / `TaskNode` / `ActionResult` 的字段。
- `AgentUI` Protocol。
- `decompose_goal` / `adjust_plan` 的调用契约。

---

## Strategy 状态

**现有（`tests/strategies.py`，不变）**：
- `task_node_strategy`
- `task_graph_strategy`
- `action_result_strategy`
- `screen_region_strategy`
- 其余 value-object strategies

**本次新增**：**无**。新组件全部是 I/O 编排（路径、HTTP、进程、文件），不存在通用命题适合 Hypothesis；L2 层收窄到少数纯函数（`gc_old_runs` 单调性、TaskFile loader 的 round-trip）。

---

## 缺陷 / 技术债收敛

| 现象 | 根因 | 修正态 | 触碰文件 |
|---|---|---|---|
| state 文件散在 `~/.strata/` 难清理 | 默认路径分散在 `config.py` / `orchestrator._default_state_dir` / `context.compress` 三处 | `RunDirLayout` 单一真相 | `strata/paths.py`、`orchestrator.py`、`context.py` |
| agent 失败无法回放屏幕 | 只有轮询 screenshot，无连续视频 | 容器内 ffmpeg x11grab 30 fps h264 + 任务边界 keyframe PNG | `strata/observability/recorder.py`、`orchestrator.py` |
| LLM I/O 黑箱 | base64 图片直接进 HTTP body 后丢失 | `ChatTranscriptSink` 在 router 层拦截，PNG 旁置 | `strata/observability/transcript.py`、`llm/router.py` |
| 题目写死在 `DEFAULT_GOALS` 常量 | 没有声明式格式 | `tasks/*.toml` + `scripts/run_tasks.py` | `tasks/`、`scripts/run_tasks.py` |

---

## Phase 概览

| Phase | 变更域 | 新增验证数 (L0/L1/L2/L3) |
|---|---|---|
| P1 路径统一 | `paths.py` + `config.py` + `orchestrator.py` | 6 / 4 / 1 / 5 |
| P2 观测层 | `observability/*` + `llm/router.py` + `orchestrator.py` | 9 / 4 / 0 / 8 |
| P3 题目协议 | `tasks/` + `scripts/run_tasks.py` | 5 / 4 / 1 / 6 |
| **合计** | | **20 / 12 / 2 / 19** |

---

# Phase P1：路径统一 + 配置扩展

## Step P1.1：`strata/paths.py` —— RunDirLayout

**目标**：把"一次 run 的所有产物路径"收敛成一个 frozen 值对象，构造即目录创建，调用方零拼接。

**新建文件**：`strata/paths.py`

**先读文件清单**：
- `strata/core/config.py`（理解 `StrataConfig` 结构，为 Step P1.2 铺路）
- `strata/harness/orchestrator.py:120-145`（现有 `_default_state_dir` / `AuditLogger(config.audit_log)` 路径语义）
- `strata/harness/context.py:105-128`（`compress` 默认路径）

**API 规格**：

```python
@dataclass(frozen=True)
class PathsConfig:
    run_root: str            # 绝对路径，dataclass 内部已 expanduser
    keep_last_runs: int      # >= 0；0 表示无限保留

@dataclass(frozen=True)
class RunDirLayout:
    run_root: Path
    run_dir: Path            # run_root / "runs" / <timestamp>_<goal-hash8>
    checkpoint_path: Path
    audit_log_path: Path
    context_dir: Path
    llm_dir: Path
    screenshots_dir: Path
    recordings_dir: Path
    logs_dir: Path
    manifest_path: Path

    @classmethod
    def create(cls, paths_config: PathsConfig, goal: str) -> RunDirLayout: ...

    def ensure(self) -> None: ...
    def link_current(self) -> None: ...
    def write_manifest(
        self,
        goal: str,
        config_snapshot: Mapping[str, object],
        started_at: float,
    ) -> None: ...

def gc_old_runs(run_root: Path, keep: int) -> Sequence[Path]: ...
```

**契约**：
- `PathsConfig.__post_init__`：`run_root` 必须是绝对路径；`keep_last_runs >= 0`。
- `RunDirLayout.create.require(goal.strip())`：goal 非空。
- `RunDirLayout.ensure.ensure`：所有子目录都已存在。
- `gc_old_runs.require`：`keep >= 0`；`run_root` 若不存在则视作空目录不报错。
- `gc_old_runs.ensure`：返回的路径都在 `run_root/runs/` 下，且都已删除。

**验证矩阵**：

- **L0** `mypy --strict strata/paths.py`：`Path` vs `str` 不混用；`PathsConfig.run_root` 必须为 `str`（TOML 友好），`RunDirLayout.*` 均为 `Path`。
- **L1** 4 个 icontract 装饰器（见上）。
- **L2** `test_gc_monotonic`：任取 `(N, keep)`，`gc_old_runs` 删除数 == `max(0, N - keep)`；再次调用删除数 == 0（幂等）。
- **L3**：
  - `test_run_layout_create_all_paths_under_run_dir`
  - `test_ensure_creates_directory_tree`
  - `test_write_manifest_round_trip`
  - `test_link_current_symlink_points_to_latest`
  - `test_gc_keeps_latest_k_by_mtime`

**Strategy 变更**：无。

**异常设计**：无新异常——`OSError` 直接透传，调用方（orchestrator）决定是否降级。

**依赖标注**：无依赖；P1.2/P1.3 依赖本 Step。

**Review 检查项**：
- `run_dir` 名包含 goal hash（短 8 字节十六进制）防止同时间戳冲突。
- `ensure()` 对已有目录幂等，不抛异常。
- `gc_old_runs` 按 mtime 排序，删除最老的；`keep=0` 特判为不删除（协议：0 = 不限制）。
- `link_current` 必须是 symlink 而非实际复制；失败（如 Windows、权限）降级为 warning 不抛。

---

## Step P1.2：`StrataConfig[paths]` 扩展

**目标**：把 `[paths]` 段接入 TOML 解析，保留 `audit_log` / `trash_dir` 旧字段带 `DeprecationWarning`。

**修改文件**：`strata/core/config.py`

**先读文件清单**：
- `strata/core/config.py`（全读）
- `tests/test_core/test_config.py`（了解现有断言）
- `config.toml`（当前用户配置）

**API 规格**：

```python
@dataclass(frozen=True)
class PathsConfig:  # 已在 Step P1.1 定义
    run_root: str
    keep_last_runs: int

@dataclass(frozen=True)
class StrataConfig:
    ...
    paths: PathsConfig
    audit_log: str   # 保留但标记 deprecated
    trash_dir: str   # 保留但标记 deprecated
    ...

def _parse_paths(raw: object) -> PathsConfig: ...
```

**契约**：
- `_parse_paths`：`run_root` 字符串非空；`keep_last_runs` 非负整数。
- `load_config` 新 ensure：`result.paths.run_root` 为绝对路径（`_expand` 已处理）。

**验证矩阵**：
- **L0**：`StrataConfig.paths: PathsConfig` mypy 覆盖。
- **L1**：`_parse_paths` 的 2 个参数谓词。
- **L2**：无。
- **L3**：
  - `test_load_config_with_paths_section`
  - `test_load_config_missing_paths_uses_defaults`
  - `test_deprecated_audit_log_still_accepted`（旧 config.toml 仍然可解析）
  - `test_paths_run_root_expands_tilde`

**Strategy 变更**：无。

**异常设计**：`ConfigError`（已有）用于字段非法。

**依赖标注**：依赖 Step P1.1。

**Review 检查项**：
- 缺省值：`run_root="~/.strata/runs-fallback"` ≠ `./.strata-run`——避免 `cwd` 变动影响；`config.toml` 里显式写 `run_root="./.strata-run"` 即为用户选择的 workspace-local 行为。
- 旧 `audit_log` / `trash_dir` 仍然在解析结果里，Phase 结束前不移除（防止用户配置爆炸）。
- `DeprecationWarning` 只在同时提供新字段 `[paths]` 且旧字段非默认时发一次（用 `warnings.warn` 的 `stacklevel=3`）。

---

## Step P1.3：Orchestrator 接入 RunDirLayout

**目标**：`AgentOrchestrator.__init__` 从 `StrataConfig.paths` 构造 layout，替代 `_default_state_dir`；`AuditLogger` / `PersistenceManager` / `ContextManager.compress` 全部改拿 layout 下的路径。

**修改文件**：`strata/harness/orchestrator.py`

**先读文件清单**：
- `strata/harness/orchestrator.py:100-160`（`__init__` 构造链）
- `strata/harness/orchestrator.py:650-684`（`_default_state_dir` 实现）
- `strata/harness/persistence.py:138-160`（`PersistenceManager` 签名）
- `strata/harness/context.py:105-128`（`compress` 默认路径）
- `tests/test_harness/test_orchestrator.py:30-50`（`STRATA_STATE_DIR` 测试隔离后门）

**API 规格**：

```python
class AgentOrchestrator:
    def __init__(
        self,
        config: StrataConfig,
        bundle: EnvironmentBundle,
        ui: AgentUI,
        llm_router: LLMRouter | None = None,
        executor: TaskExecutor | None = None,
        layout: RunDirLayout | None = None,   # 新增可选注入，默认从 config 派生
    ) -> None: ...
```

**契约**：
- `__init__.require`：`config`、`bundle`、`ui` 非 None（已有）。
- `_create_layout_for_run.ensure`：返回 layout 的目录结构已创建。
- 保持 `run_goal.ensure(final_state in COMPLETED|FAILED)` 不变。

**验证矩阵**：
- **L0**：`layout: RunDirLayout | None` 类型正确传递。
- **L1**：新构造契约（见上）。
- **L2**：无。
- **L3**：
  - `test_orchestrator_creates_run_dir_under_paths_root`（新）
  - `test_orchestrator_audit_goes_to_layout_audit_path`（新）
  - `test_orchestrator_checkpoint_goes_to_layout_dir`（改写既有）
  - `test_STRATA_STATE_DIR_env_still_overrides`（回归：后门保留）
  - 全部既有 `test_orchestrator.py` 测试回绿（约 40 个）

**Strategy 变更**：无。

**异常设计**：无新异常；layout 构造失败沿用 `OSError`。

**依赖标注**：依赖 Step P1.1 + P1.2。

**Review 检查项**：
- `_default_state_dir()` 的 `STRATA_STATE_DIR` 环境变量后门**必须保留**，但改为"优先于配置"——测试生态的跨 run 隔离靠它。
- 每次 `run_goal()` 调用生成**新的 `run_dir`**（layout.run_dir 是 per-run 的），但 `checkpoint_path` 要跨 run 保持一致以支持 resume——**决策**：`checkpoint.json` 放在 `run_root` 而非 `run_dir` 下；`run_dir` 只放本次的 audit/llm/recordings。
- goal hash 生成放 layout.create 里；run_id 字符串对外只在 `log_prefix` / manifest.json 里用。
- GC 时机：`run_goal` 成功完成后触发 `gc_old_runs(run_root, keep_last_runs)`；失败不 GC（保留 debug 资料）。

---

## Step P1.4：.gitignore + config.toml 默认值

**目标**：把新目录加 ignore；`config.toml` 给出可用的 `[paths]` 示例。

**修改文件**：`.gitignore`、`config.toml`

**先读文件清单**：`.gitignore`（末尾）、`config.toml`（全读）

**API 规格**：纯配置变更。

**验证矩阵**：
- **L3**：手工验证 `git status` 不报告 `.strata-run/` / `reports/`。

**Strategy 变更**：无。

**依赖标注**：依赖 Step P1.3（目录路径最终确定后才写入默认值）。

**Review 检查项**：
- `.gitignore` 追加 `.strata-run/` + `reports/`，放在 "# Strata runtime artifacts" 注释之后，不和原有 UV/mypy 段混编。
- `config.toml` 的 `[paths]` 段含所有字段 + 中文注释，能作为文档直接被用户参考。

---

# Phase P2：观测层（Transcript + Recorder）

## Step P2.1：`ChatTranscriptSink` + FileChatTranscriptSink

**目标**：定义 sink 协议，实现文件系统落盘版；messages 序列化时把 `images: bytes` 抽成旁置 PNG。

**新建文件**：`strata/observability/__init__.py`、`strata/observability/transcript.py`、`strata/observability/null.py`

**先读文件清单**：
- `strata/llm/provider.py`（`ChatMessage` / `ChatResponse` 结构，尤其 `images: Sequence[bytes]`）
- `strata/harness/context.py:138-183`（`AuditLogger` 的 I/O 失败降级范式）

**API 规格**：

```python
@runtime_checkable
class ChatTranscriptSink(Protocol):
    def record(
        self,
        role: str,
        messages: Sequence[ChatMessage],
        response: ChatResponse | None,
        error: Exception | None,
    ) -> None: ...

class FileChatTranscriptSink:
    def __init__(self, out_dir: Path) -> None: ...
    def record(...) -> None: ...

class NullTranscriptSink:
    def record(...) -> None: ...  # 完全 noop
```

**契约**：
- `FileChatTranscriptSink.__init__.require`：`out_dir` 路径存在或可创建。
- `record.require`：`role` 非空字符串；`messages` 非空。
- `record.ensure`：文件名 `<seq:04d>_<role>_req.json` 序号单调递增；每张 `msg.images[i]` 旁置为 `<seq>_<role>_img_<i>.png`；JSON 里 `images` 字段是 PNG 相对路径字符串数组。

**验证矩阵**：
- **L0**：Protocol 运行时检查；`NullTranscriptSink` 是否满足 `ChatTranscriptSink` 由 `isinstance(.., ChatTranscriptSink)` 在测试里断言。
- **L1**：`__init__` + `record` 4 个契约。
- **L2**：无（序列号单调性用 L3 例子即可）。
- **L3**：
  - `test_sink_writes_req_and_resp_json_files`
  - `test_sink_extracts_images_to_png_siblings`
  - `test_sink_records_error_when_response_is_none`
  - `test_sink_osError_silently_warns_and_continues`
  - `test_null_sink_implements_protocol`

**Strategy 变更**：无。

**异常设计**：
- sink 内部 OSError → stderr warning + 吞掉（与 `AuditLogger` 完全一致）。
- 序列号争用用 `itertools.count()` + `threading.Lock()`——单进程并发安全。

**依赖标注**：无依赖。

**Review 检查项**：
- **图片必须是原始 bytes 落盘，禁止 base64 JSON 内联**——discord 级别的 debug 体验差异。
- JSON 里 `ChatResponse.usage` 原样保留（方便做 cost 报表）。
- `FileChatTranscriptSink` 必须幂等：重复 `record` 不抢占同一 seq。
- 对 `error is not None` 情况也写 `_req.json`，但 `_resp.json` 替换为 `_err.json` 存异常类型和 repr。

---

## Step P2.2：`LLMRouter` 接入 sink

**目标**：router 拥有 sink，在每个 `plan/ground/see/search` 调用的前后/异常路径调用 `sink.record`。

**修改文件**：`strata/llm/router.py`

**先读文件清单**：
- `strata/llm/router.py`（全读，只 88 行）
- `strata/llm/provider.py:117-187`（chat 的成功 / 异常出口）

**API 规格**：

```python
class LLMRouter:
    def __init__(
        self,
        config: StrataConfig,
        sink: ChatTranscriptSink | None = None,  # 新增，默认 NullTranscriptSink
    ) -> None: ...

    # plan/ground/see/search 内部包一层 try/except，在出口调 sink.record
```

**契约**：
- `__init__.require`：`config.roles` 全部命中 providers（已有）。
- 新 `ensure`：`self._sink` 恒非 None（默认 NullTranscriptSink）。

**验证矩阵**：
- **L0**：签名 mypy 覆盖。
- **L1**：`__init__.ensure`。
- **L3**：
  - `test_router_calls_sink_on_success`
  - `test_router_calls_sink_on_transient_error`
  - `test_router_calls_sink_on_permanent_error`（重点：异常类型要透传给 sink 再重新 raise）
  - `test_router_defaults_to_null_sink`
  - 既有 `test_llm/test_router.py` 全部回绿

**Strategy 变更**：无。

**异常设计**：
- Provider 抛 `LLMAPIError` → router **先** `sink.record(role, messages, None, exc)` **再** re-raise。顺序不能反，否则异常丢失消息。

**依赖标注**：依赖 Step P2.1。

**Review 检查项**：
- Provider 层**不改**——保持 `chat()` 单一职责。sink 是 router 关心的事情。
- Router 的 4 个方法包装必须 DRY：抽一个 `_dispatch(role, messages, **kwargs)` 私有方法，四个 public 方法只是 thin wrapper。
- 并发：LLMRouter 在单进程内的 orchestrator 单线程调用，无需加锁；但 `FileChatTranscriptSink` 自身要 thread-safe（见 Step P2.1）。

---

## Step P2.3：`TrajectoryRecorder` 双轨录制

**目标**：启动时在 OSWorld 容器内 spawn `ffmpeg x11grab`；停止时 SIGINT + 拉 mp4 到 host；中途可打 task-boundary keyframe PNG + events.jsonl。

**新建文件**：`strata/observability/recorder.py`

**先读文件清单**：
- `strata/env/gui_osworld.py`（全读，理解 `_OSWorldHTTPClient` / `_run_python` / `capture_screen`）
- `PLAN_DISCUSSION.md` §3.2（最终方案）
- `strata/core/config.py`（`OSWorldConfig` 结构）

**API 规格**：

```python
@runtime_checkable
class TrajectoryRecorder(Protocol):
    def start(self, run_id: str) -> None: ...
    def stop(self) -> None: ...
    def note_keyframe(self, label: str) -> None: ...
    def note_event(self, kind: str, payload: Mapping[str, object]) -> None: ...

class OSWorldFFmpegRecorder:
    def __init__(
        self,
        osworld: OSWorldConfig,
        gui: IGUIAdapter,
        out_dir: Path,
        fps: int = 30,
    ) -> None: ...

class NullRecorder:
    # 全 noop 实现，osworld.enabled=false 时使用
    ...
```

**关键实现约束**：
- HTTP 访问复用新增的 `_OSWorldHTTPClient.post_form_get_bytes(path, fields)`（见下 "异常设计"）；避免 `OSWorldGUIAdapter._client` 被外部模块直接戳。
- `start()` 调用链：
  1. `POST /run_python` 下发 `rm -rf /tmp/strata_rec`。
  2. `POST /run_python` 下发 `subprocess.Popen(["ffmpeg", "-f", "x11grab", ..., f"/tmp/strata_rec/{run_id}.mp4"])`，写 `.pid` 到 `/tmp/strata_rec/{run_id}.pid`。
- `stop()` 调用链：
  1. `POST /run_python` 下发 `os.kill(pid, signal.SIGINT); time.sleep(2)` 等 mp4 flush。
  2. `POST /file` form-encoded 拉 mp4 bytes，写到 `out_dir / "osworld.mp4"`。
  3. `POST /run_python` 下发 `rm -rf /tmp/strata_rec`。
  4. 写 `events.jsonl`。
- `note_keyframe(label)`：`gui.capture_screen()` → `out_dir / screenshots / f"{label}.png"`；**不走 GUILock**（screenshot 是只读）。
- `note_event(kind, payload)`：append 一行 JSON 到 `out_dir / events.jsonl`，字段 `{ts, kind, payload}`。

**契约**：
- `OSWorldFFmpegRecorder.__init__.require`：`osworld.enabled == True`；`fps in [1, 60]`。
- `start.require`：`run_id` 符合 `^[a-zA-Z0-9_-]+$`（禁止 shell 注入）。
- `stop.ensure`：如果 `start` 曾调用成功，则 `out_dir / "osworld.mp4"` 存在（大小可以是 0，比如 ffmpeg 立刻失败，但文件必须落盘以示 "尝试过"）。
- `note_event.require`：`kind` 非空。

**验证矩阵**：
- **L0**：Protocol `TrajectoryRecorder` 的 `NullRecorder` / `OSWorldFFmpegRecorder` 结构性实现覆盖。
- **L1**：4 个契约。
- **L3**（全部 mock `_OSWorldHTTPClient`）：
  - `test_recorder_start_spawns_ffmpeg_via_run_python`
  - `test_recorder_stop_sends_sigint_and_downloads_mp4`
  - `test_recorder_stop_always_writes_mp4_file_even_on_http_error`
  - `test_keyframe_writes_png_under_screenshots_dir`
  - `test_note_event_appends_jsonl`
  - `test_null_recorder_is_noop_under_protocol`
  - `test_recorder_refuses_run_id_with_shell_chars`
  - `test_recorder_handles_consecutive_http_failures_by_disabling_itself`

**Strategy 变更**：无。

**异常设计**：
- 新增 `RecorderError(HarnessError)`：构造失败（osworld 连接不通）时抛出，但 orchestrator 层捕获后降级 `NullRecorder`。
- **I/O 连续失败 3 次后自 disable**——内部计数器 `_failures`；之后 `start/stop/note_*` 全部 early-return。
- `_OSWorldHTTPClient.post_form_get_bytes` 新方法：构造 `urllib.parse.urlencode(fields).encode()` 发 `application/x-www-form-urlencoded` POST，返回 body bytes。本次在 `strata/env/gui_osworld.py` 扩展该私有类（非公开 API 改动）。

**依赖标注**：依赖 Step P1.1（拿 `out_dir=layout.recordings_dir`）。

**Review 检查项**：
- ffmpeg 命令**必须 preset=ultrafast + pix_fmt=yuv420p**，否则 h264 默认 preset 慢到录屏卡顿，且 yuv420p 是播放器兼容性底线。
- `run_id` 里禁止 `"` `'` `$` `\\` 等 shell 元字符——`_run_python` 虽然是 Python eval 不是 shell，但我们构造 Python 字符串字面量时仍需转义；简单起见用白名单正则。
- `SIGINT` 不是 `SIGTERM`——实测 SIGTERM 会产生无 moov atom 的损坏 mp4，SIGINT 会让 ffmpeg 走正常 flush 路径。
- `stop()` **必须幂等**：重复调用不抛异常（连续 Gate Check / KeyboardInterrupt 场景）。
- 30 fps × 1080p × libx264 ultrafast 实测 ~10 MB/分钟——goal 默认 `timeout_s=120` 即 20 MB/run；`keep_last_runs=5` → 磁盘占用约 100 MB 天花板（符合讨论文档 §6 的预算）。

---

## Step P2.4：Orchestrator 接入 sink + recorder

**目标**：把 P2.1–P2.3 的组件注入 `AgentOrchestrator`，在 `run_goal` 生命周期挂钩点调用。

**修改文件**：`strata/harness/orchestrator.py`

**先读文件清单**：
- `strata/harness/orchestrator.py`（全读）
- Step P1.3 的交付物

**API 规格**：

```python
class AgentOrchestrator:
    def __init__(
        self,
        config: StrataConfig,
        bundle: EnvironmentBundle,
        ui: AgentUI,
        llm_router: LLMRouter | None = None,
        executor: TaskExecutor | None = None,
        layout: RunDirLayout | None = None,
        transcript_sink: ChatTranscriptSink | None = None,   # 新
        recorder: TrajectoryRecorder | None = None,          # 新
    ) -> None: ...
```

**生命周期挂钩**：
```
run_goal(goal):
    layout = self._layout or RunDirLayout.create(config.paths, goal)
    sink   = self._sink or FileChatTranscriptSink(layout.llm_dir) if enabled else NullTranscriptSink
    recorder = self._recorder or (
        OSWorldFFmpegRecorder(config.osworld, bundle.gui, layout.recordings_dir, fps=30)
        if config.osworld.enabled else NullRecorder()
    )
    recorder.start(run_id)
    try:
        ... 原有流程 ...
        recorder.note_event("plan_ready", {"tasks": len(graph.tasks)})
        ... run_tasks ...
        recorder.note_event("task_state", {"id": tid, "state": new_state})
        ... 每个 task 执行前 / 后 ...
        recorder.note_keyframe(f"step_{seq:04d}_pre")
        ...
    finally:
        recorder.stop()
        layout.write_manifest(goal, config_snapshot, started_at)
        if result.final_state == "COMPLETED":
            gc_old_runs(layout.run_root, config.paths.keep_last_runs)
```

**契约**：不变。

**验证矩阵**：
- **L3**（既有 + 新增）：
  - `test_orchestrator_starts_and_stops_recorder`（用 mock recorder 断言 start/stop 调用次数）
  - `test_orchestrator_calls_sink_through_router`（间接：注入 router with mock sink）
  - `test_orchestrator_fails_gracefully_when_recorder_errors`
  - `test_orchestrator_writes_manifest_on_run_end`
  - `test_orchestrator_runs_gc_only_on_success`
  - 全部既有 `test_harness/test_orchestrator.py` 回绿

**Strategy 变更**：无。

**异常设计**：
- `recorder.start()` 抛 `RecorderError` → 不影响主流程，替换为 `NullRecorder` 继续；stderr warning。
- `recorder.stop()` 抛异常 → 吞掉，`finally` 继续 `write_manifest` / `gc`。

**依赖标注**：依赖 P2.1 + P2.2 + P2.3。

**Review 检查项**：
- `run_goal` 的 `finally` 块是**全链路 best-effort**：`recorder.stop`、`write_manifest`、`gc_old_runs` 三者互不影响，任一失败不能遮盖原始 goal 结果。
- `keyframe` 最少量：只在 `task_state transition` 且 `new_state in (SUCCEEDED, FAILED)` 时拍；不在每个 action 前都拍（30 fps 视频已经覆盖了）。
- 注入优先级：构造参数 > 默认构造。测试只需传 mock 即可绕过 HTTP/ffmpeg。

---

# Phase P3：题目协议 + 批量执行器

## Step P3.1：`TaskFile` 数据类 + TOML loader

**目标**：定义声明式题目格式的 Python 对应。

**新建文件**：`strata/tasks.py`

**先读文件清单**：
- `strata/core/config.py`（TOML 解析范式）

**API 规格**：

```python
@dataclass(frozen=True)
class SetupSpec:
    target: Literal["host", "osworld"]
    commands: Sequence[str]

@dataclass(frozen=True)
class VerifySpec:
    target: Literal["host", "osworld"]
    command: str
    expected_stdout_regex: str | None
    expected_exit_code: int | None

@dataclass(frozen=True)
class TaskFile:
    id: str
    goal: str
    tags: Sequence[str]
    timeout_s: float
    max_iterations: int | None
    setup: SetupSpec | None
    verify: VerifySpec | None
    source_path: Path

    @classmethod
    def load(cls, path: Path) -> TaskFile: ...
    @classmethod
    def load_many(cls, paths: Sequence[Path]) -> Sequence[TaskFile]: ...
```

**契约**：
- `TaskFile.load.require`：`path` 存在且是 `.toml`。
- `TaskFile.__post_init__` 或 loader 内：
  - `id` 匹配 `^[a-z0-9][a-z0-9-]{0,62}$`。
  - `goal` 非空。
  - `timeout_s > 0`。
  - `verify.expected_stdout_regex` 或 `expected_exit_code` 至少一个。
- `load_many.ensure`：返回列表中 `id` 唯一。

**验证矩阵**：
- **L0**：`Literal["host","osworld"]` 覆盖。
- **L1**：4 个 loader 契约。
- **L2**：`test_taskfile_roundtrip`：任意 `TaskFile` 序列化回 TOML 再 `load()` 等值（Hypothesis 策略见下）。
- **L3**：
  - `test_load_minimal_task`
  - `test_load_full_task_with_setup_and_verify`
  - `test_rejects_bad_id_character`
  - `test_rejects_empty_goal`
  - `test_load_many_detects_duplicate_id`
  - `test_verify_spec_requires_at_least_one_expectation`

**Strategy 变更**：`tests/strategies.py` 新增 `task_file_strategy`——仅限 L2 round-trip 用。

**异常设计**：新增 `TaskFileError(StrataError)`：TOML 解析失败 / 字段非法。

**依赖标注**：无依赖。

**Review 检查项**：
- `source_path` 字段：`load()` 填充，后续 `run_tasks` 报告里引用。
- `SetupSpec.target` 和 `VerifySpec.target` 字段默认值：`"host"`（host shell 是最简单的校验方式）。
- 不引入 YAML，TOML 足够；保持与 `config.toml` 工具链一致。

---

## Step P3.2：`scripts/run_tasks.py` 批量执行器

**目标**：CLI 入口，对一批题目跑 `setup → run_goal → verify`，写聚合报告。

**新建文件**：`scripts/run_tasks.py`

**先读文件清单**：
- `scripts/agent_e2e.py`（复用 `AutoUI` / `_summarize` 模式）
- Step P3.1 交付物

**API 规格**：

```python
def main(argv: Sequence[str]) -> int: ...

# 内部
def _run_single(task: TaskFile, config: StrataConfig, bundle: EnvironmentBundle) -> TaskReport: ...
def _run_shell(target: Literal["host","osworld"], command: str, bundle: EnvironmentBundle, timeout: float) -> ShellResult: ...
def _write_report(tasks: Sequence[TaskReport], out_dir: Path) -> Path: ...

@dataclass(frozen=True)
class TaskReport:
    task_id: str
    goal: str
    verdict: Literal["PASS", "FAIL", "ERROR", "TIMEOUT"]
    duration_s: float
    run_dir: str
    setup_output: str | None
    verify_output: str | None
    error: str | None
```

**CLI**：
```bash
uv run python scripts/run_tasks.py tasks/foo.toml tasks/bar.toml
uv run python scripts/run_tasks.py 'tasks/*.toml'
uv run python scripts/run_tasks.py --tag smoke
uv run python scripts/run_tasks.py --config ./config.toml --report-dir reports/
```

**契约**：
- `main.require`：至少提供一个 task 文件路径或 `--tag`。
- `_run_shell.require`：`timeout > 0`；`target` 合法。
- `_write_report.ensure`：输出文件为 JSON，包含所有 `TaskReport` 字段。

**验证矩阵**：
- **L0**：CLI 参数用 `argparse` + typed namespace。
- **L1**：3 个契约。
- **L3**：
  - `test_run_shell_host_executes_and_matches_regex`
  - `test_run_shell_osworld_dispatches_to_run_python`（mock OSWorld）
  - `test_single_task_end_to_end_with_mock_orchestrator`
  - `test_report_json_schema`
  - `test_glob_expansion_finds_task_files`
  - `test_tag_filter_selects_matching_tasks`

**Strategy 变更**：无（复用 Step P3.1 的 strategy）。

**异常设计**：复用 `TaskFileError` + `HarnessError`；CLI 层翻译为 exit code（0=全通过，1=有失败，2=ERROR）。

**依赖标注**：依赖 Step P3.1 + Phase P2（run_tasks 依赖 orchestrator 能产出轨迹包）。

**Review 检查项**：
- **每题独立 orchestrator 实例**：`config.toml` 只加载一次，但每道题要新 `RunDirLayout` + 新 audit logger，避免跨题污染。
- `setup` 失败 → 题目 verdict=`ERROR`，不进入 `run_goal`；`verify` 失败 → verdict=`FAIL`。
- OSWorld `setup/verify` 用 `_run_python` 跑；host 用 `subprocess.run` 带 timeout。
- Report JSON 格式要兼容 `jq` 过滤（扁平数组 + 每项一 object）；不嵌套。

---

## Step P3.3：样例题目 + TEMPLATE

**目标**：从 `agent_e2e.DEFAULT_GOALS` 迁移三题；提供模板。

**新建文件**：
- `tasks/TEMPLATE.toml`
- `tasks/create-hello-txt.toml`
- `tasks/list-tmp-count.toml`
- `tasks/read-hostname.toml`

**API 规格**：纯数据文件。

**验证矩阵**：
- **L3**：`test_sample_tasks_parse_with_taskfile_load`（pytest 断言 `tasks/*.toml` 全部能被 `TaskFile.load` 解析）。

**Strategy 变更**：无。

**依赖标注**：依赖 Step P3.1。

**Review 检查项**：
- 三题 `verify` 要真实可校验：
  - `create-hello-txt`：`verify.command="cat /tmp/strata_e2e_hello.txt"`、`expected_stdout_regex="^hello\\n?$"`。
  - `list-tmp-count`：`verify` 走 host shell `ls /tmp | wc -l`，但这题无法 deterministic 校验（跟 OSWorld 当前 `/tmp` 状态耦合），改为**仅看 goal 是否 COMPLETED**，`verify` 段省略。
  - `read-hostname`：`verify.command="cat /etc/hostname"`、`expected_exit_code=0`。
- `TEMPLATE.toml` 含所有字段 + 注释，能 `cp` 即改。

---

## Step P3.4：`tasks/README.md`

**目标**：写题教程——格式、示例、执行命令、debug 流程。

**新建文件**：`tasks/README.md`

**内容大纲**：
1. 题目文件格式（逐字段说明）。
2. 两种 target 的差异（host vs osworld）。
3. 编写范式：先写 goal；再决定是否需要 setup（避免跨题污染）；再写 verify（deterministic）。
4. 执行命令三种（单题 / 批量 / 按 tag）。
5. Debug 流程：看 `.strata-run/current/recordings/osworld.mp4`、`llm/*.json`、`audit.jsonl`、`events.jsonl`。
6. FAQ：如何给某题临时加长 timeout；如何只重跑上次失败的题。

**验证矩阵**：
- **L3**：无自动化测试（文档）。手工验收：另一个不熟悉项目的读者能照做跑通一道自定义题。

**Strategy 变更**：无。

**依赖标注**：依赖 P3.1–P3.3（需要所有字段和命令都固化后再写文档）。

**Review 检查项**：不写死任何版本号；命令全部可复制粘贴。

---

## 验证矩阵汇总

| Module | 新增 L0 | 新增 L1 | 新增 L2 | 新增 L3 | 合计 |
|---|---|---|---|---|---|
| `strata/paths.py` | 3 | 4 | 1 | 5 | 13 |
| `strata/core/config.py` | 1 | 2 | 0 | 4 | 7 |
| `strata/harness/orchestrator.py` | 1 | 1 | 0 | 5 | 7 |
| `strata/observability/transcript.py` | 3 | 4 | 0 | 5 | 12 |
| `strata/llm/router.py` | 1 | 1 | 0 | 4 | 6 |
| `strata/observability/recorder.py` | 3 | 4 | 0 | 8 | 15 |
| `strata/tasks.py` | 2 | 4 | 1 | 6 | 13 |
| `scripts/run_tasks.py` | 3 | 3 | 0 | 6 | 12 |
| **合计** | **17** | **23** | **2** | **43** | **85** |

---

## 执行流水线

```
Phase P1:
  P1.1W → P1.1R → [Retry≤3] → Gate (mypy + pytest) → Commit on phase/8-runinfra
  P1.2W → P1.2R → [Retry≤3] → Gate → Commit
  P1.3W → P1.3R → [Retry≤3] → Gate → Commit
  P1.4W → P1.4R → Gate → Commit
  Phase Gate: full mypy --strict . && pytest -q && ruff check . && ruff format --check .
  Push phase/8-runinfra

Phase P2:
  P2.1W → P2.1R → [Retry≤3] → Gate → Commit
  P2.2W → P2.2R → [Retry≤3] → Gate → Commit
  P2.3W → P2.3R → [Retry≤3] → Gate → Commit
  P2.4W → P2.4R → [Retry≤3] → Gate → Commit
  Phase Gate: full stack + 手工 smoke: uv run python scripts/agent_e2e.py，验收
              .strata-run/runs/<latest>/{recordings/osworld.mp4, llm/*.json, events.jsonl} 齐全
  Push

Phase P3:
  P3.1W → P3.1R → [Retry≤3] → Gate → Commit
  P3.2W → P3.2R → [Retry≤3] → Gate → Commit
  P3.3W → P3.3R → Gate → Commit
  P3.4W → P3.4R → Gate → Commit
  Phase Gate: full stack + 手工 smoke: uv run python scripts/run_tasks.py tasks/*.toml
              三题全 PASS，reports/*.json 结构完整
  Push；合入 main
```

**熔断策略**（workspace 规则）：
- 单 Step 的 Write→Review 循环 ≤ 3 次；第 4 次失败交人类介入。
- 任何 Phase Gate 失败先定位到具体 Step 回滚 commit，不推进下一 Phase。
- 观测层 Phase（P2）允许**只接线不验证**降级：如果 live OSWorld 偶发网络抖动导致 smoke 失败 ≥ 2 次，暂停 Phase，检查 `audit.jsonl`——**观测层 bug 永远比底层连接 bug 优先级低**。

---

## 交付验收标准（整个计划完成后）

1. **`rm -rf .strata-run/` 一键清理**所有运行期产物；`git status` 干净。
2. **任意一题失败后**可以从 `.strata-run/current/recordings/osworld.mp4` 回放；`jq . .strata-run/current/llm/*_req.json` 看到完整 prompt 和图片文件名。
3. **`uv run python scripts/run_tasks.py tasks/*.toml`** 退出码反映 PASS/FAIL 情况；`reports/<时间戳>.json` 聚合结果可以 `jq` 过滤。
4. **向后兼容**：旧 `~/.strata/config.toml` 不含 `[paths]` 段的用户能直接 upgrade，不改配置也能跑——只是产物仍落旧路径，新 feature 需要显式启用。
5. **`mypy --strict .`** 零错误；**`pytest -q`** 全绿（预计 ~500 测试）；**`ruff check .`** 零警告。
