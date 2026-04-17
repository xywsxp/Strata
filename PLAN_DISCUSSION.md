# 讨论文档：OSWorld 解耦 + 本地 GUI 后端 + Live 端到端验证

---

## 1. 背景与目标

**现状问题**：Strata 的设计意图（`pan.md`）是一个通用桌面 Agent 框架，OSWorld 仅作评估环境。但当前实现中 agent **事实上绑死在 OSWorld Docker 容器上**——`osworld.enabled=false` 时整个 agent 因 `LinuxGUIAdapter.__init__` 抛 `UnsupportedPlatformError` 而无法启动。所有测试均为 mock，从未有一次 live 端到端执行。

**目标**：

1. **OSWorld 解耦**：agent 核心循环在无 OSWorld 容器时完整可用；OSWorld 退化为可选的评估 / 录屏后端。
2. **本地 GUI 后端实装**：`LinuxGUIAdapter` 用 pyautogui 直接操控宿主机 X11/Wayland 桌面（与 OSWorldGUIAdapter 内部发给容器的 pyautogui 代码完全对称）。
3. **A11y 层正式放弃**：`pan.md` 设计的 `IA11yAdapter` 不实现，感知层永久纯 VLM——减少依赖面、降低平台耦合。
4. **Live 验证补全**：
   - LLM API 连接测试（真实 provider roundtrip）
   - OSWorld 连接测试（`integration` marker，容器可用时自动跑）
   - 本地 GUI e2e（`live_gui` marker，有 X display 时自动跑）
   - `scripts/run_tasks.py` 补全 `target="osworld"` 的 setup/verify 路径
   - 启动时 health check（LLM + OSWorld），fail-fast 而非延迟到首次操作
