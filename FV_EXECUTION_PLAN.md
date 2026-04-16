# FV 执行计划：Strata Agent 框架

## 全局规则

- **SPECIFY → IMPLEMENT → VERIFY** 状态机严格执行，不可跳层。
- 每个 Step 物理隔离为 **Write Agent**（规格推演 + 实现）和 **Review Agent**（只读黑盒审计）。
- Write-Review 循环上限 **3 次**，超限熔断交人类接管。
- **Step 后 Gate**：`uv run mypy --strict <module> && uv run pytest tests/<module> -x -q`
- **Phase 后 Gate**：`uv lock --check && uv run mypy --strict . && uv run pytest --tb=short -q && uv run ruff check . && uv run ruff format --check .`
- 包管理统一 `uv`，Lint/Format 统一 `ruff`，配置格式统一 TOML。
- API 密钥仅存于 `~/.strata/config.toml`，仓库只有 `config.example.toml`。
- **纯 VLM 感知架构**：框架不使用操作系统可访问性 API（AT-SPI / AXAPI / UIA），所有 UI 元素定位完全依赖截图 + VLM。已知限制见文末。

## 工作区拓扑

```
strata/                          # 顶层 package
├── __init__.py                  # StrataError 根异常，版本号
├── core/                        # 共享基础设施
│   ├── __init__.py
│   ├── config.py                # TOML 配置加载（tomllib + frozen dataclass）
│   ├── types.py                 # 核心值对象（TaskNode, TaskGraph, ActionResult 等）
│   ├── errors.py                # 异常层级
│   └── sandbox.py               # SandboxGuard（路径规范化 + 前缀检查，跨层共用）
├── llm/                         # LLM 提供商抽象
│   ├── __init__.py
│   ├── provider.py              # LLMProvider Protocol + OpenAI-compat 实现
│   └── router.py                # 角色路由（planner/grounding/vision/search）
├── interaction/                 # Layer 0：用户交互
│   ├── __init__.py
│   └── cli.py                   # CLI 循环
├── planner/                     # Layer 1：HTN 任务规划
│   ├── __init__.py
│   ├── htn.py                   # 任务图数据模型 + 分解
│   ├── prompts.py               # LLM Prompt 模板常量
│   └── adjuster.py              # 局部微调
├── harness/                     # Layer 2：执行编排
│   ├── __init__.py
│   ├── state_machine.py         # 全局 + 任务级状态机
│   ├── scheduler.py             # 顺序调度器（初版线性）
│   ├── gui_lock.py              # GUI 全局互斥锁
│   ├── recovery.py              # 5 级错误恢复管道
│   ├── context.py               # 滑动窗口 + 关键事实抽取
│   └── persistence.py           # 原子写 + 断点续传
├── grounding/                   # Layer 3：行动接地（纯 VLM）
│   ├── __init__.py
│   ├── vision_locator.py        # 截图 → VLM → 坐标/动作（含滚动搜索循环）
│   ├── scaler.py                # 坐标 DPI 转换
│   ├── terminal_handler.py      # 终端命令处理
│   ├── filter.py                # 敏感信息过滤（VLM 发送前置检查）
│   └── validator.py             # 坐标边界检查（仅屏幕范围）
└── env/                         # Layer 4：环境交互（按平台拆分）
    ├── __init__.py
    ├── protocols.py             # 5 个 Protocol（无 A11y）
    ├── factory.py               # EnvironmentFactory（sys.platform 分发 GUI 实现）
    ├── terminal_pty.py          # POSIX PTY 实现（Linux + macOS 共用）
    ├── filesystem.py            # 沙盒化文件系统（POSIX 共用，注入 SandboxGuard）
    ├── gui_osworld.py           # OSWorld DesktopEnv 适配器（跨平台测试后端）
    ├── linux/                   # Linux 平台特定实现
    │   ├── __init__.py
    │   ├── gui.py               # xdotool + pyautogui
    │   ├── app_manager.py       # xdg-open / wmctrl
    │   └── system.py            # xclip/xsel + 环境变量
    └── macos/                   # macOS 平台（Phase 后续填充）
        ├── __init__.py
        ├── gui.py               # stub → Quartz/AppKit
        ├── app_manager.py       # stub → osascript
        └── system.py            # stub → pbcopy/pbpaste

tests/
├── __init__.py
├── strategies.py                # Hypothesis 自定义 Strategy
├── conftest.py                  # 共享 Fixtures（含 OSWorld Docker session fixture）
├── test_core/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_types.py
│   ├── test_errors.py
│   └── test_sandbox.py          # SandboxGuard 测试
├── test_llm/
│   ├── __init__.py
│   ├── test_provider.py
│   └── test_router.py
├── test_env/
│   ├── __init__.py
│   ├── test_protocols.py
│   ├── test_factory.py           # EnvironmentFactory 平台分发
│   ├── test_terminal_pty.py
│   ├── test_filesystem.py
│   └── test_linux/               # Linux 平台适配器测试
│       ├── __init__.py
│       └── test_system.py
├── test_harness/
│   ├── __init__.py
│   ├── test_state_machine.py
│   ├── test_scheduler.py
│   ├── test_gui_lock.py
│   ├── test_persistence.py
│   ├── test_recovery.py
│   └── test_context.py
├── test_planner/
│   ├── __init__.py
│   ├── test_htn.py
│   └── test_adjuster.py
├── test_grounding/
│   ├── __init__.py
│   ├── test_vision_locator.py
│   ├── test_terminal_handler.py
│   ├── test_scaler.py
│   ├── test_filter.py
│   └── test_validator.py
├── test_interaction/
│   ├── __init__.py
│   └── test_cli.py
└── test_integration.py          # 端到端（含 @pytest.mark.live_llm）
```

### 不可变 API 边界

- `strata.env.protocols` 中的 5 个 Protocol（无 A11y）是跨层契约，一旦 Phase 3 定稿，后续 Phase 不得修改签名（只可新增方法）。
- `strata.core.types` 中的 frozen dataclass 是全局值对象，变更需同步所有消费方。
- `strata.core.errors` 中的异常层级一旦建立，子模块只可派生子类，不可修改基类。
- `strata.core.sandbox.SandboxGuard` 一旦 Phase 3 定稿，接口不可变（env 层和 harness 层均依赖）。

### 层间依赖规则

- env（L4）只可依赖 core；**不可**依赖 harness（L2）、planner（L1）、grounding（L3）。
- 平台特定实现（`env/linux/`、`env/macos/`）只可被 `env/factory.py` 引用，外部代码通过 Protocol 消费。

### 已知限制（纯 VLM 感知架构）

- **不可见元素无法操作**：被其他窗口完全遮挡的控件、需要滚动才能看到的列表项（滚动搜索可部分缓解）、纯逻辑上存在但视觉不可见的元素。
- **VLM 成本与延迟**：每个 GUI 动作至少产生一次 VLM 调用；滚动搜索模式下可能产生 N 次。建议配置低延迟模型（Grok fast / Kimi 视觉）。
- **无结构化语义**：无 A11y 树意味着无法获取元素的 role/state/value 等语义信息，完全依赖 VLM 的视觉理解能力。

## Strategy 状态

### 现有 Strategy
无（`tests/strategies.py` 不存在）。

### 本次新增 Strategy

| Strategy 名称 | 生成类型 | 首次引入 Phase |
|---|---|---|
| `st_config_toml()` | 有效/边界 TOML 配置字符串 | Phase 1 |
| `st_strata_config()` | StrataConfig frozen dataclass 实例 | Phase 1 |
| `st_task_node()` | TaskNode（primitive/compound） | Phase 1 |
| `st_task_graph()` | 合法 TaskGraph（线性链，初版无 DAG） | Phase 1 |
| `st_sandbox_path(sandbox_root)` | 沙盒内/外路径（含 `..`、符号链接） | Phase 3 |
| `st_global_state_event_seq()` | 合法/非法状态转换事件序列 | Phase 4 |
| `st_command_result()` | CommandResult（各种退出码、超时组合） | Phase 3 |
| `st_coordinate(screen_bounds)` | 屏幕坐标（含边界） | Phase 6 |
| `st_llm_role_config()` | LLM 角色配置 | Phase 2 |

## Phase 概览表

| Phase | 变更域 | 新增验证数（L0 类型 / L1 契约 / L2 属性 / L3 示例） |
|---|---|---|
| Phase 1：Bootstrap + Core | `pyproject.toml`, `strata/core/`, `strata/__init__.py`, `tests/strategies.py` | 15 / 8 / 4 / 6 |
| Phase 2：LLM 抽象 | `strata/llm/` | 6 / 4 / 2 / 4 |
| Phase 3：L4 环境层 + SandboxGuard（无 A11y） | `strata/env/`, `strata/core/sandbox.py` | 16 / 8 / 3 / 8 |
| Phase 4：L2 Harness | `strata/harness/` | 18 / 12 / 4 / 10 |
| Phase 5：L1 HTN Planner | `strata/planner/` | 8 / 6 / 3 / 5 |
| Phase 6：L3 Grounding（纯 VLM）+ OSWorld | `strata/grounding/`, `strata/env/gui_osworld.py` | 12 / 8 / 2 / 10 |
| Phase 7：L0 CLI + 集成 | `strata/interaction/`, `strata/harness/context.py` | 6 / 4 / 1 / 4 |

---

## Phase 1：项目初始化 + 核心类型 + 配置

### Step 1.1: 项目骨架与依赖声明

目标: 创建 `pyproject.toml`、目录结构、安装 `uv`、声明全部依赖、部署运行时配置

新建/修改文件:
- `pyproject.toml`（新建）
- `strata/__init__.py`（新建）
- `strata/core/__init__.py`（新建）
- `tests/__init__.py`（新建）
- `tests/strategies.py`（新建，空骨架）
- `tests/conftest.py`（新建）
- `.cursor/rules/workspace.mdc`（修改 python_version 为 3.13）
- `config.example.toml`（新建，仓库内示例配置，含 OSWorld 配置段）
- `~/.strata/config.toml`（新建，不入 git，含真实 API 密钥）

先读文件清单:
- `.cursor/rules/workspace.mdc`（获取标准 pyproject.toml 配置段）
- `.gitignore`（确认覆盖模式）

API 规格:
  签名: 无运行时 API（纯项目配置）
  契约: 无
验证矩阵:
  L0 类型: `uv run mypy --strict strata/` 通过（空 package）
  L1 契约: 无
  L2 属性: 无
  L3 示例: `uv run pytest` 可执行（0 tests collected）
Strategy 变更: 创建 `tests/strategies.py` 空骨架
异常设计: 无
依赖标注: 无依赖（Phase 1 起点）
Review 检查项:
- `pyproject.toml` 包含所有 workspace 标准配置段（mypy/ruff/pytest/hypothesis），python_version = "3.13"
- 所有依赖版本为最新稳定版
- `uv lock` 成功（所有依赖在 Python 3.13 上可解析）
- `uv run mypy --strict strata/ && uv run ruff check . && uv run ruff format --check .` 通过
- `~/.strata/config.toml` 已创建，包含三家 LLM 真实 API 密钥
- `config.example.toml` 包含 `[osworld]` 配置段、`[gui]` 滚动搜索字段、无 `[a11y]` 段

### Step 1.2: 异常层级

目标: 建立 `StrataError` 根异常和各子包专用异常基类

新建/修改文件:
- `strata/__init__.py`（修改，导出 StrataError）
- `strata/core/errors.py`（新建）

先读文件清单:
- `strata/__init__.py`
- `.cursor/rules/workspace.mdc`（异常层级规范）

API 规格:
  签名:
    ```python
    class StrataError(Exception): ...
    class ConfigError(StrataError): ...
    class PlannerError(StrataError): ...
    class HarnessError(StrataError): ...
    class GroundingError(StrataError): ...
    class EnvironmentError(StrataError): ...   # strata 命名空间下，不冲突
    class LLMError(StrataError): ...
    class InteractionError(StrataError): ...
    # ── 安全 ──
    class SandboxViolationError(StrataError): ...   # core 层，非 Harness 专属
    # ── Harness ──
    class StateTransitionError(HarnessError): ...
    class GUILockTimeoutError(HarnessError): ...
    class AdjusterNotAvailableError(HarnessError): ...
    class MaxIterationsExceededError(HarnessError): ...
    # ── Environment ──
    class UnsupportedPlatformError(EnvironmentError): ...
    class CommandTimeoutError(EnvironmentError): ...
    class SilenceTimeoutError(CommandTimeoutError): ...
    class OSWorldConnectionError(EnvironmentError): ...
    # ── Grounding ──
    class VisionLocatorError(GroundingError): ...
    class InvalidCoordinateError(GroundingError): ...
    class ElementNotFoundError(GroundingError): ...
    class SensitiveContentError(GroundingError): ...
    # ── LLM ──
    class LLMAPIError(LLMError): ...
    class LLMFeatureNotSupportedError(LLMError): ...
    ```
  契约:
    require: 无（异常类无构造约束）
    ensure: 无
    invariant: 所有异常类必须继承自 StrataError 且不跨 package 边界
