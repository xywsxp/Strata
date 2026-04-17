# FV 执行计划：OSWorld 解耦 + 端到端验证通路

> 决议：
> **Q1**=pyautogui 暂不加依赖（headless 服务器，GUI 全走 OSWorld）｜**Q2**=`os_type` 保留在 `[osworld]`，非 OSWorld 从 `platform.system()` 推断｜**Q3**=LLM 失败直接退出｜**Q4**=N/A（headless）｜**Q5**=OSWorld 已启动，测试自动探测连接｜**Q6**=`target="osworld"` 通过 `POST /execute` 在容器内执行｜**Q7**=`create-hello-txt` + `read-hostname` 必须 PASS。
>
> A11y 层正式 ABANDONED：`IA11yAdapter` 设计不实现，感知层永久纯 VLM。
>
> 对应讨论文档：`PLAN_DISCUSSION.md`。

---

## 全局规则

- 遵循 workspace.mdc：**SPECIFY → IMPLEMENT → VERIFY**，契约先行、类型先行、Hypothesis 次之、示例兜底、ruff 过线。
- Write-Review 物理隔离；熔断 3 次交人类；Phase 完成前 Gate Check：`mypy --strict . && pytest -q && ruff check . && ruff format --check .`。
- 观测层**绝不升格为 goal 失败**——任何 I/O 错误 stderr warning + 降级。
- LLM health check 失败 → **直接退出**（与观测层策略相反：LLM 不可用 = agent 注定失败，不降级）。

---

## 工作区拓扑

```
strata/
├── core/
│   └── config.py                [微改] os_type 默认值逻辑
├── env/
│   ├── protocols.py             [不改] IGUIAdapter 等 5 Protocol
│   ├── osworld_client.py        [新] 提取 _OSWorldHTTPClient + OSWorldRawClient
│   ├── gui_osworld.py           [改] import 路径改为 osworld_client
│   ├── factory.py               [改] 清晰双路 + 错误消息
│   └── linux/gui.py             [改] 错误消息改善（不再暗示"就用 OSWorld"）
├── observability/
│   └── recorder.py              [改] 注入 RemoteCodeRunner Protocol
├── harness/
│   └── orchestrator.py          [改] os_type 解耦；recorder 构造解耦
├── health.py                    [新] LLM / OSWorld / GUI 健康检查
└── __main__.py                  [改] 启动时调 health check

scripts/
├── run_tasks.py                 [改] 补全 target="osworld" 路径
└── agent_e2e.py                 [改] 加 health check

tasks/
├── create-hello-txt.toml        [改] target → osworld
├── read-hostname.toml           [改] target → osworld
└── list-tmp-count.toml          [不改] 无 verify

tests/
├── conftest.py                  [改] 添加 OSWorld auto-detect fixture
├── e2e/
│   ├── conftest.py              [改] 添加 osworld 连接 fixture
│   ├── test_live_llm.py         [改] 扩展覆盖
│   ├── test_osworld_pipeline.py [改] 加 health check 测试
│   └── test_e2e_tasks.py        [新] 端到端题目执行测试
├── test_env/
│   └── test_osworld_client.py   [新] 提取后的 HTTP client 单元测试
└── test_health.py               [新] health check 单元测试
```

**不可变 API 边界**（本次不碰）：
- `EnvironmentBundle` / `IGUIAdapter` / `ITerminalAdapter` / `IFileSystemAdapter` 的方法签名。
- `TaskGraph` / `TaskNode` / `ActionResult` 的字段。
- `AgentUI` Protocol。
- `decompose_goal` / `adjust_plan` 的调用契约。
- `ChatMessage` / `ChatResponse` / `LLMProvider` Protocol。

---

## Strategy 状态

**现有（`tests/strategies.py`，不变）**：
- `st_task_node` / `st_task_graph`
- `st_sandbox_path` / `st_coordinate` / `st_command_result`
- `st_action_name` / `st_primitive_task_node` / `st_invalid_primitive_task`
- `st_failing_sequence` / `st_deterministic_mock_executor`

**本次新增**：**无**。新组件全部是 I/O 编排（HTTP、进程、连接验证），不存在通用命题适合 Hypothesis。

---

## 缺陷 / 技术债收敛