5. **至少跑通 2 道 tasks/*.toml 题目并产出 report.json**，证明框架端到端可执行。

---

## 2. 现状拓扑

### Package / Module 地图

```
strata/
├── core/           L∞  类型 + 配置 + 异常 + 沙盒
├── env/            L4  环境适配器
│   ├── protocols.py       5 个 Protocol (IGUIAdapter …)
│   ├── gui_osworld.py     ✅ 唯一可用 GUI 实现
│   ├── linux/gui.py       ❌ 纯 stub，__init__ 抛异常
│   ├── pty_terminal.py    ✅ 宿主机 PTY（OSWorld 无关）
│   ├── filesystem.py      ✅ 沙盒文件系统（OSWorld 无关）
│   └── factory.py         分发器，osworld off 时走 stub 死路
├── grounding/      L3  纯 VLM（无 A11y）
├── harness/        L2  状态机 + 调度 + 恢复
│   └── orchestrator.py    import OSWorldFFmpegRecorder
├── observability/  跨层  录制 + 转录
│   └── recorder.py        import _OSWorldHTTPClient ← 层级违规
├── llm/            跨层  Provider Protocol + Router
├── planner/        L1  HTN 规划
├── interaction/    L0  CLI
└── paths.py        运行期产物目录布局
```

### 架构痛点

| # | 痛点 | 位置 |
|---|------|------|
| P1 | `LinuxGUIAdapter` 是 stub → `osworld.enabled=false` 不可用 | `env/linux/gui.py` |
| P2 | `recorder.py` import `_OSWorldHTTPClient` → 观测层反向依赖 env 具体实现 | `observability/recorder.py:27` |
| P3 | `orchestrator._build_recorder` 直接 import `OSWorldFFmpegRecorder` | `harness/orchestrator.py:59-62` |
| P4 | `orchestrator._plan` 用 `config.osworld.os_type` 做 plan context → 核心循环对 OSWorld 配置有语义依赖 | `harness/orchestrator.py:323` |
| P5 | `EnvironmentFactory` 非 OSWorld 路径直接走入 stub 死路 | `env/factory.py:46-48` |
| P6 | 启动无 health check → 连接错误延迟到首次 GUI 操作才暴露 | `__main__.py` / `factory.py` |
| P7 | `run_tasks.py` 忽略 `task.setup.target` / `task.verify.target`，只跑 host shell | `scripts/run_tasks.py:134,178` |
| P8 | 所有测试为 mock，从未 live 执行 | 全 `tests/` |
| P9 | `IA11yAdapter` 在 `pan.md` 设计中存在但未实现，残留设计债 | 架构层面 |

---

## 3. 演进方案

### 3.1 本地 GUI 后端（LinuxGUIAdapter 实装）

**核心洞察**：`OSWorldGUIAdapter` 的工作方式是把 `import pyautogui; pyautogui.click(x, y)` 代码片段通过 HTTP 发给容器内 Flask 执行。`LinuxGUIAdapter` 只需把同样的 pyautogui 调用**在宿主机直接执行**——两者的业务语义完全对称。

**实现路径**：
- 新增 `pyautogui` 为依赖（`uv add pyautogui`）
- `LinuxGUIAdapter.__init__` 验证 `DISPLAY` 环境变量存在（X11）或 `WAYLAND_DISPLAY`（Wayland + xdotool fallback）
- 每个方法直接调 `pyautogui.*`
- `capture_screen` 返回 PNG bytes（`pyautogui.screenshot()` → `io.BytesIO` → bytes）
- `get_dpi_scale_for_point` → 调 `xrandr` 或默认 `1.0`
- 构造时 fail-fast：无 display → `UnsupportedPlatformError`（仍然 fail-fast，但原因是"无 display"而非"未实现"）

**对称性保证**：两个 adapter 实现相同 `IGUIAdapter` Protocol，行为等价（差异仅在传输层：本地 vs HTTP）。

### 3.2 OSWorld 解耦

**策略**：把 OSWorld 相关代码收敛到 `strata/env/` 子包内，其他层不得 import `gui_osworld` 或 `OSWorldConfig` 的内部细节。

- **提取 HTTP 客户端**：`_OSWorldHTTPClient` 从 `gui_osworld.py` 提取到 `strata/env/osworld_client.py`。`gui_osworld.py` 和 `recorder.py` 都从新位置 import。
- **Recorder 注入**：`OSWorldFFmpegRecorder.__init__` 改为接收一个轻量 `RemoteCodeRunner` Protocol（只有 `exec_remote(code: str) -> None` 和 `download_file(remote_path: str) -> bytes`），不再自行构造 HTTP client。这样 recorder 不知道 OSWorld 的存在。
- **Orchestrator os_type 解耦**：`_plan()` 里的 `config.osworld.os_type` 改为从 `platform.system()` / `platform.release()` 获取（本机），或由配置显式覆盖（`config.os_type_override`）。OSWorld 场景由 `config.toml` 里 `os_type_override = "Ubuntu"` 覆盖。
- **EnvironmentFactory 双路**：`osworld.enabled=true` → `OSWorldGUIAdapter`；`osworld.enabled=false` → `LinuxGUIAdapter`（真实实现，不再是 stub）。

### 3.3 A11y 层正式放弃

- `pan.md` 中的 `IA11yAdapter`、`ElementInfo`、`启发式缓存` 设计全部标记为 **ABANDONED**。
- 不在 `env/protocols.py` 添加任何 A11y Protocol。
- `VisionLocator` 是唯一的感知路径，已经纯 VLM。
- 记录 CONVENTION 注释。

### 3.4 启动 Health Check

在 `__main__.py` 和 `scripts/agent_e2e.py` / `run_tasks.py` 的启动流程中加入：
1. **LLM Health Check**：对每个已配置的 provider 发一个 minimal `chat([ChatMessage(role="user", content="ping")])` 调用。失败 → 打印 provider 名称 + 错误 + 退出。
2. **OSWorld Health Check**（仅 `osworld.enabled=true`）：`POST /screen_size` 一次。失败 → 打印连接错误 + 退出。
3. **GUI Health Check**（仅 `osworld.enabled=false`）：`pyautogui.screenshot()` 一次验证 X11 连接。

### 3.5 Live 测试 + 端到端

- **LLM Live Test**（`@pytest.mark.live_llm`）：扩展现有 `test_live_llm.py`，覆盖所有 4 个 role 的 roundtrip + vision 带图片。
- **OSWorld Live Test**（`@pytest.mark.integration`）：扩展现有 `test_osworld_pipeline.py`，加连接 health check 测试。
- **Host GUI Live Test**（新 `@pytest.mark.live_gui`）：验证 `LinuxGUIAdapter` 在有 X display 时能 screenshot + click。
- **`run_tasks.py` 补全**：`target="osworld"` 的 setup/verify 通过 `_OSWorldHTTPClient.post_json("/run_python", {"code": cmd})` 在容器内执行。
- **端到端验收**：实际执行 `tasks/create-hello-txt.toml` + `tasks/read-hostname.toml`，验证 `report.json` 中 verdict 字段。

### 3.6 状态机变更

**无**。`GlobalState` / `TaskState` 状态机不变。本次变更全部在 env 层 + 观测层 + 启动流程，不触碰核心调度逻辑。

---

## 4. 验证策略

### 类型捕获（L0）
- `IGUIAdapter` Protocol 结构化检查：`LinuxGUIAdapter` 和 `OSWorldGUIAdapter` 都必须通过 `isinstance(..., IGUIAdapter)` 运行时断言。
- `RemoteCodeRunner` Protocol：recorder 依赖注入的类型正确性。
- `HealthCheckResult` 数据类的字段类型。

### 契约表达（L1）
- `LinuxGUIAdapter.__init__`：`require(DISPLAY 或 WAYLAND_DISPLAY 存在)`
- `LinuxGUIAdapter.click/type_text/...`：坐标在屏幕范围内
- `check_llm_health`：`require(providers 非空)`，`ensure(返回每个 provider 的状态)`
- `check_osworld_health`：`require(config.osworld.enabled)`

### Hypothesis 属性（L2）
- `LinuxGUIAdapter` vs `OSWorldGUIAdapter` 的 `get_screen_size` 返回值结构一致性（类型 + 范围约束）——但这更适合 L3 因为需要 live 环境。
- `gc_old_runs` 幂等性（已有）。
- `TaskFile.load` round-trip（已有）。

### pytest 示例（L3）
- **Live LLM**：4 role roundtrip + vision with image
- **Live OSWorld**：screenshot roundtrip + click + run_python
- **Live Host GUI**：screenshot + screen_size
- **E2E pipeline**：`run_tasks.py tasks/create-hello-txt.toml` → verdict=PASS
- **Health check**：mock 失败场景 → 正确错误消息；live 成功场景 → 正常返回
- **Recorder 解耦**：mock `RemoteCodeRunner` → recorder 正常工作
- **Factory 双路**：`osworld.enabled=true` → OSWorldGUIAdapter；`false` → LinuxGUIAdapter

---

## 5. 爆炸半径

### 涉及 Package 和文件

| 类别 | 文件 | 变更类型 |
|------|------|----------|
| 新建 | `strata/env/osworld_client.py` | 提取 `_OSWorldHTTPClient` |
| 重写 | `strata/env/linux/gui.py` | stub → 真实 pyautogui 实现 |
| 修改 | `strata/env/gui_osworld.py` | import 路径改为 `osworld_client` |
| 修改 | `strata/env/factory.py` | 双路分发变为真实双路 |
| 修改 | `strata/observability/recorder.py` | 注入 `RemoteCodeRunner`，不再 import `_OSWorldHTTPClient` |
| 修改 | `strata/harness/orchestrator.py` | os_type 解耦；recorder 构造解耦 |
| 修改 | `strata/core/config.py` | 新增 `os_type_override` 字段 |
| 新建 | `strata/health.py` | LLM / OSWorld / GUI health check |
| 修改 | `strata/__main__.py` | 启动时调 health check |
| 修改 | `scripts/run_tasks.py` | 补全 `target="osworld"` |
| 修改 | `scripts/agent_e2e.py` | 加 health check |
| 新建 | `tests/e2e/test_host_gui.py` | 本地 GUI live 测试 |
| 修改 | `tests/e2e/test_live_llm.py` | 扩展覆盖 |
| 修改 | `tests/e2e/test_osworld_pipeline.py` | 加 health check 测试 |
| 新建 | `tests/test_env/test_linux_gui.py` | LinuxGUIAdapter 单元测试 |
| 修改 | `tests/test_env/test_gui_osworld.py` | import 路径更新 |
| 修改 | `tests/test_observability/test_recorder.py` | 用 mock RemoteCodeRunner |
| 修改 | `tests/test_harness/test_orchestrator.py` | os_type 解耦后的回归 |
| 修改 | `pyproject.toml` | 加 `pyautogui` 依赖 + `live_gui` marker |

### `tests/strategies.py`

不需要扩充。新增组件主要是 I/O 层和集成层，不涉及通用命题属性测试。

---

## 6. 约束与防线

### CONVENTION 记录

- **CONVENTION: `strata.env.linux.gui` — 纯 pyautogui 实现，不走 A11y API。`IA11yAdapter` 设计正式 ABANDONED。**
- **CONVENTION: `strata.observability.recorder` — 依赖 `RemoteCodeRunner` Protocol 而非具体 HTTP client；OSWorld 特化逻辑收在 `strata/env/` 内。**
- **CONVENTION: `strata.harness.orchestrator` — os_type 从 `platform.system()` 获取，`config.os_type_override` 可覆盖（OSWorld 场景设 `"Ubuntu"`）。**

### 并发假设

- `LinuxGUIAdapter` 与 `OSWorldGUIAdapter` 一样，假设单线程调用（`GUILock` 保证互斥）。
- `pyautogui` 本身非线程安全，但在 GUILock 保护下安全。

### 性能天花板

- `LinuxGUIAdapter.capture_screen` → pyautogui.screenshot() → PIL → PNG bytes：约 50-150ms（1080p）。与 OSWorld HTTP roundtrip（~100-200ms）量级相当。
- Health check 在启动时执行，一次性开销，不影响运行时性能。
- LLM health check 每个 provider 一次 minimal chat，总开销 < 5s。

---

## 7. Phase 划分

| Phase | 阶段目标 | 核心交付 |
|-------|----------|----------|
| **P1: OSWorld 解耦 + 本地 GUI** | HTTP client 提取；LinuxGUIAdapter 实装；recorder 注入解耦；factory 双路；os_type 解耦 | `osworld.enabled=false` 时 agent 可启动，截图 / 点击可用 |
| **P2: Health Check + 连接验证** | LLM / OSWorld / GUI 启动 health check；集成到所有入口点 | 启动时 fail-fast，明确告知哪个组件连接失败 |
| **P3: Live 测试 + run_tasks 补全** | live_llm / integration / live_gui 三类 marker 测试；`run_tasks.py` 补全 osworld target | 真实 API 验证通过；题目执行器完整 |
| **P4: 端到端验收** | 至少 2 道 tasks 跑通；report.json 产出；轨迹产物验证 | 证明框架端到端可执行，不再是"全 mock 通过" |

---

## 8. 待决议断言

| # | 问题 | 默认选择 | 需要确认 |
|---|------|----------|----------|
| **Q1** | `pyautogui` 作为宿主机 GUI 依赖，是 `dependencies` 还是 `optional-dependencies`？ | `dependencies`（agent 核心能力） | 如果担心 headless 服务器安装 pyautogui 报错，可选 `optional` + 延迟导入 |
| **Q2** | `os_type_override` 配置字段名？ | `os_type` 放在 `[agent]` 或顶层？ | 或者直接在 `[osworld]` 里保留 `os_type`，非 OSWorld 场景从 `platform.system()` 推断 |
| **Q3** | LLM health check 失败时行为：退出？还是 warning 继续？ | 退出（fail-fast，避免浪费时间在注定失败的规划上） | 如果某些 role（如 search）不是必需的，可以 warning 降级 |
| **Q4** | `LinuxGUIAdapter` 是否也需要支持 Wayland？ | 初版仅 X11（pyautogui 依赖 Xlib），Wayland 做 Phase 12+ | 如果你的桌面是 Wayland 需要提前处理 |
| **Q5** | Live 测试中 OSWorld Docker 容器的启动由谁负责？测试自启？还是预设环境？ | 预设环境（`STRATA_OSWORLD_URL` 环境变量），测试不自启容器 | 如果想测试自启，需要额外的 docker compose 集成 |
| **Q6** | `run_tasks.py` 的 `target="osworld"` setup/verify 通过什么执行？直接 HTTP 到容器 `/run_python`？ | 是，复用 `OSWorldHTTPClient.post_json("/run_python", {"code": cmd})` | 或者通过 `EnvironmentBundle.terminal.run_command` 统一路径 |
| **Q7** | 端到端验收的最小通过标准？至少哪几道题 PASS？ | `create-hello-txt` + `read-hostname`（最简单的文件操作 + 命令执行） | 如果想要更高标准可加 `list-tmp-count` |

---

> 以上为架构草案。如有修正请指出；若无异议，回复「确认」，我将生成 `FV_EXECUTION_PLAN.md`。