验证矩阵:
  L0 类型: mypy 确认异常层级继承链完整，`except StrataError` 可捕获所有子类
  L1 契约: 无
  L2 属性: 无
  L3 示例:
    - `test_all_exceptions_inherit_strata_error`: Given 所有导出异常类 / When isinstance 检查 / Then 全部为 StrataError 子类
    - `test_exception_hierarchy_no_cross_package`: Given 异常类集合 / When 检查 MRO / Then 不存在跨 package 基类引用
Strategy 变更: 无
异常设计: 见上 API 规格
依赖标注: 依赖 Step 1.1
Review 检查项:
- 层级完整覆盖所有子包
- 无裸 `Exception` 使用
- `EnvironmentError` 名称不与 Python 内置冲突（在 `strata` 命名空间下安全）

### Step 1.3: 核心值对象

目标: 定义全局共享的 frozen dataclass 值对象和类型别名

新建/修改文件:
- `strata/core/types.py`（新建）
- `strata/core/__init__.py`（修改，导出公开类型）

先读文件清单:
- `strata/core/__init__.py`
- `strata/core/errors.py`
- `.cursor/rules/workspace.mdc`（Immutability First 规范）

API 规格:
  签名:
    ```python
    from typing import Literal, Final

    GlobalState = Literal["INIT", "PLANNING", "CONFIRMING", "SCHEDULING",
                          "EXECUTING", "RECOVERING", "WAITING_USER",
                          "COMPLETED", "FAILED"]
    TaskState = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED"]

    LLMRole = Literal["planner", "grounding", "vision", "search"]

    @dataclass(frozen=True)
    class Coordinate:
        x: float
        y: float

    @dataclass(frozen=True)
    class ScreenRegion:
        x: int
        y: int
        width: int
        height: int

    VisionActionType = Literal["click", "scroll", "next_page", "not_found"]

    @dataclass(frozen=True)
    class VisionResponse:
        action_type: VisionActionType
        coordinate: Coordinate | None = None    # click/next_page 时有值
        scroll_direction: Literal["up", "down", "left", "right"] | None = None
        confidence: float = 0.0
        raw_text: str = ""                      # VLM 原始输出，用于调试

    @dataclass(frozen=True)
    class WindowInfo:
        title: str
        process_id: int
        window_id: str
        position: Coordinate
        size: tuple[int, int]

    @dataclass(frozen=True)
    class FileInfo:
        path: str
        name: str
        is_dir: bool
        size: int
        modified_at: float

    @dataclass(frozen=True)
    class AppInfo:
        name: str
        identifier: str
        pid: int

    @dataclass(frozen=True)
    class CommandResult:
        stdout: str
        stderr: str
        returncode: int
        timed_out: bool
        interrupted_by_silence: bool

    @dataclass(frozen=True)
    class ActionResult:
        success: bool
        data: Mapping[str, object] | None = None
        error: str | None = None

    @dataclass(frozen=True)
    class TaskNode:
        id: str
        task_type: Literal["primitive", "compound", "repeat", "if_then", "for_each"]
        action: str | None = None          # primitive 的动作名
        params: Mapping[str, object] = field(default_factory=dict)
        method: str | None = None          # compound 的方法名
        depends_on: Sequence[str] = field(default_factory=tuple)
        output_var: str | None = None
        max_iterations: int | None = None  # repeat/for_each 的上限

    @dataclass(frozen=True)
    class TaskGraph:
        goal: str
        tasks: Sequence[TaskNode]
        methods: Mapping[str, Sequence[TaskNode]] = field(default_factory=dict)
    ```
  契约:
    require: 无（frozen dataclass 通过类型系统保证）
    ensure: 无
    invariant: 所有 dataclass 为 frozen=True
验证矩阵:
  L0 类型: mypy 验证 frozen 不可变性、field 类型完整、Literal 值域
  L1 契约: 无（类型系统已覆盖）
  L2 属性:
    - `prop_task_node_roundtrip`: TaskNode 可 JSON 序列化/反序列化往返
    - `prop_task_graph_roundtrip`: TaskGraph 可序列化/反序列化往返
  L3 示例:
    - `test_frozen_dataclass_immutable`: Given TaskNode / When 修改字段 / Then FrozenInstanceError
    - `test_task_graph_empty_valid`: Given 空 tasks / When 构造 TaskGraph / Then 合法
Strategy 变更:
  - 新增 `st_task_node()`: 生成各种 task_type 的 TaskNode
  - 新增 `st_task_graph()`: 生成包含 1-10 个 TaskNode 的线性 TaskGraph
异常设计: 无
依赖标注: 依赖 Step 1.2
Review 检查项:
- 所有 dataclass 为 frozen=True
- 容器入参使用 Sequence/Mapping 而非 list/dict
- 无 Any 类型
- JSON 序列化辅助函数类型签名完整

### Step 1.4: TOML 配置系统

目标: 实现 `~/.strata/config.toml` 的加载、验证、默认值填充

新建/修改文件:
- `strata/core/config.py`（新建）
- `config.example.toml`（新建，仓库内示例配置）
- `tests/test_core/test_config.py`（新建）
- `tests/strategies.py`（修改，添加 st_config_toml / st_strata_config）

先读文件清单:
- `strata/core/types.py`
- `strata/core/errors.py`（ConfigError）
- `.cursor/rules/workspace.mdc`

API 规格:
  签名:
    ```python
    @dataclass(frozen=True)
    class LLMProviderConfig:
        api_key: str
        base_url: str
        model: str
        def __repr__(self) -> str: ...  # 隐藏 api_key，输出 "sk-***"

    @dataclass(frozen=True)
    class LLMRolesConfig:
        planner: str       # provider name
        grounding: str
        vision: str
        search: str

    @dataclass(frozen=True)
    class SandboxConfig:
        enabled: bool
        root: str
        read_only_paths: Sequence[str]
        ask_for_permission: bool

    @dataclass(frozen=True)
    class GUIConfig:
        lock_timeout: float
        wait_interval: float
        screenshot_without_lock: bool
        enable_scroll_search: bool              # 是否启用滚动搜索
        max_scroll_attempts: int                # 滚动/翻页上限
        scroll_step_pixels: int                 # 每次滚动步长（像素）

    @dataclass(frozen=True)
    class TerminalConfig:
        command_timeout: float
        silence_timeout: float | None
        default_shell: str

    @dataclass(frozen=True)
    class MemoryConfig:
        sliding_window_size: int
        max_facts_in_slot: int

    @dataclass(frozen=True)
    class OSWorldConfig:
        enabled: bool
        provider: Literal["docker", "vmware", "virtualbox"]
        os_type: str                          # "Ubuntu" / "Windows" / "macOS"
        screen_size: tuple[int, int]          # (1920, 1080)
        headless: bool
        action_space: str                     # "computer_13"
        docker_image: str | None              # 自定义镜像名，None 用默认

    @dataclass(frozen=True)
    class StrataConfig:
        log_level: str
        audit_log: str
        trash_dir: str
        providers: Mapping[str, LLMProviderConfig]
        roles: LLMRolesConfig
        sandbox: SandboxConfig
        gui: GUIConfig
        terminal: TerminalConfig
        memory: MemoryConfig
        osworld: OSWorldConfig
        max_loop_iterations: int
        dangerous_patterns: Sequence[str]
        auto_confirm_level: Literal["none", "low", "medium", "high"]
        def __repr__(self) -> str: ...  # 委托各子 config 的安全 repr

    def load_config(path: str | None = None) -> StrataConfig: ...
    def get_default_config() -> StrataConfig: ...
    ```
  契约:
    require:
      - `load_config`: path 为 None 或已存在文件
    ensure:
      - `load_config`: 返回值所有字段非 None
      - `load_config`: providers 中每个 api_key 非空字符串
      - `load_config`: Required 字段缺失 → ConfigError（不静默填充默认值）
    invariant: StrataConfig 为 frozen dataclass

  Required 字段（缺失即 ConfigError）:
    - `providers` 中至少一个 provider 的 api_key + base_url + model
    - `roles.planner` / `roles.grounding` / `roles.vision` / `roles.search` 引用的 provider 名必须存在于 `providers` 中
    - `roles.vision` 指向的 provider 必须额外校验 api_key 非空（感知层完全依赖 VLM）
    - `sandbox.root`（安全策略不可绕过）
    - `terminal.default_shell`

  Optional 字段（可用默认值）:
    - `log_level`（默认 "INFO"）
    - `gui.lock_timeout`（默认 10.0）、`gui.wait_interval`（默认 0.5）
    - `gui.enable_scroll_search`（默认 true）、`gui.max_scroll_attempts`（默认 10）、`gui.scroll_step_pixels`（默认 300）
    - `memory.*`（默认 sliding_window_size=5 等）
    - `osworld.*`（默认 enabled=false）
验证矩阵:
  L0 类型: mypy 验证 StrataConfig 所有嵌套类型、Literal 值域
  L1 契约:
    - `@require(lambda path: path is None or Path(path).is_file())`
    - `@ensure(lambda result: all(p.api_key for p in result.providers.values()))`
  L2 属性:
    - `prop_config_roundtrip`: `serialize(load(toml_str)) == normalize(toml_str)`
    - `prop_default_config_valid`: `get_default_config()` 返回值满足所有契约
  L3 示例:
    - `test_load_example_config`: Given config.example.toml / When load / Then 解析成功（api_key 为占位符）
    - `test_optional_field_uses_default`: Given TOML 含 providers+roles+sandbox.root+terminal.default_shell 但缺 log_level / When load / Then log_level = "INFO"
    - `test_required_field_missing_raises`: Given TOML 缺 sandbox.root / When load / Then ConfigError
    - `test_invalid_toml_raises_config_error`: Given 非法 TOML / When load / Then ConfigError
    - `test_expand_tilde`: Given path 含 `~` / When load / Then 正确展开
Strategy 变更:
  - 新增 `st_config_toml()`: 生成有效/边界 TOML 配置字符串
  - 新增 `st_strata_config()`: 生成 StrataConfig 实例
异常设计: `ConfigError` 已在 Step 1.2 定义
依赖标注: 依赖 Step 1.3
Review 检查项:
- `~` 路径展开正确处理
- 默认值覆盖完整（任何字段缺失都不 crash）
- `config.example.toml` 与 StrataConfig 字段一一对应，含 `[osworld]` 段和 `[gui]` 滚动搜索字段，无 `[a11y]` 段
- `LLMProviderConfig.__repr__` 和 `StrataConfig.__repr__` 隐藏 api_key（输出 `"sk-***"`），覆盖 frozen dataclass 默认 repr
- `OSWorldConfig` 默认值：`enabled=false, provider="docker", os_type="Ubuntu", screen_size=(1920,1080), headless=true, action_space="computer_13", docker_image=None`
- 无 `A11yConfig`——纯 VLM 架构不需要 A11y 配置
- `GUIConfig` 包含滚动搜索配置：`enable_scroll_search`, `max_scroll_attempts`, `scroll_step_pixels`

---

## Phase 2：LLM 提供商抽象

### Step 2.1: LLMProvider Protocol 与 OpenAI 兼容实现

目标: 定义 LLM 调用的 Protocol 接口和基于 openai SDK 的统一实现

新建/修改文件:
- `strata/llm/__init__.py`（新建）
- `strata/llm/provider.py`（新建）
- `tests/test_llm/__init__.py`（新建）
- `tests/test_llm/test_provider.py`（新建）

先读文件清单:
- `strata/core/config.py`（LLMProviderConfig）
- `strata/core/types.py`
- `strata/core/errors.py`（LLMError）
- `strata/__init__.py`