| 现象 | 根因 | 修正态 | 触碰文件 |
|---|---|---|---|
| `osworld.enabled=false` 时 agent 无法启动 | `LinuxGUIAdapter.__init__` 直接抛异常，factory 无降级 | 错误消息改善；headless 场景文档化 | `env/linux/gui.py`、`env/factory.py` |
| `recorder.py` 反向依赖 `gui_osworld._OSWorldHTTPClient` | 观测层 import env 具体实现 | `RemoteCodeRunner` Protocol 注入 | `observability/recorder.py` |
| `orchestrator._plan` 用 `config.osworld.os_type` | 核心循环对 OSWorld 有语义依赖 | 从 `platform.system()` 推断，OSWorld 时用 config 覆盖 | `harness/orchestrator.py` |
| `run_tasks.py` 忽略 `target="osworld"` | `_run_shell_host` 是唯一路径 | 新增 `_run_shell_osworld` 通过 `POST /execute` | `scripts/run_tasks.py` |
| 所有 task 文件 `target="host"` | agent 操作在容器内，host verify 看不到容器文件 | 改为 `target="osworld"` | `tasks/*.toml` |
| 启动无 health check | 连接错误延迟到首次操作 | `strata/health.py` 启动时验证 | `health.py`、`__main__.py` |
| 所有测试全 mock | 从未 live 验证 | 扩展 e2e 测试 + 新 marker | `tests/e2e/*` |

---

## Phase 概览

| Phase | 变更域 | 新增验证数 (L0/L1/L2/L3) |
|---|---|---|
| P1 连接验证层 | `health.py` + `__main__.py` + `conftest.py` | 4 / 3 / 0 / 8 |
| P2 端到端通路 | `run_tasks.py` + `tasks/*.toml` + `test_e2e_tasks.py` | 3 / 2 / 0 / 7 |
| P3 OSWorld 解耦 | `osworld_client.py` + `recorder.py` + `orchestrator.py` + `factory.py` | 6 / 4 / 0 / 12 |
| P4 端到端验收 | 执行 + 报告 | 0 / 0 / 0 / 3 |
| **合计** | | **13 / 9 / 0 / 30** |

---

# Phase P1：连接验证层

## Step P1.1：`strata/health.py` —— Health Check 函数

**目标**：提供 LLM 和 OSWorld 的连接验证函数，fail-fast 语义。

**新建文件**：`strata/health.py`

**先读文件清单**：
- `strata/llm/provider.py`（`ChatMessage`、`ChatResponse`、`LLMProvider` Protocol）
- `strata/llm/router.py`（`LLMRouter` 构造与 role 分发）
- `strata/core/config.py`（`StrataConfig`、`OSWorldConfig`、`LLMProviderConfig`）
- `strata/core/errors.py`（`LLMAPIError`、`OSWorldConnectionError`）
- `strata/env/gui_osworld.py:28-65`（`_OSWorldHTTPClient` 的 HTTP 方法）

**API 规格**：

```python
@dataclass(frozen=True)
class HealthStatus:
    component: str
    ok: bool
    detail: str
    latency_ms: float

@icontract.require(lambda config: len(config.providers) > 0)
@icontract.ensure(lambda result: len(result) > 0)
def check_llm_providers(config: StrataConfig) -> Sequence[HealthStatus]: ...

@icontract.require(lambda config: config.osworld.enabled)
def check_osworld(config: StrataConfig) -> HealthStatus: ...

def check_all(config: StrataConfig) -> Sequence[HealthStatus]: ...

@icontract.ensure(lambda result: result is None, "exits on failure")
def require_healthy(statuses: Sequence[HealthStatus]) -> None: ...
```

**契约**：
- `check_llm_providers.require`：`config.providers` 非空。
- `check_llm_providers.ensure`：返回列表长度 == provider 数量；每项 `component` 包含 provider 名。
- `check_osworld.require`：`config.osworld.enabled == True`。
- `require_healthy`：任一 `ok=False` → `sys.exit(1)`（打印失败组件名 + detail）。

**验证矩阵**：
- **L0**：`HealthStatus` frozen dataclass 字段类型。`check_all` 返回 `Sequence[HealthStatus]`。
- **L1**：3 个 icontract 装饰器。
- **L3**：
  - `test_check_llm_success_with_mock_provider`
  - `test_check_llm_failure_with_unreachable_provider`
  - `test_check_osworld_success_with_mock_server`
  - `test_check_osworld_failure_with_unreachable_server`
  - `test_require_healthy_exits_on_failure`
  - `test_require_healthy_passes_on_all_ok`

**Strategy 变更**：无。

**异常设计**：无新异常。`check_*` 函数**不抛异常**——内部 catch 所有错误并转为 `HealthStatus(ok=False, detail=repr(exc))`。`require_healthy` 用 `sys.exit` 而非异常（入口点语义）。

**依赖标注**：无依赖。

**Review 检查项**：
- LLM health check 发 minimal message `[ChatMessage(role="user", content="ping")]`，`max_tokens=1`，`temperature=0`。只验证能返回不报错，不关心内容。
- OSWorld health check 用 `POST /screen_size`（轻量，不截图）。
- `latency_ms` 用 `time.monotonic()` 测量，精度足够。
- 所有网络调用有独立 timeout（5s），不继承 config 里的 `request_timeout`。

---

## Step P1.2：入口点集成

**目标**：`__main__.py`、`scripts/agent_e2e.py`、`scripts/run_tasks.py` 启动时调用 health check。

**修改文件**：`strata/__main__.py`、`scripts/agent_e2e.py`、`scripts/run_tasks.py`

**先读文件清单**：
- `strata/__main__.py`（全读，79 行）
- `scripts/agent_e2e.py:116-125`（`main` 函数启动段）
- `scripts/run_tasks.py:250-260`（`main` 函数启动段）

**API 规格**：在每个入口的 `load_config` 之后、`EnvironmentFactory.create` 之前插入：

```python
from strata.health import check_all, require_healthy
statuses = check_all(config)
for s in statuses:
    mark = "+" if s.ok else "!"
    print(f"[{mark}] {s.component}: {s.detail} ({s.latency_ms:.0f}ms)")
require_healthy(statuses)
```

**契约**：无新增（复用 P1.1 的契约）。

**验证矩阵**：
- **L0**：import 路径 mypy 覆盖。
- **L3**：
  - `test_main_exits_on_llm_failure`（mock `check_all` 返回失败 → `SystemExit`）
  - `test_main_continues_on_all_healthy`（mock `check_all` 返回全 ok → 继续）

**Strategy 变更**：无。

**异常设计**：无。

**依赖标注**：依赖 Step P1.1。

**Review 检查项**：
- `require_healthy` 的 `sys.exit(1)` 在 `__main__` 里直接生效；在 `scripts/` 里也直接生效（都是 `if __name__ == "__main__"` 入口）。
- health check 输出格式与已有 `[+]` 前缀风格一致。

---

## Step P1.3：pytest conftest + Live 测试 fixture

**目标**：在 `tests/conftest.py` 和 `tests/e2e/conftest.py` 里添加 OSWorld 自动探测 fixture，`live_llm` 和 `integration` marker 自动跳过。

**修改文件**：`tests/conftest.py`、`tests/e2e/conftest.py`、`pyproject.toml`

**先读文件清单**：
- `tests/conftest.py`（当前为空 stub）
- `tests/e2e/conftest.py`（现有 `repo_config` fixture）
- `pyproject.toml`（现有 markers）

**API 规格**：

```python
# tests/conftest.py
@pytest.fixture(scope="session")
def repo_config() -> StrataConfig | None:
    """Load config.toml from repo root; None if missing."""
    ...

# tests/e2e/conftest.py
@pytest.fixture(scope="session")
def live_config(repo_config: StrataConfig) -> StrataConfig:
    """Skip if no config.toml."""
    ...

@pytest.fixture(scope="session")
def osworld_url(live_config: StrataConfig) -> str:
    """Return OSWorld server URL; skip if not enabled or unreachable."""
    ...

@pytest.fixture(scope="session")
def osworld_client(osworld_url: str) -> OSWorldRawClient:
    """Build an OSWorldRawClient for e2e tests."""
    ...
```

**契约**：Fixture 级别，无 icontract。

**验证矩阵**：
- **L0**：fixture 返回类型。
- **L3**（间接验证）：后续 e2e 测试能正常 skip/pass。

**Strategy 变更**：无。

**异常设计**：无。fixture skip 用 `pytest.skip()`。

**依赖标注**：依赖 Step P1.1（`check_osworld` 用于 fixture 探测）。

**Review 检查项**：
- `osworld_url` fixture 内部调 `check_osworld`；unreachable → `pytest.skip("OSWorld not reachable")`。
- `live_llm` marker 的 skip 逻辑移到 conftest fixture（从 `tests/e2e/test_live_llm.py` 的 module-level skip 改为 fixture-based）。
- `STRATA_OSWORLD_URL` 环境变量仍然优先级最高（覆盖 config.toml 里的 `server_url`）。

---

# Phase P2：端到端通路打通