API 规格:
  签名:
    ```python
    from typing import Protocol, Sequence, AsyncIterator

    @dataclass(frozen=True)
    class ChatMessage:
        role: Literal["system", "user", "assistant"]
        content: str
        images: Sequence[bytes] = field(default_factory=tuple)

    @dataclass(frozen=True)
    class ChatResponse:
        content: str
        model: str
        usage: Mapping[str, int]
        finish_reason: str

    class LLMProvider(Protocol):
        def chat(
            self,
            messages: Sequence[ChatMessage],
            temperature: float = 0.7,
            max_tokens: int | None = None,
            json_mode: bool = False,
        ) -> ChatResponse: ...

        @property
        def model_name(self) -> str: ...

    class OpenAICompatProvider:
        def __init__(self, config: LLMProviderConfig) -> None: ...
        def chat(
            self,
            messages: Sequence[ChatMessage],
            temperature: float = 0.7,
            max_tokens: int | None = None,
            json_mode: bool = False,
        ) -> ChatResponse: ...

        @property
        def model_name(self) -> str: ...
    ```
  契约:
    require:
      - `chat`: messages 非空，temperature ∈ [0, 2]
    ensure:
      - `chat`: 返回值 content 非空字符串
  验证矩阵:
    L0 类型: mypy 验证 OpenAICompatProvider 实现 LLMProvider Protocol
    L1 契约:
      - `@require(lambda messages: len(messages) > 0)`
      - `@require(lambda temperature: 0.0 <= temperature <= 2.0)`
      - `@ensure(lambda result: len(result.content) > 0)`
    L2 属性:
      - `prop_provider_satisfies_protocol`: OpenAICompatProvider 是 LLMProvider 的 structural subtype
    L3 示例:
      - `test_provider_init_from_config`: Given LLMProviderConfig / When init / Then 正确设置 base_url 和 model
      - `test_provider_chat_mock`: Given mock openai client / When chat / Then 返回 ChatResponse
      - `test_provider_empty_messages_contract`: Given 空 messages / When chat / Then ViolationError
Strategy 变更:
  - 新增 `st_chat_message()`: 生成 ChatMessage
异常设计: `LLMError` 已在 Step 1.2 定义；新增 `LLMAPIError(LLMError)` 包装 openai 异常
依赖标注: 依赖 Step 1.4（config）
Review 检查项:
- Protocol 定义无实现细节泄漏
- api_key 不出现在日志/repr 中
- openai SDK 异常被包装为 LLMAPIError，不透传
- **`json_mode` Fail Fast**：若提供商不支持 `response_format` 且调用时 `json_mode=True`，必须立即抛出 `LLMFeatureNotSupportedError`，**禁止**降级为 system prompt 注入（下游 JSON 解析失败更难调试）。实现时探测各提供商对 `response_format={"type": "json_object"}` 的支持，可在 `__init__` 时发送一个最小测试请求或基于已知提供商白名单判断
- `images` 字段处理：`ChatMessage.images: Sequence[bytes]` 在 `OpenAICompatProvider.chat` 中转换为 OpenAI 兼容的 base64 data URL 格式（`{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`），供 VLM 视觉定位使用

### Step 2.2: 角色路由器

目标: 基于配置将 LLM 角色（planner/grounding/vision/search）映射到具体 Provider 实例

新建/修改文件:
- `strata/llm/router.py`（新建）
- `tests/test_llm/test_router.py`（新建）
- `tests/strategies.py`（修改，添加 st_llm_role_config）

先读文件清单:
- `strata/llm/provider.py`
- `strata/core/config.py`（StrataConfig, LLMRolesConfig）
- `strata/core/types.py`（LLMRole）

API 规格:
  签名:
    ```python
    class LLMRouter:
        def __init__(self, config: StrataConfig) -> None: ...
        def get_provider(self, role: LLMRole) -> LLMProvider: ...
        def plan(self, messages: Sequence[ChatMessage], **kwargs: object) -> ChatResponse: ...
        def ground(self, messages: Sequence[ChatMessage], **kwargs: object) -> ChatResponse: ...
        def see(self, messages: Sequence[ChatMessage], **kwargs: object) -> ChatResponse: ...
        def search(self, messages: Sequence[ChatMessage], **kwargs: object) -> ChatResponse: ...
    ```
  契约:
    require:
      - `get_provider`: role 必须在 config.roles 中有映射
      - `__init__`: config.providers 中必须包含所有 roles 引用的 provider name
    ensure:
      - `get_provider`: 返回的 Provider 对应正确的 model
  验证矩阵:
    L0 类型: mypy 验证 LLMRole literal、get_provider 返回类型
    L1 契约:
      - `@require(lambda self, role: role in ("planner", "grounding", "vision", "search"))`
      - `@ensure(lambda self, role, result: result.model_name == expected_model)`
    L2 属性:
      - `prop_router_role_deterministic`: 相同 role 连续调用返回相同 Provider 实例
    L3 示例:
      - `test_router_dispatches_to_correct_provider`: Given config with 3 providers / When get_provider("planner") / Then 返回 DeepSeek provider
      - `test_router_missing_provider_raises`: Given config 缺少 provider / When init / Then ConfigError
Strategy 变更:
  - 新增 `st_llm_role_config()`: 生成 LLMRolesConfig 变体
异常设计: 无新增
依赖标注: 依赖 Step 2.1
Review 检查项:
- Provider 实例缓存：使用内部 `dict[str, OpenAICompatProvider]` 存储已创建的 Provider，避免重复构造 `openai.OpenAI` 客户端
- 快捷方法（plan/ground/see/search）正确委托
- 多 Provider 共用同一 api_key 时只创建一个 client 实例

---

## Phase 3：环境抽象层（L4）

### Step 3.1: Protocol 定义 + EnvironmentFactory 骨架

目标: 定义 5 个环境适配器 Protocol（无 A11y）+ EnvironmentBundle + EnvironmentFactory + 平台目录结构

新建/修改文件:
- `strata/env/__init__.py`（新建）
- `strata/env/protocols.py`（新建）
- `strata/env/factory.py`（新建，EnvironmentFactory 骨架）
- `strata/env/linux/__init__.py`（新建）
- `strata/env/macos/__init__.py`（新建）
- `tests/test_env/__init__.py`（新建）
- `tests/test_env/test_protocols.py`（新建）
- `tests/test_env/test_factory.py`（新建）

先读文件清单:
- `strata/core/types.py`（WindowInfo, FileInfo, AppInfo, CommandResult, Coordinate, ScreenRegion）
- `strata/core/errors.py`（UnsupportedPlatformError）

API 规格:
  签名:
    ```python
    class IGUIAdapter(Protocol):
        def click(self, x: float, y: float, button: str = "left") -> None: ...
        def double_click(self, x: float, y: float) -> None: ...
        def move_mouse(self, x: float, y: float) -> None: ...
        def type_text(self, text: str, interval: float = 0.05) -> None: ...
        def press_key(self, key: str) -> None: ...
        def hotkey(self, *keys: str) -> None: ...
        def scroll(self, delta_x: int, delta_y: int) -> None: ...
        def get_screen_size(self) -> tuple[int, int]: ...
        def capture_screen(self, region: ScreenRegion | None = None) -> bytes: ...
        def get_dpi_scale_for_point(self, x: float, y: float) -> float: ...

    class ITerminalAdapter(Protocol):
        def run_command(self, command: str, cwd: str | None = None,
                        env: Mapping[str, str] | None = None,
                        timeout: float = 300.0,
                        silence_timeout: float | None = 30.0) -> CommandResult: ...
        def open_terminal(self, cwd: str | None = None) -> str: ...
        def send_to_terminal(self, session_id: str, text: str) -> None: ...
        def read_terminal_output(self, session_id: str, timeout: float = 1.0) -> str: ...
        def close_terminal(self, session_id: str) -> None: ...

    class IFileSystemAdapter(Protocol):
        def read_file(self, path: str) -> str: ...
        def write_file(self, path: str, content: str, encoding: str = "utf-8") -> None: ...
        def list_directory(self, path: str, pattern: str | None = None) -> Sequence[FileInfo]: ...
        def move_to_trash(self, path: str) -> str: ...
        def restore_from_trash(self, trash_path: str) -> None: ...
        def get_file_info(self, path: str) -> FileInfo: ...

    class IAppManagerAdapter(Protocol):
        def launch_app(self, app_name: str, args: Sequence[str] | None = None) -> str: ...
        def close_app(self, app_identifier: str) -> None: ...
        def get_running_apps(self) -> Sequence[AppInfo]: ...
        def switch_to_app(self, app_identifier: str) -> None: ...

    class ISystemAdapter(Protocol):
        def get_clipboard_text(self) -> str: ...
        def set_clipboard_text(self, text: str) -> None: ...
        def get_environment_variable(self, name: str) -> str | None: ...
        def set_environment_variable(self, name: str, value: str) -> None: ...
        def get_cwd(self) -> str: ...
        def set_cwd(self, path: str) -> None: ...

    # ── 环境捆绑 + 工厂 ──

    @dataclass(frozen=True)
    class EnvironmentBundle:
        gui: IGUIAdapter
        terminal: ITerminalAdapter
        filesystem: IFileSystemAdapter
        app_manager: IAppManagerAdapter
        system: ISystemAdapter

    class EnvironmentFactory:
        @staticmethod
        def create(config: StrataConfig) -> EnvironmentBundle: ...
        # 实现伪代码：
        #   if sys.platform == "linux":
        #       from strata.env.linux import ...
        #       return EnvironmentBundle(gui=..., terminal=..., ...)
        #   else:
        #       raise UnsupportedPlatformError(
        #           f"当前仅支持 Linux，{sys.platform} 适配器未实现"
        #       )
        # 关键：darwin/win32 在入口处直接抛出，不导入 macos/ 子包，不走到 stub
    ```
  契约: 无（Protocol 无运行时约束）
验证矩阵:
  L0 类型: mypy 验证 Protocol 签名完整性，runtime_checkable 标记
  L1 契约: 无
  L2 属性: 无
  L3 示例:
    - `test_protocol_runtime_checkable`: Given mock 实现 / When isinstance 检查 / Then True
    - `test_protocol_structural_conformance`: Given 正确/错误签名 mock / When mypy 检查 / Then pass/fail
    - `test_factory_unsupported_win32`: Given sys.platform="win32" mock / When create / Then UnsupportedPlatformError（消息含"当前仅支持 Linux"）
    - `test_factory_unsupported_darwin`: Given sys.platform="darwin" mock / When create / Then UnsupportedPlatformError（消息含"当前仅支持 Linux"）
    - `test_factory_linux_returns_bundle`: Given sys.platform="linux" / When create / Then 返回 EnvironmentBundle 所有字段非 None
Strategy 变更: 无
异常设计: `UnsupportedPlatformError` 已在 Step 1.2 定义
依赖标注: 依赖 Step 1.3（types）
Review 检查项:
- 所有 Protocol 标记 `@runtime_checkable`
- 入参容器用 Sequence/Mapping，返回值可用具体类型
- `capture_screen` 返回 `bytes`（PNG 格式），不引入 PIL 依赖到 Protocol 层
- `EnvironmentFactory.create` 使用延迟导入（`import strata.env.linux` 仅在 `sys.platform == "linux"` 时执行）
- **Fail-fast 平台检查**：`sys.platform != "linux"` 时在 `create()` 入口处直接 `raise UnsupportedPlatformError("当前仅支持 Linux，{platform} 适配器未实现")`，不导入 macos 子包，不走到 stub 的 `NotImplementedError`——用户启动时必须收到明确的平台不支持错误，而非某个 stub 方法的误导性 `NotImplementedError`
- `EnvironmentBundle` 为 frozen dataclass，5 个字段全部为 Protocol 类型（无 A11y）
- 无 `IA11yAdapter`——纯 VLM 架构不需要可访问性 API 抽象

### Step 3.2: SandboxGuard

目标: 实现独立的路径规范化 + 沙盒检查模块，供 FileSystem 和其他组件注入使用

新建/修改文件:
- `strata/core/sandbox.py`（新建）
- `tests/test_core/test_sandbox.py`（新建）
- `tests/strategies.py`（修改，添加 st_sandbox_path）

先读文件清单:
- `strata/core/config.py`（SandboxConfig）
- `strata/core/errors.py`（SandboxViolationError）

API 规格:
  签名:
    ```python
    class SandboxGuard:
        def __init__(self, config: SandboxConfig) -> None: ...
        def check_path(self, path: str, write: bool = False) -> str: ...  # 返回规范化绝对路径
        def is_within_sandbox(self, path: str) -> bool: ...
    ```
  契约:
    require:
      - `check_path`: path 非空
    ensure:
      - `check_path`: 返回值为绝对路径
      - `check_path`: 返回值在 sandbox.root 内（或 write=False 且在 read_only_paths 内）
      - `check_path`: write=True + path 在 read_only_paths 内 → SandboxViolationError