## Step P2.1：`run_tasks.py` 补全 `target="osworld"` 路径

**目标**：setup 和 verify 的 `target="osworld"` 通过 `POST /execute` 在 OSWorld 容器内执行。

**修改文件**：`scripts/run_tasks.py`

**先读文件清单**：
- `scripts/run_tasks.py`（全读）
- `scripts/osworld_smoke.py:40-64`（`OSWorldRawClient.execute` 的 wire format）
- `strata/tasks.py`（`SetupSpec.target`、`VerifySpec.target` 类型）
- `strata/core/config.py`（`OSWorldConfig.server_url`）

**API 规格**：

```python
def _run_shell_osworld(
    command: str,
    server_url: str,
    timeout: float,
) -> ShellResult:
    """Run a shell command inside the OSWorld container via POST /execute."""
    ...

def _run_shell(
    target: Literal["host", "osworld"],
    command: str,
    timeout: float,
    server_url: str = "",
) -> ShellResult:
    """Dispatch to host or osworld shell."""
    ...
```

**契约**：
- `_run_shell_osworld.require`：`server_url` 非空。
- `_run_shell.require`：`timeout > 0`；`target == "osworld"` 时 `server_url` 非空。

**验证矩阵**：
- **L0**：`Literal["host", "osworld"]` 类型覆盖。
- **L1**：2 个 require。
- **L3**：
  - `test_run_shell_host_executes_command`
  - `test_run_shell_osworld_posts_to_execute`（mock HTTP）
  - `test_run_shell_osworld_timeout_returns_error`（mock）
  - `test_single_task_setup_osworld_dispatches_correctly`

**Strategy 变更**：无。

**异常设计**：复用 `OSWorldConnectionError`；在 `_run_shell_osworld` 内 catch → 转为 `ShellResult(returncode=-1, stderr=...)`。

**依赖标注**：无依赖。

**Review 检查项**：
- `POST /execute` body 格式：`{"command": cmd, "shell": true}`。返回 JSON `{"output": "...", "returncode": 0}`——需确认 OSWorld Flask 服务端实际返回字段。
- `_run_single` 里 setup/verify 调用改为 `_run_shell(task.setup.target, cmd, timeout, server_url=cfg.osworld.server_url)`。
- `--config` 参数传入时 `server_url` 从 config 获取；OSWorld 未启用时 `target="osworld"` 的 task → `verdict=ERROR`。

---

## Step P2.2：Task 文件 target 修正

**目标**：把 `create-hello-txt.toml` 和 `read-hostname.toml` 的 setup/verify target 从 `"host"` 改为 `"osworld"`（agent 在容器内操作，verify 必须在容器内检查）。

**修改文件**：`tasks/create-hello-txt.toml`、`tasks/read-hostname.toml`

**先读文件清单**：
- `tasks/create-hello-txt.toml`（全读）
- `tasks/read-hostname.toml`（全读）

**API 规格**：纯数据文件变更。

**验证矩阵**：
- **L3**：
  - `test_sample_tasks_parse_with_osworld_target`（`TaskFile.load` 断言 target 字段）
  - `test_template_still_parses`（回归）

**Strategy 变更**：无。

**依赖标注**：依赖 Step P2.1（`run_tasks.py` 必须先支持 osworld target）。

**Review 检查项**：
- `create-hello-txt` 的 setup 命令 `rm -f /tmp/strata_e2e_hello.txt` 改在容器内执行（`target = "osworld"`）。
- `read-hostname` 的 verify 命令 `cat /etc/hostname` 改在容器内执行——容器的 hostname 可能跟 host 不同。
- `expected_stdout_regex` 可能需要调整（容器 hostname 不是 host hostname）。`read-hostname` 改为只校验 `expected_exit_code = 0`（文件存在即可），去掉 regex。

---

## Step P2.3：Live LLM 连接测试扩展

**目标**：扩展 `tests/e2e/test_live_llm.py`，覆盖全部 4 个 role 的 roundtrip + vision 带图片。

**修改文件**：`tests/e2e/test_live_llm.py`

**先读文件清单**：
- `tests/e2e/test_live_llm.py`（全读）
- `strata/llm/provider.py:8-40`（`ChatMessage` / `ChatResponse`）
- `strata/llm/router.py`（`LLMRouter.plan/ground/see/search`）

**API 规格**：新增测试函数，无新 API。

**验证矩阵**：
- **L3**：
  - `test_health_check_all_providers_pass`（用 `check_llm_providers`，真实 API）
  - `test_router_plan_roundtrip`（`router.plan(messages)` 返回非空 content）
  - `test_router_see_with_screenshot`（vision role + 真实 PNG → 返回含 JSON 的 content）

**Strategy 变更**：无。

**异常设计**：无。

**依赖标注**：依赖 Step P1.1（`check_llm_providers`）+ P1.3（fixture）。

**Review 检查项**：
- 所有 live 测试标记 `@pytest.mark.live_llm`，默认不跑，`STRATA_LIVE_LLM=1` 时启用。
- Vision test 的 PNG：用 Pillow 生成一个 100x100 红色方块，不依赖文件系统。
- 断言 `ChatResponse.content` 非空 + `ChatResponse.usage` 有 `prompt_tokens` 和 `completion_tokens`。

---

# Phase P3：OSWorld 解耦 + 抽象层

## Step P3.1：`strata/env/osworld_client.py` —— HTTP Client 提取

**目标**：把 `_OSWorldHTTPClient` 从 `gui_osworld.py` 提取到独立模块，加入 `OSWorldRawClient`（来自 `osworld_smoke.py` 的 `/execute` 调用能力）。`gui_osworld.py` 和 `recorder.py` 改从新位置 import。

**新建文件**：`strata/env/osworld_client.py`
**修改文件**：`strata/env/gui_osworld.py`、`scripts/osworld_smoke.py`

**先读文件清单**：
- `strata/env/gui_osworld.py:28-65`（`_OSWorldHTTPClient` 完整实现）
- `scripts/osworld_smoke.py:40-64`（`OSWorldRawClient` 实现）

**API 规格**：

```python
# strata/env/osworld_client.py

class OSWorldHTTPClient:
    """HTTP client for OSWorld Docker server — used by GUI adapter, recorder, task runner."""
    def __init__(self, base_url: str, timeout: float) -> None: ...
    def post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]: ...
    def post_form_get_bytes(self, path: str, fields: dict[str, str]) -> bytes: ...
    def get_bytes(self, path: str) -> bytes: ...
    def execute_shell(self, command: str) -> dict[str, object]: ...
    def run_python(self, code: str) -> dict[str, object]: ...
    def health_check(self) -> bool: ...
```

**契约**：
- `__init__.require`：`base_url` 非空且以 `http` 开头。
- `execute_shell.require`：`command` 非空。
- `run_python.require`：`code` 非空。
- `health_check.ensure`：不抛异常（内部 catch 返回 bool）。

**验证矩阵**：
- **L0**：公有 API 签名 mypy 覆盖。从 `_OSWorldHTTPClient`（下划线前缀私有）升级为 `OSWorldHTTPClient`（公有）。
- **L1**：4 个契约。
- **L3**：
  - `test_post_json_success`（mock urllib）
  - `test_post_json_connection_error_wraps`
  - `test_execute_shell_sends_correct_payload`
  - `test_run_python_sends_correct_payload`
  - `test_health_check_returns_true_on_reachable`
  - `test_health_check_returns_false_on_unreachable`

**Strategy 变更**：无。

**异常设计**：复用 `OSWorldConnectionError`。

**依赖标注**：无依赖。

**Review 检查项**：
- `gui_osworld.py` 改为 `from strata.env.osworld_client import OSWorldHTTPClient`，内部 `self._client` 类型更新。
- `osworld_smoke.py` 的 `OSWorldRawClient` 删除，改用 `OSWorldHTTPClient.execute_shell`。
- `_OSWorldHTTPClient` 的下划线前缀移除——它现在是公有 API（recorder、run_tasks、smoke 都需要）。
- `post_form_get_bytes` 保持与原始签名一致。

---

## Step P3.2：`RemoteCodeRunner` Protocol + Recorder 解耦

**目标**：`recorder.py` 不再 import `_OSWorldHTTPClient` / `osworld_client`，改为依赖 `RemoteCodeRunner` Protocol。OSWorld 特化由构造时注入。

**修改文件**：`strata/observability/recorder.py`

**先读文件清单**：
- `strata/observability/recorder.py`（全读）
- Step P3.1 的 `strata/env/osworld_client.py`

**API 规格**：