验证矩阵:
  L0 类型: mypy 验证签名
  L1 契约:
    - `@ensure(lambda result: os.path.isabs(result))`
    - `@ensure` 沙盒包含检查
  L2 属性:
    - `prop_normalization_idempotent`: `check(check(p)) == check(p)`（对合法路径）
    - `prop_traversal_always_caught`: 任何含 `..` 导致逃逸的路径 → SandboxViolationError
  L3 示例:
    - `test_normal_path_passes`: Given 沙盒内路径 / When check / Then 返回规范化路径
    - `test_dotdot_escape_blocked`: Given 沙盒内 + ../../ / When check / Then SandboxViolationError
    - `test_symlink_escape_blocked`: Given 沙盒内符号链接→外部 / When check / Then SandboxViolationError
    - `test_read_only_path_allows_read`: Given /etc/os-release in read_only_paths / When check(write=False) / Then 通过
    - `test_read_only_path_blocks_write`: Given /etc/os-release / When check(write=True) / Then SandboxViolationError
Strategy 变更:
  - 新增 `st_sandbox_path(sandbox_root)`: 生成各种路径变体（含 `..`、符号链接、绝对/相对）
异常设计: `SandboxViolationError` 已在 Step 1.2 定义
依赖标注: 依赖 Step 1.4（SandboxConfig）
Review 检查项:
- `os.path.realpath()` 解析符号链接
- `os.path.commonpath()` 严格前缀比对
- `# CONVENTION: 不防御硬链接穿透和 TOCTOU — 自用非对抗环境`
- 模块位置为 `strata/core/sandbox.py`（跨层共用，env 和 harness 均可依赖）
- **安全边界声明**：`SandboxGuard` 类文档字符串中必须包含以下声明：`"""安全边界：SandboxGuard 是框架内所有文件 I/O 的唯一授权检查点。任何文件操作必须通过 SandboxedFileSystemAdapter（其内部注入 SandboxGuard）执行。框架其他组件严禁绕过 filesystem 适配器直接使用 pathlib / open / os.* 进行文件读写。此约束无运行时强制，依赖架构纪律。"""`
- `EnvironmentFactory.create` 中同样添加内联注释：`# SECURITY: 所有文件 I/O 必须通过 filesystem 适配器，严禁直接操作路径`

### Step 3.3: Terminal PTY 实现

目标: 实现 `ITerminalAdapter`——PTY + prompt token + 退出码捕获 + 静默超时

新建/修改文件:
- `strata/env/terminal_pty.py`（新建）
- `tests/test_env/test_terminal_pty.py`（新建）
- `tests/strategies.py`（修改，添加 st_command_result）

先读文件清单:
- `strata/env/protocols.py`（ITerminalAdapter）
- `strata/core/types.py`（CommandResult）
- `strata/core/config.py`（TerminalConfig）
- `strata/core/errors.py`（CommandTimeoutError, SilenceTimeoutError）

API 规格:
  签名:
    ```python
    class PTYTerminalAdapter:
        def __init__(self, config: TerminalConfig) -> None: ...
        # 实现 ITerminalAdapter 所有方法
    ```
  契约:
    require:
      - `run_command`: command 非空字符串
      - `run_command`: timeout > 0
      - `run_command`: command 中若含 sudo 则必须含 -n
    ensure:
      - `run_command`: returncode 为 int
      - `run_command`: timed_out 和 interrupted_by_silence 不同时为 True
    invariant: 无
验证矩阵:
  L0 类型: mypy 验证 PTYTerminalAdapter 实现 ITerminalAdapter Protocol
  L1 契约:
    - `@require(lambda command: len(command.strip()) > 0)`
    - `@require(lambda command: "sudo" not in command or "-n" in command)`
    - `@ensure(lambda result: isinstance(result.returncode, int))`
    - `@ensure(lambda result: not (result.timed_out and result.interrupted_by_silence))`
  L2 属性:
    - `prop_echo_captures_exit_code`: `echo hello; exit N` 返回 returncode == N
  L3 示例:
    - `test_run_echo_success`: Given "echo hello" / When run / Then returncode=0, stdout 含 "hello"
    - `test_run_false_returns_1`: Given "false" / When run / Then returncode=1
    - `test_run_timeout`: Given "sleep 100" timeout=1 / When run / Then timed_out=True
    - `test_silence_timeout_triggers_interrupt`: Given 阻塞命令 silence_timeout=1 / When run / Then interrupted_by_silence=True
    - `test_sudo_without_n_rejected`: Given "sudo rm" / When run / Then ViolationError
Strategy 变更:
  - 新增 `st_command_result()`: 生成 CommandResult 各种组合
异常设计: `CommandTimeoutError` / `SilenceTimeoutError` 已在 Step 1.2 定义
依赖标注: 依赖 Step 3.1
Review 检查项:
- Prompt token 使用 UUID 保证唯一性
- PTY 读取使用非阻塞 I/O + select/poll
- 超时后正确清理子进程（SIGTERM → SIGKILL）
- 命令模板不引入 shell injection 风险（prompt token 只在输出匹配，不在命令中拼接用户输入）

### Step 3.4: FileSystem 实现（沙盒化）

目标: 实现 `IFileSystemAdapter`——通过注入的 `SandboxGuard` 进行路径检查 + 删除转回收站

新建/修改文件:
- `strata/env/filesystem.py`（新建）
- `tests/test_env/test_filesystem.py`（新建）

先读文件清单:
- `strata/env/protocols.py`（IFileSystemAdapter）
- `strata/core/sandbox.py`（SandboxGuard）
- `strata/core/errors.py`（SandboxViolationError）
- `strata/core/types.py`（FileInfo）

API 规格:
  签名:
    ```python
    class SandboxedFileSystemAdapter:
        def __init__(self, guard: SandboxGuard, trash_dir: str) -> None: ...
        # 实现 IFileSystemAdapter 所有方法
        # 所有路径操作委托 self._guard.check_path()
    ```
  契约:
    require:
      - 所有文件操作: path 经 SandboxGuard.check_path() 验证通过
    ensure:
      - `move_to_trash`: 原路径不存在，trash 目标存在
      - `write_file`: 文件存在且内容匹配
    invariant: 沙盒路径检查不可绕过（SandboxGuard 通过构造函数注入，无 fallback）
验证矩阵:
  L0 类型: mypy 验证 SandboxedFileSystemAdapter 实现 IFileSystemAdapter Protocol
  L1 契约:
    - 所有路径方法开头调用 `self._guard.check_path(path, write=...)`
    - `@ensure` on `write_file`: `Path(path).read_text() == content`
  L2 属性:
    - `prop_symlink_traversal_blocked`: 符号链接指向沙盒外 → SandboxViolationError（委托 SandboxGuard）
  L3 示例:
    - `test_read_write_within_sandbox`: Given 沙盒内路径 / When write+read / Then 内容匹配
    - `test_path_traversal_blocked`: Given "../../etc/passwd" / When read / Then SandboxViolationError
    - `test_symlink_escape_blocked`: Given 沙盒内符号链接指向沙盒外 / When read / Then SandboxViolationError
    - `test_delete_moves_to_trash`: Given 沙盒内文件 / When move_to_trash / Then 文件在 trash_dir
Strategy 变更: 无（使用 Step 3.2 的 st_sandbox_path）
异常设计: `SandboxViolationError` 已在 Step 1.2 定义
依赖标注: 依赖 Step 3.1（Protocol）+ Step 3.2（SandboxGuard）
Review 检查项:
- **零路径检查逻辑内联**：所有路径验证 100% 委托 `SandboxGuard.check_path()`，FileSystemAdapter 自身不做任何 realpath/commonpath 操作
- trash 目录保留原始路径元数据（JSON sidecar）
- 空文件/空目录/大文件边界情况

### Step 3.5: Linux System 适配器实现

目标: 实现 `ISystemAdapter`（Linux）——剪贴板 (xclip) + 环境变量 + CWD 管理

新建/修改文件:
- `strata/env/linux/system.py`（新建）
- `tests/test_env/test_linux/__init__.py`（新建）
- `tests/test_env/test_linux/test_system.py`（新建）

先读文件清单:
- `strata/env/protocols.py`（ISystemAdapter）

API 规格:
  签名:
    ```python
    class LinuxSystemAdapter:
        def __init__(self) -> None: ...
        # 实现 ISystemAdapter 所有方法
        # __init__ 时检测 xclip/xsel 可用性，不可用 → EnvironmentError
    ```
  契约:
    require:
      - `__init__`: `shutil.which("xclip")` 或 `shutil.which("xsel")` 返回非 None（否则 EnvironmentError）
      - `set_cwd`: path 必须是存在的目录
    ensure:
      - `set_clipboard_text` → `get_clipboard_text`: 往返一致
      - `set_cwd` → `get_cwd`: 往返一致
验证矩阵:
  L0 类型: mypy 验证实现 Protocol
  L1 契约:
    - `@require(lambda path: Path(path).is_dir())` on `set_cwd`
    - `@ensure` clipboard roundtrip
  L2 属性: 无（剪贴板为外部状态，不适合 property test）
  L3 示例:
    - `test_env_var_roundtrip`: Given name+value / When set+get / Then 匹配
    - `test_cwd_roundtrip`: Given valid dir / When set+get / Then 匹配
Strategy 变更: 无
异常设计: 无新增
依赖标注: 依赖 Step 3.1
Review 检查项:
- 剪贴板实现: subprocess 调用 `xclip`/`xsel`
- **Fail Fast**：`__init__` 时 `shutil.which("xclip")` 和 `shutil.which("xsel")` 均为 None → 立即抛出 `EnvironmentError("clipboard tool not found: install xclip or xsel")`，拒绝在功能残缺环境下运行

### Step 3.6: 平台特定 GUI + AppManager 薄 stub

目标: 提供 Linux 和 macOS 子包中 `IGUIAdapter`、`IAppManagerAdapter` 的最小 stub 实现（`raise NotImplementedError` 为主，Phase 6 填充 Linux 实现）

新建/修改文件:
- `strata/env/linux/gui.py`（新建，stub）
- `strata/env/linux/app_manager.py`（新建，stub）
- `strata/env/macos/gui.py`（新建，stub）
- `strata/env/macos/app_manager.py`（新建，stub）
- `strata/env/macos/system.py`（新建，stub）

先读文件清单:
- `strata/env/protocols.py`

API 规格:
  签名: 各类实现 Protocol 方法，方法体为 `raise NotImplementedError("Phase 6")` (Linux) 或 `raise NotImplementedError("macOS support planned")` (macOS)
  契约: 无（stub）
验证矩阵:
  L0 类型: mypy 验证 structural conformance
  L1 契约: 无
  L2 属性: 无
  L3 示例:
    - `test_stub_raises_not_implemented`: 各 stub 方法调用 → NotImplementedError（**不是** RuntimeError）
Strategy 变更: 无
异常设计: 无
依赖标注: 依赖 Step 3.1，可与 Step 3.2-3.5 并行
Review 检查项:
- 所有 Protocol 方法均有 stub 实现，使用 `raise NotImplementedError`（Python 标准语义）
- 类型签名与 Protocol 完全一致
- 无 A11y stub——彻底删除，不留未实现的抽象
- `linux/gui.py` stub 中标注 `# CONVENTION: Phase 6 实现时须检查 DISPLAY 环境变量 — headless 环境无显示器`

---

## Phase 4：执行编排核心（L2 Harness）

### Step 4.1: 全局 + 任务级状态机

目标: 实现带类型安全转换的状态机，支持事件驱动转换和非法转换拒绝

新建/修改文件:
- `strata/harness/__init__.py`（新建）
- `strata/harness/state_machine.py`（新建）
- `tests/test_harness/__init__.py`（新建）
- `tests/test_harness/test_state_machine.py`（新建）
- `tests/strategies.py`（修改，添加 st_global_state_event_seq）

先读文件清单:
- `strata/core/types.py`（GlobalState, TaskState）
- `strata/core/errors.py`（StateTransitionError）

API 规格:
  签名:
    ```python
    GlobalEvent = Literal["receive_goal", "plan_ready", "user_confirm", "user_revise",
                          "task_dispatched", "task_done", "task_failed", "recovered",
                          "escalated", "user_decision", "user_abort", "all_done", "unrecoverable"]
    TaskEvent = Literal["start", "succeed", "fail", "skip"]

    VALID_GLOBAL_TRANSITIONS: Final[Mapping[GlobalState, Mapping[GlobalEvent, GlobalState]]]
    VALID_TASK_TRANSITIONS: Final[Mapping[TaskState, Mapping[TaskEvent, TaskState]]]

    class StateMachine[S, E]:
        def __init__(self, initial: S, transitions: Mapping[S, Mapping[E, S]]) -> None: ...
        @property
        def state(self) -> S: ...
        def transition(self, event: E) -> S: ...
        def can_transition(self, event: E) -> bool: ...
        def reset(self) -> None: ...

    def create_global_state_machine() -> StateMachine[GlobalState, GlobalEvent]: ...
    def create_task_state_machine() -> StateMachine[TaskState, TaskEvent]: ...
    ```
  契约:
    require:
      - `transition`: `can_transition(event)` 为 True
    ensure:
      - `transition`: 返回值 == 新状态
      - `transition`: 新状态 == transitions[old_state][event]
    invariant:
      - `state` 始终在合法状态集内