```python
# strata/observability/recorder.py 新增

@runtime_checkable
class RemoteCodeRunner(Protocol):
    """Minimal contract for running code on a remote machine."""
    def run_python(self, code: str) -> dict[str, object]: ...
    def post_form_get_bytes(self, path: str, fields: dict[str, str]) -> bytes: ...
    def get_bytes(self, path: str) -> bytes: ...

class OSWorldFFmpegRecorder:
    def __init__(
        self,
        runner: RemoteCodeRunner,      # 替换 osworld_config
        screen_size: tuple[int, int],   # 替换 osworld_config.screen_size
        out_dir: Path,
        fps: int = 30,
    ) -> None: ...
```

**契约**：
- `__init__.require`：`1 <= fps <= 60`；`screen_size[0] > 0 and screen_size[1] > 0`。
- `start.require`：`run_id` 匹配 `^[a-zA-Z0-9_-]+$`（不变）。

**验证矩阵**：
- **L0**：`RemoteCodeRunner` Protocol 类型检查；`NullRecorder` / `OSWorldFFmpegRecorder` 满足 `TrajectoryRecorder`。
- **L1**：2 个契约（`fps`、`run_id`）。
- **L3**：
  - `test_recorder_accepts_mock_runner`
  - `test_recorder_start_calls_run_python`
  - `test_recorder_stop_downloads_mp4_via_runner`
  - `test_recorder_refuses_bad_run_id`
  - `test_null_recorder_satisfies_protocol`

**Strategy 变更**：无。

**异常设计**：`RecorderError`（已有）保留。

**依赖标注**：依赖 Step P3.1。

**Review 检查项**：
- `recorder.py` 的 `from strata.env.gui_osworld import _OSWorldHTTPClient` 必须被完全移除。
- `recorder.py` 不再 import 任何 `strata.env.*` 模块——观测层 → env 层的反向依赖彻底切断。
- `OSWorldConfig` import 也移除——recorder 只需要 `screen_size` 和 `runner`。

---

## Step P3.3：Orchestrator os_type 解耦 + recorder 构造解耦

**目标**：`_plan()` 里的 os_type 不再读 `config.osworld.os_type`；`_build_recorder` 不再直接 import `OSWorldFFmpegRecorder`。

**修改文件**：`strata/harness/orchestrator.py`

**先读文件清单**：
- `strata/harness/orchestrator.py:317-344`（`_plan` 方法）
- `strata/harness/orchestrator.py:281-297`（`_build_recorder` 方法）
- `strata/harness/orchestrator.py:56-64`（import 段）

**API 规格**：

```python
# orchestrator.py 修改

def _plan(self, goal: str) -> TaskGraph:
    plan_context: dict[str, object] = {
        "os_type": self._resolve_os_type(),  # 新私有方法
        ...
    }
    ...

def _resolve_os_type(self) -> str:
    """Return OS type for plan context.
    OSWorld enabled → config.osworld.os_type; else → platform.system()."""
    ...

def _build_recorder(self, layout: RunDirLayout | None) -> TrajectoryRecorder:
    """Recorder construction now fully delegated to injected factory or NullRecorder."""
    if self._recorder is not None:
        return self._recorder
    # OSWorld recorder construction moves to factory/caller
    return NullRecorder()
```

**契约**：不变。

**验证矩阵**：
- **L0**：`_resolve_os_type` 返回 `str`。import 段不再有 `OSWorldFFmpegRecorder`。
- **L3**：
  - `test_plan_context_os_type_from_platform_when_osworld_disabled`
  - `test_plan_context_os_type_from_config_when_osworld_enabled`
  - `test_build_recorder_returns_null_when_no_injection`
  - 全部既有 `test_orchestrator.py` 回绿

**Strategy 变更**：无。

**异常设计**：无。

**依赖标注**：依赖 Step P3.2。

**Review 检查项**：
- `from strata.observability.recorder import OSWorldFFmpegRecorder` 从 orchestrator 的 import 段移除。
- `NullRecorder` import 保留（default fallback）。
- Recorder 注入由调用方（`__main__.py` / `scripts/agent_e2e.py`）负责构造——如果 OSWorld enabled，调用方构造 `OSWorldFFmpegRecorder(runner=OSWorldHTTPClient(...), ...)`，注入给 orchestrator。
- `_build_recorder` 简化为：注入优先 → NullRecorder 兜底。

---

## Step P3.4：Factory 抽象层整理 + LinuxGUIAdapter 错误改善

**目标**：清理 factory 的双路分发；改善非 OSWorld 路径的错误消息；为未来 macOS 支持预留扩展点。

**修改文件**：`strata/env/factory.py`、`strata/env/linux/gui.py`

**先读文件清单**：
- `strata/env/factory.py`（全读）
- `strata/env/linux/gui.py`（全读）