验证矩阵:
  L0 类型: mypy 验证泛型 StateMachine[S, E]、Literal 状态/事件值域
  L1 契约:
    - `@require(lambda self, event: self.can_transition(event))` on `transition`
    - `@ensure(lambda self, event, result, OLD: result == self._transitions[OLD.state][event])` on `transition`
  L2 属性:
    - `prop_valid_sequence_never_errors`: 任意合法事件序列不抛 StateTransitionError
    - `prop_invalid_event_always_rejects`: 非法事件序列必抛 StateTransitionError
    - `prop_reset_returns_to_initial`: transition N 次后 reset → state == initial
  L3 示例:
    - `test_global_happy_path`: INIT → receive_goal → PLANNING → plan_ready → ... → COMPLETED
    - `test_invalid_transition_raises`: INIT → task_dispatched → StateTransitionError
    - `test_recovery_path`: EXECUTING → task_failed → RECOVERING → recovered → SCHEDULING
Strategy 变更:
  - 新增 `st_global_state_event_seq()`: 生成合法/非法事件序列
异常设计: `StateTransitionError` 已在 Step 1.2 定义
依赖标注: 依赖 Step 1.3
Review 检查项:
- 转换表 `VALID_GLOBAL_TRANSITIONS` 与设计文档状态图完全一致
- 泛型 StateMachine 不泄漏内部状态（只通过 property 暴露）

### Step 4.2: 顺序调度器

目标: 实现线性任务调度器，支持 Repeat/If/ForEach 控制流节点的解释执行

新建/修改文件:
- `strata/harness/scheduler.py`（新建）
- `tests/test_harness/test_scheduler.py`（新建）

先读文件清单:
- `strata/harness/state_machine.py`
- `strata/core/types.py`（TaskGraph, TaskNode, TaskState, ActionResult）
- `strata/core/config.py`（max_loop_iterations）

API 规格:
  签名:
    ```python
    class TaskExecutor(Protocol):
        def execute(self, task: TaskNode, context: Mapping[str, object]) -> ActionResult: ...

    class LinearScheduler:
        def __init__(self, config: StrataConfig) -> None: ...
        def run(self, graph: TaskGraph, executor: TaskExecutor) -> Mapping[str, ActionResult]: ...
        def _execute_task(self, node: TaskNode, executor: TaskExecutor,
                          context: dict[str, object]) -> ActionResult: ...
        def _interpret_repeat(self, node: TaskNode, executor: TaskExecutor,
                              context: dict[str, object]) -> ActionResult: ...
        def _interpret_if(self, node: TaskNode, executor: TaskExecutor,
                          context: dict[str, object]) -> ActionResult: ...
        def _interpret_foreach(self, node: TaskNode, executor: TaskExecutor,
                               context: dict[str, object]) -> ActionResult: ...
    ```
  契约:
    require:
      - `run`: graph.tasks 非空
      - `_interpret_repeat`: node.max_iterations > 0
      - `_interpret_foreach`: node.max_iterations > 0
    ensure:
      - `run`: 返回的 Mapping 包含所有 task.id 的结果
      - `_interpret_repeat`: 迭代次数 ≤ max_iterations
    invariant: 循环迭代次数不超过 max_loop_iterations（全局上限）
验证矩阵:
  L0 类型: mypy 验证 TaskExecutor Protocol、泛型一致性
  L1 契约:
    - `@require(lambda graph: len(graph.tasks) > 0)` on `run`
    - `@require(lambda node: node.max_iterations is not None and node.max_iterations > 0)` on repeat/foreach
    - `@ensure(lambda result, graph: set(result.keys()) == {t.id for t in graph.tasks})` on `run`
  L2 属性:
    - `prop_linear_execution_order_preserved`: tasks 按 graph.tasks 顺序执行
  L3 示例:
    - `test_linear_three_tasks`: Given 3 primitive tasks / When run / Then 按序执行，全部成功
    - `test_repeat_max_iterations_guard`: Given repeat max=5 + always-true condition / When run / Then 恰好 5 次
    - `test_if_true_branch`: Given if(true) / When run / Then 执行 then_subtask
    - `test_if_false_branch`: Given if(false) / When run / Then 执行 else_subtask
    - `test_foreach_iterates_list`: Given foreach over 3 items / When run / Then 执行 3 次
Strategy 变更: 无（使用 st_task_graph）
异常设计: 新增 `MaxIterationsExceededError(HarnessError)`
依赖标注: 依赖 Step 4.1
Review 检查项:
- repeat/foreach 迭代计数器不可被外部篡改
- output_var 正确写入 context
- compound task 的 method 展开为子任务序列

### Step 4.3: GUI 全局互斥锁

目标: 实现 GUI 操作的互斥锁，支持可抢占的原子事务（wait → check → act 不释放锁）

新建/修改文件:
- `strata/harness/gui_lock.py`（新建）
- `tests/test_harness/test_gui_lock.py`（新建）

先读文件清单:
- `strata/core/config.py`（GUIConfig）
- `strata/core/errors.py`（GUILockTimeoutError）

API 规格:
  签名:
    ```python
    class GUILock:
        def __init__(self, config: GUIConfig) -> None: ...
        def acquire(self, timeout: float | None = None) -> bool: ...
        def release(self) -> None: ...
        def __enter__(self) -> "GUILock": ...
        def __exit__(self, *args: object) -> None: ...

    class AtomicGUITransaction:
        """可抢占原子事务：wait→check→act 期间不释放锁"""
        def __init__(self, lock: GUILock, config: GUIConfig) -> None: ...
        def wait_and_act(
            self,
            check_fn: Callable[[], bool],
            act_fn: Callable[[], ActionResult],
            max_wait: float = 30.0,
            auxiliary_fn: Callable[[], None] | None = None,
        ) -> ActionResult: ...
        # auxiliary_fn: check_fn 返回 False 时，在释放锁之前执行的辅助动作
        # 典型用途：VLM 确认目标不存在时执行滚动/翻页
        # 调用顺序：acquire → check_fn() → False → auxiliary_fn() → release → sleep → 重试
    ```
  契约:
    require:
      - `release`: 锁已被当前线程持有
      - `wait_and_act`: max_wait > 0
    ensure:
      - `wait_and_act`: 返回时锁已释放
      - `acquire` 成功 → `locked()` 为 True
验证矩阵:
  L0 类型: mypy 验证 context manager 协议、Callable 签名
  L1 契约:
    - `@ensure(lambda self, result: not self._lock.locked())` on `wait_and_act`
  L2 属性: 无（涉及线程，Hypothesis 不适合）
  L3 示例:
    - `test_lock_acquire_release`: Given lock / When acquire+release / Then 成功
    - `test_lock_timeout`: Given 已被占用的 lock / When acquire(timeout=0.1) / Then False
    - `test_context_manager`: Given lock / When with 语句 / Then 自动释放
    - `test_atomic_transaction_wait_then_act`: Given check 在第 3 次返回 True / When wait_and_act / Then act 在锁保护下执行
    - `test_atomic_transaction_timeout`: Given check 始终 False / When wait_and_act(max_wait=0.5) / Then GUILockTimeoutError
Strategy 变更: 无
异常设计: `GUILockTimeoutError` 已在 Step 1.2 定义
依赖标注: 依赖 Step 1.4（config），可与 Step 4.1/4.2 并行
Review 检查项:
- 使用 `threading.RLock`（可重入，防止同一线程 deadlock）
- wait_and_act 的轮询间隔使用 config.gui.wait_interval
- 异常路径也正确释放锁（`try/finally` 保证）
- **原子性不变量文档化**：`wait_and_act` 方法体内须有注释明确标注原子性边界——`check_fn` 和 `act_fn` 均在锁保护下执行；check 失败时先执行 `auxiliary_fn`（若有），然后释放锁 + sleep；check 成功时**不释放锁**直接执行 act_fn，然后在 finally 中释放。后续维护者不得在 check 成功和 act 之间插入释放锁的代码
- **auxiliary_fn 在锁内执行**：确保辅助动作（如滚动）不与其他 GUI 任务竞争
- **纯视觉 check_fn 模式**：check_fn 的典型实现为"截图 → VLM 询问'元素是否已出现'"，每次检查产生一次 VLM 调用

### Step 4.4: 原子持久化

目标: 实现状态持久化——tmp + fsync + replace 原子写，支持断点续传

新建/修改文件:
- `strata/harness/persistence.py`（新建）
- `tests/test_harness/test_persistence.py`（新建）

先读文件清单:
- `strata/core/types.py`（TaskGraph, GlobalState, TaskState）
- `strata/core/config.py`

API 规格:
  签名:
    ```python
    @dataclass(frozen=True)
    class Checkpoint:
        global_state: GlobalState
        task_states: Mapping[str, TaskState]
        context: Mapping[str, object]
        task_graph: TaskGraph
        timestamp: float

    class PersistenceManager:
        def __init__(self, state_dir: str) -> None: ...
        def save_checkpoint(self, checkpoint: Checkpoint) -> None: ...
        def load_checkpoint(self) -> Checkpoint | None: ...
        def clear_checkpoint(self) -> None: ...

    def atomic_write(path: str, content: bytes) -> None: ...
    ```
  契约:
    require:
      - `atomic_write`: path 的父目录必须存在
    ensure:
      - `atomic_write`: 写入后 path 存在且内容匹配
      - `atomic_write`: 不存在 .tmp 残留文件
      - `save_checkpoint` → `load_checkpoint`: 往返一致
验证矩阵:
  L0 类型: mypy 验证 Checkpoint 类型完整性
  L1 契约:
    - `@require(lambda path: Path(path).parent.is_dir())` on `atomic_write`
    - `@ensure(lambda path, content: Path(path).read_bytes() == content)` on `atomic_write`
    - `@ensure(lambda path: not Path(path + ".tmp").exists())` on `atomic_write`
  L2 属性:
    - `prop_checkpoint_roundtrip`: `load(save(checkpoint)) == checkpoint`
    - `prop_atomic_write_no_partial`: 模拟写入过程中的"中断"（仅 tmp 阶段）→ 原文件不变
  L3 示例:
    - `test_save_load_checkpoint`: Given checkpoint / When save+load / Then 一致
    - `test_load_no_checkpoint_returns_none`: Given 空目录 / When load / Then None
    - `test_atomic_write_creates_file`: Given 新路径 / When atomic_write / Then 文件存在
    - `test_no_tmp_residue_after_write`: Given path / When atomic_write / Then .tmp 不存在
Strategy 变更: 无
异常设计: 无新增
依赖标注: 依赖 Step 1.3
Review 检查项:
- `os.fsync()` 在 `os.replace()` 之前
- JSON 序列化 Checkpoint 时处理所有自定义类型
- 并发安全：虽然初版单线程，但 atomic_write 本身对文件系统是原子的

### Step 4.5: 错误恢复管道

目标: 实现 5 级错误恢复管道（重试 → 替代动作 → 局部微调 → 跳过 → 用户介入）

新建/修改文件:
- `strata/harness/recovery.py`（新建）
- `tests/test_harness/test_recovery.py`（新建）

先读文件清单:
- `strata/harness/state_machine.py`（GlobalState 转换）
- `strata/core/types.py`（ActionResult, TaskNode）
- `strata/core/config.py`（auto_confirm_level）

API 规格:
  签名:
    ```python
    class RecoveryLevel(enum.IntEnum):
        RETRY = 1
        ALTERNATIVE = 2
        REPLAN = 3
        SKIP = 4
        USER_INTERVENTION = 5

    @dataclass(frozen=True)
    class RecoveryAction:
        level: RecoveryLevel
        description: str
        replacement_task: TaskNode | None = None

    class RecoveryPipeline:
        def __init__(self, config: StrataConfig,
                     adjuster: "Callable[[TaskNode, Exception], Sequence[TaskNode]]") -> None: ...
        def attempt_recovery(
            self,
            failed_task: TaskNode,
            error: Exception,
            attempt_count: int,
        ) -> RecoveryAction: ...
    ```
  契约:
    require:
      - `__init__`: adjuster 为必需参数（非 Optional），未提供 → TypeError
      - `attempt_recovery`: attempt_count >= 0
    ensure:
      - `attempt_recovery`: 返回值 level 随 attempt_count 递增（单调不减）
      - `attempt_recovery`: level == SKIP → replacement_task 为 None
      - `attempt_recovery`: level == REPLAN → 调用 adjuster → replacement_task 非 None