**API 规格**：

```python
# factory.py — 清理后

class EnvironmentFactory:
    @staticmethod
    def create(config: StrataConfig) -> EnvironmentBundle:
        """Build EnvironmentBundle.

        Dispatch:
        - osworld.enabled=True → OSWorldGUIAdapter (any platform)
        - osworld.enabled=False + Linux + DISPLAY → LinuxGUIAdapter (future)
        - otherwise → UnsupportedPlatformError with actionable message
        """
        ...

# linux/gui.py — 错误消息改善

class LinuxGUIAdapter:
    def __init__(self) -> None:
        raise UnsupportedPlatformError(
            "Native Linux GUI backend not yet implemented. "
            "Options: (1) set osworld.enabled=true in config.toml "
            "and start an OSWorld Docker container; "
            "(2) wait for Phase 12+ native backend."
        )
```

**契约**：不变。

**验证矩阵**：
- **L3**：
  - `test_factory_osworld_enabled_creates_osworld_adapter`（mock HTTP）
  - `test_factory_osworld_disabled_on_linux_raises_with_actionable_message`
  - `test_factory_non_linux_raises_with_actionable_message`

**Strategy 变更**：无。

**异常设计**：无。

**依赖标注**：依赖 Step P3.1（import 路径变更）。

**Review 检查项**：
- 错误消息必须包含具体解决步骤（不是泛泛"not supported"）。
- Factory 的注释标明 macOS 扩展点位置。
- CONVENTION 注释：`# CONVENTION: LinuxGUIAdapter stub — Phase 12+ 实装 pyautogui/xdotool 后端。headless 服务器请用 OSWorld。`

---

# Phase P4：端到端验收

## Step P4.1：`tests/e2e/test_e2e_tasks.py` —— 真实题目执行

**目标**：用 `run_tasks.py` 的核心逻辑执行 `create-hello-txt` 和 `read-hostname`，验证 verdict 和 report。

**新建文件**：`tests/e2e/test_e2e_tasks.py`

**先读文件清单**：
- `scripts/run_tasks.py`（全读）
- `tasks/create-hello-txt.toml`（修正后）
- `tasks/read-hostname.toml`（修正后）
- Step P2.1 的 `_run_single` 新签名

**API 规格**：纯测试文件。

**验证矩阵**：
- **L3**：
  - `test_create_hello_txt_e2e`（`@pytest.mark.integration` + `@pytest.mark.live_llm`，真实 OSWorld + 真实 LLM → verdict=PASS 或 FAIL with meaningful error）
  - `test_read_hostname_e2e`（同上）
  - `test_report_json_written_and_valid`（执行后 reports/ 下有 JSON）

**Strategy 变更**：无。

**异常设计**：无。

**依赖标注**：依赖 Phase P1 + P2 + P3 全部完成。

**Review 检查项**：
- 测试标记 `@pytest.mark.integration` + `@pytest.mark.live_llm`，仅在环境完备时执行。
- 如果 agent 未能 COMPLETED goal，测试应该 **不 assert PASS**——而是断言 verdict 是合法值（PASS/FAIL/ERROR/TIMEOUT）+ report 结构完整。真正的 PASS 是后续调优的事。
- timeout：单题 180s（给 LLM 规划 + 执行足够时间）。

---

## Step P4.2：手动 Smoke 验收

**目标**：在 Phase Gate 之后，手动执行完整 smoke：

```bash
# 1. Health check
uv run strata --config ./config.toml  # 应该显示连接状态

# 2. 单题执行
uv run python scripts/run_tasks.py tasks/create-hello-txt.toml --config ./config.toml

# 3. 批量执行
uv run python scripts/run_tasks.py 'tasks/*.toml' --config ./config.toml

# 4. 检查产物
ls .strata-run/current/
cat reports/*.json | python -m json.tool
```

**验证矩阵**：
- **L3**：无自动化测试（手工）。验收标准：
  - Health check 打印所有组件状态 + latency
  - 至少 1 道题 verdict 非 ERROR（agent 成功启动并尝试执行）
  - `reports/*.json` 结构完整

**Strategy 变更**：无。

**依赖标注**：依赖 Step P4.1。

**Review 检查项**：结果截图 / 终端输出保存为记录。

---

## 验证矩阵汇总