验证矩阵:
  L0 类型: mypy 验证 RecoveryLevel IntEnum、RecoveryAction 类型
  L1 契约:
    - `@require(lambda attempt_count: attempt_count >= 0)`
    - `@ensure(lambda result, attempt_count: result.level.value >= min(attempt_count + 1, 5))`
  L2 属性:
    - `prop_recovery_escalates_monotonically`: 连续调用 attempt_recovery(0..N) → level 单调不减
  L3 示例:
    - `test_first_attempt_retries`: Given attempt_count=0 / When recover / Then level=RETRY
    - `test_third_attempt_replans`: Given attempt_count=2 / When recover / Then level=REPLAN, adjuster 被调用, replacement_task 非 None
    - `test_fifth_attempt_user`: Given attempt_count=4 / When recover / Then level=USER_INTERVENTION
    - `test_adjuster_failure_escalates_to_skip`: Given adjuster 抛出 PlannerError / When attempt_count=2 / Then level=SKIP（adjuster 失败不阻塞恢复流程）
    - `test_adjuster_returns_empty_treated_as_failure`: Given adjuster 返回空序列 / When attempt_count=2 / Then level=SKIP
Strategy 变更: 无
异常设计: 无新增
依赖标注: 依赖 Step 4.1
Review 检查项:
- **RecoveryLevel 阈值硬编码**：5 级恢复管道的 attempt_count 阈值不可通过 config 调整（`RETRY: 0-1, ALTERNATIVE: 2, REPLAN: 3, SKIP: 4, USER_INTERVENTION: 5+`），保证恢复行为可预测、可调试
- **adjuster 为必需依赖**：构造函数不接受 `None`；Phase 4 测试时传入 mock adjuster，Phase 5 完成后注入真实 adjuster
- **Phase 4 mock adjuster 规格**：`tests/test_harness/test_recovery.py` 中提供 `_mock_adjuster`，其行为和契约如下：
  ```python
  def _mock_adjuster(failed_task: TaskNode, error: Exception) -> Sequence[TaskNode]:
      """最小 mock：始终返回 [failed_task]（原样回退，不做修改）。
      契约（Phase 5 真实实现必须兼容）：
        - 入参：failed_task 为待恢复任务，error 为导致失败的异常
        - 返回值：非空 Sequence[TaskNode]，至少包含一个任务
        - 异常传播：adjuster 自身失败 → 抛出 PlannerError，由 RecoveryPipeline 捕获并升级到 SKIP
        - 不可返回空序列（空序列语义模糊，RecoveryPipeline 应视为 adjuster 失败）
      """
      return [failed_task]
  ```
  Phase 5 的 `Adjuster.adjust()` 完成后，其签名 `(TaskNode, Exception) -> Sequence[TaskNode]` 必须与此 mock 类型签名一致。若不兼容，Phase 5 Gate 必须同步更新 Phase 4 测试
- USER_INTERVENTION 触发状态机 → WAITING_USER

### Step 4.6: 上下文管理器（最简版本）

目标: 实现变量绑定存储和基本的工作记忆，为 Phase 5/6 的 LLM 调用提供上下文；完整的滑动窗口压缩逻辑推迟到 Phase 7

新建/修改文件:
- `strata/harness/context.py`（新建）
- `tests/test_harness/test_context.py`（新建）

先读文件清单:
- `strata/core/config.py`（MemoryConfig）
- `strata/core/types.py`

API 规格:
  签名:
    ```python
    @dataclass(frozen=True)
    class ContextFact:
        key: str
        value: str
        timestamp: float

    class WorkingMemory:
        def __init__(self, config: MemoryConfig) -> None: ...
        def set_var(self, key: str, value: object) -> None: ...
        def get_var(self, key: str) -> object | None: ...
        def add_fact(self, key: str, value: str) -> None: ...
        def get_facts(self) -> Sequence[ContextFact]: ...
        def get_variables(self) -> Mapping[str, object]: ...
        def clear(self) -> None: ...
    ```
  契约:
    require:
      - `set_var` / `add_fact`: key 非空字符串
    ensure:
      - `get_facts`: 数量 ≤ max_facts_in_slot（FIFO 淘汰）
      - `set_var` → `get_var`: 往返一致
验证矩阵:
  L0 类型: mypy 验证签名
  L1 契约:
    - `@require(lambda key: len(key.strip()) > 0)` on set_var / add_fact
    - `@ensure(lambda self: len(self.get_facts()) <= self._config.max_facts_in_slot)` on add_fact
  L2 属性: 无
  L3 示例:
    - `test_var_roundtrip`: Given set_var("x", 42) / When get_var("x") / Then 42
    - `test_facts_fifo_eviction`: Given max=3, add 5 facts / When get_facts / Then 只有最后 3 条
    - `test_clear_resets_all`: Given vars+facts / When clear / Then 全部清空
Strategy 变更: 无
异常设计: 无新增
依赖标注: 依赖 Step 1.4（config），可与 Step 4.1-4.6 并行
Review 检查项:
- 此为最简版本，仅存储变量绑定 + 事实槽，不含滑动窗口和压缩逻辑（Phase 7 Step 7.2 完善）
- 变量值尽量保留具体值（如文件路径），而非摘要

---

## Phase 5：HTN 任务规划（L1 Planner）

### Step 5.1: 任务图数据模型与序列化

目标: 实现 TaskGraph 的 JSON 序列化/反序列化 + 方法注册表

新建/修改文件:
- `strata/planner/__init__.py`（新建）
- `strata/planner/htn.py`（新建）
- `tests/test_planner/__init__.py`（新建）
- `tests/test_planner/test_htn.py`（新建）

先读文件清单:
- `strata/core/types.py`（TaskGraph, TaskNode）
- `strata/core/errors.py`（PlannerError）

API 规格:
  签名:
    ```python
    def serialize_graph(graph: TaskGraph) -> str: ...     # JSON string
    def deserialize_graph(data: str) -> TaskGraph: ...
    def validate_graph(graph: TaskGraph) -> Sequence[str]: ...  # 返回错误列表（空=有效）

    class MethodRegistry:
        def __init__(self) -> None: ...
        def register(self, name: str, preconditions: Sequence[str],
                     subtasks: Sequence[TaskNode]) -> None: ...
        def get(self, name: str) -> tuple[Sequence[str], Sequence[TaskNode]]: ...
        def expand_compound(self, node: TaskNode) -> Sequence[TaskNode]: ...
    ```
  契约:
    require:
      - `deserialize_graph`: data 为有效 JSON 字符串
      - `expand_compound`: node.task_type == "compound" 且 node.method 已注册
    ensure:
      - `deserialize_graph(serialize_graph(g)) == g`（往返一致）
      - `validate_graph`: 无重复 task.id
验证矩阵:
  L0 类型: mypy 验证序列化函数签名、MethodRegistry 泛型
  L1 契约: 如上
  L2 属性:
    - `prop_graph_serialize_roundtrip`: `deserialize(serialize(g)) == g`
    - `prop_validate_catches_duplicate_ids`: 含重复 ID 的 graph → 非空错误列表
  L3 示例:
    - `test_serialize_simple_graph`: Given 2-task graph / When serialize / Then 有效 JSON
    - `test_expand_compound_task`: Given compound task / When expand / Then 返回子任务列表
    - `test_validate_catches_cycle`: Given 循环依赖 / When validate / Then 报错
Strategy 变更: 无（使用已有 st_task_graph）
异常设计: 无新增
依赖标注: 依赖 Step 1.3
Review 检查项:
- JSON schema 与设计文档示例兼容
- validate_graph 检查：重复 ID、悬空依赖、循环、缺失 method

### Step 5.2: LLM 驱动的目标分解

目标: 通过 LLM（planner 角色）将自然语言目标分解为 TaskGraph

新建/修改文件:
- `strata/planner/htn.py`（修改，添加 decompose 函数）
- `strata/planner/prompts.py`（新建，Prompt 模板常量）
- `tests/test_planner/test_htn.py`（修改）

先读文件清单:
- `strata/llm/router.py`（LLMRouter.plan）
- `strata/llm/provider.py`（ChatMessage, ChatResponse）
- `strata/planner/htn.py`（serialize/deserialize/validate）

API 规格:
  签名:
    ```python
    def decompose_goal(
        goal: str,
        router: LLMRouter,
        available_actions: Sequence[str],
        context: Mapping[str, object] | None = None,
    ) -> TaskGraph: ...
    ```
  契约:
    require:
      - goal 非空字符串
      - available_actions 非空
    ensure:
      - 返回的 TaskGraph 通过 validate_graph（无错误）
      - 返回的 TaskGraph.goal == goal
验证矩阵:
  L0 类型: mypy 验证签名
  L1 契约:
    - `@require(lambda goal: len(goal.strip()) > 0)`
    - `@ensure(lambda result: len(validate_graph(result)) == 0)`
  L2 属性: 无（LLM 输出不可预测，不适合 property test）
  L3 示例:
    - `test_decompose_with_mock_llm`: Given mock LLM 返回固定 JSON / When decompose / Then 解析为合法 TaskGraph
    - `test_decompose_invalid_llm_response`: Given mock LLM 返回非法 JSON / When decompose / Then PlannerError
Strategy 变更: 无
异常设计: 无新增
依赖标注: 依赖 Step 5.1 + Step 2.2
Review 检查项:
- LLM prompt 包含 available_actions 清单和 JSON schema 约束
- LLM 返回值解析失败时有结构化重试（最多 2 次）
- json_mode=True 减少格式错误
- **Prompt 管理**：所有 prompt 模板定义为 `strata/planner/prompts.py` 中的多行字符串常量（`DECOMPOSE_SYSTEM_PROMPT`、`DECOMPOSE_USER_TEMPLATE` 等），使用 `str.format()` 或 f-string 插值，不引入 Jinja2 依赖（Flat Abstraction 原则）。便于调试和迭代

### Step 5.3: 局部微调

目标: 当任务执行失败时，生成替代子图替换/插入原任务

新建/修改文件:
- `strata/planner/adjuster.py`（新建）
- `tests/test_planner/test_adjuster.py`（新建）

先读文件清单:
- `strata/planner/htn.py`
- `strata/llm/router.py`
- `strata/core/types.py`（TaskGraph, TaskNode, ActionResult）

API 规格:
  签名:
    ```python
    @dataclass(frozen=True)
    class Adjustment:
        original_task_id: str
        replacement_tasks: Sequence[TaskNode]
        strategy: Literal["replace", "insert_before", "insert_after"]

    def adjust_plan(
        graph: TaskGraph,
        failed_task_id: str,
        failure_context: Mapping[str, object],
        router: LLMRouter,
    ) -> Adjustment: ...

    def apply_adjustment(graph: TaskGraph, adjustment: Adjustment) -> TaskGraph: ...
    ```
  契约:
    require:
      - `adjust_plan`: failed_task_id 存在于 graph.tasks 中
      - `apply_adjustment`: adjustment.original_task_id 存在于 graph.tasks 中
    ensure:
      - `adjust_plan`: replacement_tasks 数量 ∈ [1, 3]
      - `apply_adjustment`: 返回的 TaskGraph 通过 validate_graph
验证矩阵:
  L0 类型: mypy 验证 Adjustment 类型、Literal strategy
  L1 契约: 如上
  L2 属性:
    - `prop_apply_adjustment_preserves_other_tasks`: 调整后，非目标任务保持不变
  L3 示例:
    - `test_adjust_replaces_task`: Given mock LLM / When adjust(replace) / Then 原任务被替换
    - `test_adjust_inserts_before`: Given mock LLM / When adjust(insert_before) / Then 新任务在原任务前
    - `test_apply_validates_result`: Given 无效 adjustment / When apply / Then PlannerError
Strategy 变更: 无
异常设计: 无新增
依赖标注: 依赖 Step 5.1 + Step 5.2
Review 检查项:
- LLM prompt 包含失败上下文和当前任务图
- 替代子图 ID 不与现有 ID 冲突
- replacement_tasks 上限为 3（防止 LLM 过度扩展）
- **failure_context 丰富度**：调用方（Harness 调度器）须填充足够上下文到 `failure_context` 中，包括：错误类型和消息、最近的 WorkingMemory 变量快照、若为 GUI 任务则附带最近截图描述（VLM 摘要）、若为终端任务则附带 CommandResult.stderr

---

## Phase 6：行动接地（L3 Grounding）+ OSWorld 适配器

### Step 6.1: VisionLocator（纯 VLM 定位 + 滚动搜索）

目标: 实现纯 VLM UI 元素定位——截图 → VLM → 坐标/动作，含滚动搜索循环

新建/修改文件:
- `strata/grounding/__init__.py`（新建）
- `strata/grounding/vision_locator.py`（新建）
- `tests/test_grounding/__init__.py`（新建）
- `tests/test_grounding/test_vision_locator.py`（新建）

先读文件清单:
- `strata/env/protocols.py`（IGUIAdapter）
- `strata/llm/router.py`（LLMRouter.see）
- `strata/core/config.py`（GUIConfig）
- `strata/core/types.py`（VisionResponse, VisionActionType, Coordinate）
- `strata/core/errors.py`（VisionLocatorError, ElementNotFoundError, SensitiveContentError）
- `strata/grounding/filter.py`（contains_sensitive）

API 规格:
  签名:
    ```python
    class VisionLocator:
        def __init__(self, gui: IGUIAdapter, router: LLMRouter,
                     config: GUIConfig) -> None: ...

        def locate(self, description: str, role: str | None = None) -> Coordinate:
            """单次定位：截图 → VLM → 坐标。不做滚动搜索。
            VLM 失败 → VisionLocatorError。"""
            ...

        def locate_with_scroll(self, description: str, role: str | None = None,
                               timeout: float = 30.0) -> Coordinate:
            """带滚动搜索的定位。循环：截图 → VLM → 若 not_found 则滚动/翻页 → 重试。
            达到 max_scroll_attempts 或 timeout → ElementNotFoundError。
            仅在 config.enable_scroll_search=True 时可用，否则退化为单次 locate。"""
            ...

        def _call_vlm(self, screenshot: bytes, description: str,
                      role: str | None) -> VisionResponse:
            """核心 VLM 调用：构造 ChatMessage，调用 router.see，解析结构化响应。
            解析失败 → VisionLocatorError。"""
            ...

        def _execute_scroll_action(self, response: VisionResponse) -> None:
            """根据 VisionResponse 执行滚动或翻页点击。"""
            ...
    ```
  契约:
    require:
      - `locate` / `locate_with_scroll`: description 非空
      - `locate` / `locate_with_scroll`: description 不含敏感信息（调用 filter.contains_sensitive 前置检查，违反 → SensitiveContentError）
    ensure:
      - `locate`: 返回的 Coordinate 在屏幕范围内
      - `locate_with_scroll`: 返回的 Coordinate 在屏幕范围内
      - `_call_vlm`: 返回 VisionResponse（action_type 为合法枚举值）

  VLM 响应结构（router.see 返回的 JSON 解析为 VisionResponse）:
    ```json
    {"action_type": "click", "x": 150, "y": 300, "confidence": 0.95}
    {"action_type": "scroll", "direction": "down"}
    {"action_type": "next_page", "x": 800, "y": 550}
    {"action_type": "not_found"}
    ```

  滚动搜索算法:
    1. 截图 → VLM → VisionResponse
    2. 若 action_type == "click" → 返回 Coordinate
    3. 若 action_type == "scroll" → 调用 gui.scroll(0, config.scroll_step_pixels * direction_sign)，scroll_count++
    4. 若 action_type == "next_page" → 调用 gui.click(response.coordinate)，翻页后等待加载
    5. 若 action_type == "not_found" → 尝试一次滚动（如有剩余次数），否则 ElementNotFoundError
    6. 若 scroll_count >= config.max_scroll_attempts → ElementNotFoundError
    7. 重复 1

  翻页坐标缓存:
    - 首次 VLM 返回 next_page 坐标后缓存
    - 后续翻页直接使用缓存坐标，减少 VLM 调用
    - 若点击后 VLM 仍报告同一页面内容（无变化），清除缓存并重新全图识别

验证矩阵:
  L0 类型: mypy 验证 Protocol 依赖、VisionResponse 类型
  L1 契约:
    - `@require(lambda description: len(description.strip()) > 0)`
    - `@require(lambda self, description: not contains_sensitive(description), "SensitiveContentError")`
    - `@ensure(lambda self, result: 0 <= result.x <= self._screen_w and 0 <= result.y <= self._screen_h)`
  L2 属性: 无（涉及外部 VLM API）
  L3 示例:
    - `test_locate_success`: Given mock VLM 返回 click(150, 300) / When locate / Then Coordinate(150, 300)
    - `test_locate_vlm_error_raises`: Given mock VLM 网络错误 / When locate / Then VisionLocatorError
    - `test_locate_sensitive_description_rejected`: Given "type my password" / When locate / Then SensitiveContentError
    - `test_locate_with_scroll_finds_after_scroll`: Given mock VLM 先返回 scroll(down) 再返回 click / When locate_with_scroll / Then 返回坐标
    - `test_locate_with_scroll_max_attempts_exceeded`: Given mock VLM 始终返回 scroll / When locate_with_scroll / Then ElementNotFoundError
    - `test_locate_with_scroll_next_page`: Given mock VLM 返回 next_page / When locate_with_scroll / Then 点击翻页后重新定位
    - `test_locate_with_scroll_disabled`: Given config.enable_scroll_search=False / When locate_with_scroll / Then 退化为单次 locate
    - `test_page_coordinate_cache_invalidation`: Given 翻页后内容未变化 / When 二次翻页 / Then 清除缓存重新识别
Strategy 变更: 无
异常设计: `VisionLocatorError`, `ElementNotFoundError`, `SensitiveContentError` 已在 Step 1.2 定义
依赖标注: 依赖 Step 3.1（IGUIAdapter）+ Step 2.2（Router）+ Step 6.3（filter）
Review 检查项:
- **单一职责**：VisionLocator 只做"截图→VLM→坐标/动作"，不持有 A11y 引用
- **敏感信息前置过滤**：locate/locate_with_scroll 入口处调用 filter.contains_sensitive(description)，拒绝含敏感词的请求
- **VLM 错误零容忍**：网络/认证/配额/格式非法 → 立即抛出 VisionLocatorError，不做重试或降级
- **滚动操作限定当前窗口**：gui.scroll 发送到当前活动窗口/鼠标位置，避免全局滚轮事件
- **滚动增量由配置控制**：使用 config.scroll_step_pixels，不依赖 VLM 估算像素值
- **审计日志记录每次滚动/翻页**：隐式动作完整写入 audit log
- **VLM prompt 包含加载状态识别**：prompt 中指示 VLM 识别"加载中"状态并返回 not_found（建议等待），而非盲目继续滚动
- **翻页缓存失效检测**：点击后 VLM 判断页面无变化 → 清除缓存

### Step 6.2: 终端命令处理器

目标: 封装 ITerminalAdapter，添加 prompt token 退出码捕获、静默超时、sudo 处理

新建/修改文件:
- `strata/grounding/terminal_handler.py`（新建）
- `tests/test_grounding/test_terminal_handler.py`（新建）

先读文件清单:
- `strata/env/protocols.py`（ITerminalAdapter）
- `strata/core/types.py`（CommandResult）
- `strata/core/config.py`（TerminalConfig）

API 规格:
  签名:
    ```python
    class TerminalHandler:
        def __init__(self, terminal: ITerminalAdapter, config: TerminalConfig) -> None: ...
        def execute_command(self, command: str, cwd: str | None = None) -> CommandResult: ...
        def _wrap_command(self, command: str) -> str: ...  # 添加 prompt token
        def _sanitize_sudo(self, command: str) -> str: ...  # 添加 -n 标志
    ```
  契约:
    require:
      - `execute_command`: command 非空
    ensure:
      - `execute_command`: returncode 为 int
      - `_sanitize_sudo`: 输出中 sudo 后必有 -n
验证矩阵:
  L0 类型: mypy
  L1 契约: 如上
  L2 属性: 无
  L3 示例:
    - `test_wrap_command_adds_token`: Given command / When wrap / Then 含 AGENT_DONE token
    - `test_sanitize_sudo_adds_n`: Given "sudo apt update" / When sanitize / Then "sudo -n apt update"
    - `test_execute_captures_exit_code`: Given mock terminal / When execute / Then returncode 正确
Strategy 变更: 无
异常设计: 无新增
依赖标注: 依赖 Step 3.2（terminal）
Review 检查项:
- prompt token UUID 每次唯一
- _wrap_command 不引入 shell injection
- 审计日志记录每条命令

### Step 6.3: 坐标 DPI 缩放 + 敏感过滤 + 坐标边界验证

目标: 实现坐标转换、敏感信息检测（VLM 前置）、坐标边界验证（三个小模块合并）

新建/修改文件:
- `strata/grounding/scaler.py`（新建）
- `strata/grounding/filter.py`（新建）
- `strata/grounding/validator.py`（新建）
- `tests/test_grounding/test_scaler.py`（新建）
- `tests/test_grounding/test_filter.py`（新建）
- `tests/test_grounding/test_validator.py`（新建）
- `tests/strategies.py`（修改，添加 st_coordinate）

先读文件清单:
- `strata/env/protocols.py`（IGUIAdapter.get_dpi_scale_for_point, get_screen_size）
- `strata/core/types.py`（Coordinate）
- `strata/core/config.py`（dangerous_patterns）
- `strata/core/errors.py`（InvalidCoordinateError, SensitiveContentError）

API 规格:
  签名:
    ```python
    # scaler.py
    class CoordinateScaler:
        def __init__(self, gui: IGUIAdapter) -> None: ...
        def logical_to_physical(self, coord: Coordinate) -> Coordinate: ...
        def physical_to_logical(self, coord: Coordinate) -> Coordinate: ...

    # filter.py
    SENSITIVE_PATTERNS: Final[Sequence[str]]  # password, token, secret, ...
    def contains_sensitive(text: str, extra_patterns: Sequence[str] = ()) -> bool: ...
    def redact(text: str, extra_patterns: Sequence[str] = ()) -> str: ...

    # validator.py
    class ActionValidator:
        def __init__(self, gui: IGUIAdapter) -> None: ...
        def validate_coordinates_in_screen(self, coord: Coordinate) -> None: ...
            # 超出屏幕范围 → InvalidCoordinateError
    ```
  契约:
    require:
      - `logical_to_physical` / `physical_to_logical`: DPI scale > 0
      - `contains_sensitive`: text 非 None
    ensure:
      - 坐标转换往返一致（浮点误差内）
      - `contains_sensitive` 对已知模式返回 True
      - `validate_coordinates_in_screen`: 通过时坐标在 [0, screen_w) x [0, screen_h)
验证矩阵:
  L0 类型: mypy
  L1 契约: 如上
  L2 属性:
    - `prop_dpi_roundtrip`: `physical_to_logical(logical_to_physical(c, s), s) ≈ c`
    - `prop_redact_replaces_sensitive`: 含密码字段的文本 → redact 后不含原始值
  L3 示例:
    - `test_dpi_1x_identity`: Given scale=1.0 / When convert / Then 坐标不变
    - `test_dpi_2x_doubles`: Given scale=2.0 / When logical_to_physical / Then x*2, y*2
    - `test_sensitive_password_detected`: Given "my password is 123" / When contains_sensitive / Then True
    - `test_sensitive_as_vlm_pre_check`: Given 含敏感词描述 / When contains_sensitive / Then True（阻止发送截图给 VLM）
    - `test_validate_in_screen`: Given (100, 200) screen 1920x1080 / When validate / Then 通过
    - `test_validate_out_of_screen`: Given (2000, 200) screen 1920x1080 / When validate / Then InvalidCoordinateError
Strategy 变更:
  - 新增 `st_coordinate(screen_bounds)`: 生成屏幕坐标
异常设计: `InvalidCoordinateError`, `SensitiveContentError` 已在 Step 1.2 定义
依赖标注: 依赖 Step 3.1（IGUIAdapter）
Review 检查项:
- DPI scale 从 IGUIAdapter.get_dpi_scale_for_point 获取
- 敏感模式列表可通过 config 扩展
- **无 A11y 依赖**：ActionValidator 仅依赖 IGUIAdapter.get_screen_size()，不做角色校验
- **filter.py 作为 VLM 前置守卫**：contains_sensitive 在 VisionLocator.locate 入口处调用，阻止含敏感描述的截图发送给云端 VLM
- 坐标超出范围 → InvalidCoordinateError（非 bool 返回值，Fail Fast）