| Module | 新增 L0 | 新增 L1 | 新增 L2 | 新增 L3 | 合计 |
|---|---|---|---|---|---|
| `strata/health.py` | 2 | 3 | 0 | 6 | 11 |
| `strata/__main__.py` + scripts | 1 | 0 | 0 | 2 | 3 |
| `tests/conftest.py` + `e2e/conftest.py` | 1 | 0 | 0 | 0 | 1 |
| `scripts/run_tasks.py` | 1 | 2 | 0 | 4 | 7 |
| `tasks/*.toml` | 0 | 0 | 0 | 2 | 2 |
| `tests/e2e/test_live_llm.py` | 0 | 0 | 0 | 3 | 3 |
| `strata/env/osworld_client.py` | 2 | 4 | 0 | 6 | 12 |
| `strata/observability/recorder.py` | 2 | 2 | 0 | 5 | 9 |
| `strata/harness/orchestrator.py` | 1 | 0 | 0 | 4 | 5 |
| `strata/env/factory.py` + `linux/gui.py` | 0 | 0 | 0 | 3 | 3 |
| `tests/e2e/test_e2e_tasks.py` | 0 | 0 | 0 | 3 | 3 |
| **合计** | **10** | **11** | **0** | **38** | **59** |

---

## 执行流水线

```
Phase P1 — 连接验证层:
  P1.1W → P1.1R → [Retry≤3] → Gate (mypy + pytest) → Commit on phase/9-e2e-decouple
  P1.2W → P1.2R → [Retry≤3] → Gate → Commit
  P1.3W → P1.3R → [Retry≤3] → Gate → Commit
  Phase Gate: mypy --strict . && pytest -q && ruff check . && ruff format --check .
  ★ 里程碑：uv run strata --config ./config.toml 能打印 health check 结果
  Push phase/9-e2e-decouple

Phase P2 — 端到端通路:
  P2.1W → P2.1R → [Retry≤3] → Gate → Commit
  P2.2W → P2.2R → [Retry≤3] → Gate → Commit
  P2.3W → P2.3R → [Retry≤3] → Gate → Commit
  Phase Gate: full stack
  ★ 里程碑：STRATA_LIVE_LLM=1 pytest tests/e2e/test_live_llm.py -v 通过
  Push

Phase P3 — OSWorld 解耦:
  P3.1W → P3.1R → [Retry≤3] → Gate → Commit
  P3.2W → P3.2R → [Retry≤3] → Gate → Commit
  P3.3W → P3.3R → [Retry≤3] → Gate → Commit
  P3.4W → P3.4R → [Retry≤3] → Gate → Commit
  Phase Gate: full stack + recorder.py 的 import 中不含 strata.env.gui_osworld
  ★ 里程碑：grep -r "gui_osworld" strata/observability/ 返回空
  Push

Phase P4 — 端到端验收:
  P4.1W → P4.1R → [Retry≤3] → Gate → Commit
  P4.2 手工验收
  Phase Gate: full stack + 手工 smoke
  ★ 里程碑：uv run python scripts/run_tasks.py tasks/create-hello-txt.toml 有输出
  Push；合入 main
```

**熔断策略**（workspace 规则）：
- 单 Step 的 Write→Review 循环 ≤ 3 次；第 4 次失败交人类介入。
- 任何 Phase Gate 失败先定位到具体 Step 回滚 commit，不推进下一 Phase。
- LLM live 测试偶发超时 → 重试 1 次；连续 2 次失败暂停 Phase 检查 API key / 网络。
- OSWorld 容器连接失败 → 检查 `docker ps`，确认容器运行后重试。

---

## 交付验收标准（整个计划完成后）

1. **`uv run strata --config ./config.toml`** 启动时打印所有组件 health check 状态 + latency；LLM 不可用时 exit 1。
2. **`uv run python scripts/run_tasks.py tasks/create-hello-txt.toml`** 能执行（无论 PASS/FAIL），`reports/*.json` 结构完整。
3. **`strata/observability/recorder.py` 不 import `strata.env.gui_osworld`**——观测层与 env 层解耦。
4. **`strata/harness/orchestrator.py` 不 import `OSWorldFFmpegRecorder`**——核心循环与 OSWorld 解耦。
5. **`STRATA_LIVE_LLM=1 uv run pytest tests/e2e/test_live_llm.py -v`** 全绿。
6. **`mypy --strict .`** 零错误；**`pytest -q`** 全绿；**`ruff check .`** 零警告。
7. **CONVENTION 注释记录**：A11y ABANDONED；LinuxGUIAdapter stub → Phase 12+；GUI 抽象层为 macOS 预留。