### Step 6.4: OSWorld GUI 适配器

目标: 实现 `IGUIAdapter` 的 OSWorld 后端，映射到 OSWorld `computer_13` action space

新建/修改文件:
- `strata/env/gui_osworld.py`（新建，替换 stub）
- `tests/test_env/test_gui_osworld.py`（新建）

先读文件清单:
- `strata/env/protocols.py`（IGUIAdapter）
- OSWorld `desktop_env` 源码（了解 DesktopEnv API）

API 规格:
  签名:
    ```python
    class OSWorldGUIAdapter:
        def __init__(self, env: "DesktopEnv") -> None: ...
        # 实现 IGUIAdapter 全部方法
        # click → env.execute_action("click", ...)
        # type_text → env.execute_action("type", ...)
        # capture_screen → env.execute_action("screenshot", ...)
    ```
  契约:
    require:
      - `__init__`: Docker VM 已启动且可连接
    ensure:
      - `__init__`: 启动后调用 `get_screen_size()`，与 `config.osworld.screen_size` 比对，不匹配 → `ConfigError("screen size mismatch: expected {expected}, got {actual}")`
      - `capture_screen`: 返回非空 bytes（PNG）
验证矩阵:
  L0 类型: mypy 验证实现 IGUIAdapter Protocol
  L1 契约: 如上
  L2 属性: 无（依赖外部 Docker）
  L3 示例:
    - `test_osworld_click_delegates`: Given mock DesktopEnv / When click(100, 200) / Then execute_action("click", ...) called
    - `test_osworld_screenshot_returns_bytes`: Given mock env / When capture_screen / Then bytes 非空
Strategy 变更: 无
异常设计: 新增 `OSWorldConnectionError(EnvironmentError)`
依赖标注: 依赖 Step 3.1
Review 检查项:
- `# CONVENTION: OSWorld adapter 直接调用 DesktopEnv API — 不通过 pyautogui 中转`
- DesktopEnv 的 action space 映射完整覆盖 IGUIAdapter 方法
- DPI scale 对 OSWorld VM 固定为 1.0（Docker VM 无多屏）
- **坐标系统确认**：实施前阅读 OSWorld `desktop_env` 源码，明确 `computer_13` action space 的坐标约定（绝对像素 vs. 归一化 0-1）；IGUIAdapter 使用绝对像素，若 OSWorld 使用归一化坐标则在 adapter 内转换（screen_size 从 config.osworld.screen_size 获取）
- **OSWorld 配置集成**：`OSWorldGUIAdapter.__init__` 接受 `OSWorldConfig` 而非裸 `DesktopEnv`；由 adapter 内部根据 config 初始化 `DesktopEnv(provider_name=config.osworld.provider, screen_size=config.osworld.screen_size, headless=config.osworld.headless, ...)`

---

## Phase 7：用户交互（L0）+ 端到端集成

### Step 7.1: CLI 界面

目标: 实现命令行交互循环——接收指令、展示规划、确认执行、进度展示、中断处理

新建/修改文件:
- `strata/interaction/__init__.py`（新建）
- `strata/interaction/cli.py`（新建）
- `strata/__main__.py`（新建，入口点 `--config` 参数）
- `tests/test_interaction/__init__.py`（新建）
- `tests/test_interaction/test_cli.py`（新建）

先读文件清单:
- `strata/core/config.py`
- `strata/harness/state_machine.py`
- `strata/harness/scheduler.py`
- `strata/planner/htn.py`

API 规格:
  签名:
    ```python
    class CLI:
        def __init__(self, config: StrataConfig) -> None: ...
        def run(self) -> None: ...                          # 主循环
        def display_plan(self, graph: TaskGraph) -> None: ...
        def confirm_plan(self) -> bool: ...
        def display_progress(self, task_id: str, state: TaskState) -> None: ...
        def handle_error(self, task_id: str, error: Exception) -> Literal["retry", "skip", "abort"]: ...
    ```
  契约:
    require: 无（交互层无前置约束）
    ensure:
      - `confirm_plan`: 返回 bool
验证矩阵:
  L0 类型: mypy 验证
  L1 契约: 最小（交互层重点在 UX）
  L2 属性: 无
  L3 示例:
    - `test_cli_parse_config_arg`: Given --config path / When parse / Then 加载对应 config
    - `test_display_plan_outputs`: Given TaskGraph / When display / Then 输出到 stdout
    - `test_handle_error_choices`: Given error / When mock input "retry" / Then 返回 "retry"
Strategy 变更: 无
异常设计: 无新增
依赖标注: 依赖 Phase 1-6 全部完成
Review 检查项:
- SIGINT handler：停止新调度，等待当前任务，保存 checkpoint
- `--config` 为唯一 CLI 参数
- 进度展示不阻塞执行

### Step 7.2: 上下文管理器 + 审计日志

目标: 实现滑动窗口上下文管理和 JSON Lines 审计日志

新建/修改文件:
- `strata/harness/context.py`（新建）
- `tests/test_harness/test_context.py`（新建）

先读文件清单:
- `strata/core/config.py`（MemoryConfig）
- `strata/core/types.py`
- `strata/harness/persistence.py`（原子写复用）

API 规格:
  签名:
    ```python
    @dataclass(frozen=True)
    class ContextFact:
        key: str
        value: str
        timestamp: float

    class ContextManager:
        def __init__(self, config: MemoryConfig) -> None: ...
        def add_fact(self, key: str, value: str) -> None: ...
        def get_facts(self) -> Sequence[ContextFact]: ...
        def get_window(self) -> Sequence[Mapping[str, object]]: ...
        def compress(self) -> None: ...  # 触发压缩，保存快照

    class AuditLogger:
        def __init__(self, log_path: str) -> None: ...
        def log(self, task_id: str, action: str,
                params: Mapping[str, object],
                result: str,
                user_confirmed: bool = False) -> None: ...
    ```
  契约:
    require:
      - `add_fact`: key 非空
    ensure:
      - `get_facts`: 数量 ≤ max_facts_in_slot
      - `get_window`: 数量 ≤ sliding_window_size
      - `log`: 追加一行有效 JSON 到日志文件
验证矩阵:
  L0 类型: mypy
  L1 契约: 如上
  L2 属性: 无
  L3 示例:
    - `test_context_window_size_limit`: Given 添加 10 条 / When window_size=5 / Then 只保留最近 5 条
    - `test_facts_slot_limit`: Given 添加 30 条 / When max=20 / Then 只保留最近 20 条
    - `test_audit_log_json_lines`: Given 3 次 log / When 读文件 / Then 3 行有效 JSON
Strategy 变更: 无
异常设计: 无新增
依赖标注: 依赖 Step 4.4（persistence），可与 Step 7.1 并行
Review 检查项:
- compress() 保存完整快照到 `~/.strata/context_snapshots/`，**复用 Phase 4 的 `atomic_write()` 函数**确保快照文件写入原子性
- 此 Step 扩展 Phase 4 Step 4.7 的 `WorkingMemory` 为完整的 `ContextManager`（增加滑动窗口 + 压缩触发逻辑），WorkingMemory 作为 ContextManager 的内部组件
- 审计日志使用原子追加（open with "a"）
- 敏感信息在审计日志中通过 `strata.grounding.filter.redact()` 过滤

### Step 7.3: 端到端集成测试

目标: 在 OSWorld Docker 环境中跑通完整的端到端任务

新建/修改文件:
- `tests/test_integration.py`（新建）

先读文件清单:
- 所有 `strata/` 模块的 `__init__.py`
- `strata/interaction/cli.py`
- `strata/env/gui_osworld.py`

API 规格: 无新增 API（集成测试）
验证矩阵:
  L0 类型: 全量 mypy --strict .
  L1 契约: 全量运行
  L2 属性: 全量运行
  L3 示例:
    - `test_e2e_terminal_command`: CLI → 规划 "运行 echo hello" → 调度 → 终端执行 → 退出码 0
    - `test_e2e_file_operation`: CLI → 规划 "在沙盒中创建 test.txt" → 调度 → 文件存在
    - `test_e2e_osworld_screenshot`: 连接 OSWorld Docker → 截图 → 返回 PNG bytes
    - `test_e2e_vision_locate`: OSWorld Docker → 截图 → VLM 定位桌面图标 → 返回坐标 → 验证在屏幕内（@pytest.mark.live_llm）
    - `test_e2e_vision_scroll_search`: OSWorld Docker → 打开长页面 → VisionLocator.locate_with_scroll → 找到目标 → 验证滚动次数合理（@pytest.mark.live_llm）
    - `test_e2e_recovery_pipeline`: 模拟第一次失败 → 重试成功
Strategy 变更: 无
异常设计: 无
依赖标注: 依赖全部 Phase 1-6 + Step 7.1 + 7.2
Review 检查项:
- OSWorld Docker 环境在 CI 中可用（或标记 `@pytest.mark.integration` 跳过）
- **Docker 容器生命周期管理**：使用 `conftest.py` 中的 session-scoped fixture 管理 OSWorld Docker 容器（setUp 启动 / tearDown 销毁），确保测试结束后容器正确清理，避免资源泄漏
- 测试隔离：每个测试使用独立沙盒目录
- 大部分集成测试使用 mock LLM
- **至少一个 `@pytest.mark.live_llm` 端到端测试为必需**：验证三家提供商 API 契约未漂移（如 `test_e2e_live_llm_decompose`：真实调用 planner → 解析返回 → 验证为合法 TaskGraph）。若 `~/.strata/config.toml` 缺失或 API 调用失败，测试**失败**（非 skip）——CI 环境中可通过 `pytest -m "not live_llm"` 排除
- **`@pytest.mark.live_llm` 视觉定位测试强制**：至少一个端到端 GUI 任务测试调用真实 VLM，验证视觉定位链路完整性（截图 → VLM → 坐标解析 → 屏幕范围验证）

---

## 验证矩阵汇总

| Module | 新增 L0 (类型) | 新增 L1 (契约) | 新增 L2 (属性) | 新增 L3 (示例) | 合计 |
|---|---|---|---|---|---|
| `strata/core/`（含 sandbox） | 16 | 9 | 6 | 8 | 39 |
| `strata/llm/` | 6 | 4 | 2 | 4 | 16 |
| `strata/env/`（无 A11y，含 linux/macos） | 16 | 8 | 3 | 8 | 35 |
| `strata/harness/` | 18 | 12 | 4 | 10 | 44 |
| `strata/planner/` | 8 | 6 | 3 | 5 | 22 |
| `strata/grounding/`（纯 VLM） | 12 | 8 | 2 | 10 | 32 |
| `strata/interaction/` | 6 | 4 | 1 | 4 | 15 |
| **总计** | **82** | **51** | **21** | **49** | **203** |

## 执行流水线

```
Phase 1: 1.1W → 1.1R → 1.2W → 1.2R → 1.3W → 1.3R → 1.4W → 1.4R → Gate → Commit "phase-1: core types, config, errors"

Phase 2: 2.1W → 2.1R → 2.2W → 2.2R → Gate → Commit "phase-2: llm provider abstraction"

Phase 3: 3.1W → 3.1R → 3.2W → 3.2R → [3.3W ∥ 3.4W ∥ 3.5W ∥ 3.6W] → [3.3R ∥ 3.4R ∥ 3.5R ∥ 3.6R] → Gate → Commit "phase-3: l4 env adapters + sandbox"

Phase 4: 4.1W → 4.1R → 4.2W → 4.2R → [4.3W ∥ 4.4W ∥ 4.6W] → [4.3R ∥ 4.4R ∥ 4.6R] → 4.5W → 4.5R → Gate → Commit "phase-4: l2 harness"

Phase 5: 5.1W → 5.1R → 5.2W → 5.2R → 5.3W → 5.3R → Gate → Commit "phase-5: l1 htn planner"

Phase 6: [6.1W ∥ 6.2W ∥ 6.3W] → [6.1R ∥ 6.2R ∥ 6.3R] → 6.4W → 6.4R → Gate → Commit "phase-6: l3 grounding + osworld"

Phase 7: [7.1W ∥ 7.2W] → [7.1R ∥ 7.2R] → 7.3W → 7.3R → Full Gate → Commit "phase-7: l0 cli + e2e integration"
```

### Phase 完成协议

每个 Phase 的 Gate 通过后：
1. `git add -A`
2. `git commit -m "phase-N: <描述>"`
3. 不自动 push（等待人类确认）

全部 Phase 完成后运行全量验证：
```bash
uv run mypy --strict . && uv run pytest --tb=short -q && uv run ruff check . && uv run ruff format --check .
```
