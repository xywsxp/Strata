
# Agent 框架设计方案（最终完整版）



> 设计目标：一个自用的、能够处理日常计算机任务的智能 Agent 框架，具备 HTN 任务规划、动态适应、GUI 互斥（支持抢占）、安全沙盒、高 DPI 适配（主屏幕）、精准终端控制等能力。初版即可实现完整核心功能。



---



## 一、整体分层架构



| 层级 | 名称 | 核心职责 |

|------|------|----------|

| Layer 0 | 用户交互层 | 接收指令，展示计划，确认执行，处理干预和通知 |

| Layer 1 | 任务规划层 | 基于 HTN 将用户目标分解为动态任务图（静态规划 + 局部微调） |

| Layer 2 | 执行编排层 (Harness) | 状态机、调度、GUI 互斥锁（可抢占）、错误恢复、上下文管理、持久化、安全沙盒 |

| Layer 3 | 行动接地层 | 抽象动作转具体坐标/元素，A11y/VLM 定位，坐标 DPI 转换，敏感信息过滤 |

| Layer 4 | 环境交互层 | 封装 OS/GUI/文件系统/PTY/系统 A11y API，提供统一抽象接口 |



---



## 二、Layer 0：用户交互层



### 职责

- 接收自然语言指令

- 展示规划结果（任务图）并请求用户确认

- 执行中展示进度（当前任务、已完成、错误）

- 处理用户中断（暂停、继续、取消）和错误时的决策（重试、跳过、终止）



### 设计决策

- **交互模式**：先确认方案 → 自主执行 → 异常时介入

- **实现形式**：命令行 + 自然语言（初期），预留 Web/TUI 接口

- **运行模式**：单次任务模式 + 守护模式（后台监听队列）

- **中断处理**：SIGINT 时停止调度新任务，等待当前任务完成，保存状态后退出

- **通知机制**：桌面弹窗（`notify-send`/`osascript`/`toast`），可选手机推送



---



## 三、Layer 1：任务规划层（HTN）



### 核心概念

- **原始任务（Primitive Task）**：可直接由 Layer 3 执行的动作（如 `click_button`、`type_text`、`run_command`）。

- **复合任务（Compound Task）**：需要进一步分解的任务（如 `download_file`、`organize_files`）。

- **方法（Method）**：定义如何分解复合任务，包含前置条件和子任务列表（支持顺序、并行、条件分支）。

- **规划策略**：**静态规划 + 局部微调**（非完全交错）。初始时规划器生成完整任务图；执行中如果遇到意外（如弹窗、元素缺失），Layer 2 可请求 Layer 1 进行局部修改（插入/替换少量任务），无需全盘重规划。



### 循环与条件实现

引入特殊原始任务类型，由 Layer 2 解释执行，**所有循环必须设置最大迭代上限（Max Iterations Guard）**：

- `Repeat(condition, subtask, max_iterations=100)`：重复执行 subtask 直到条件为假或达到上限。

- `If(condition, then_subtask, else_subtask)`：条件分支。

- `ForEach(iterable, subtask, max_iterations=100)`：遍历列表执行子任务。



### 任务图表示（示例 JSON）

```json

{

  "goal": "整理 Downloads 文件夹",

  "tasks": [

    {"id": "T1", "type": "primitive", "action": "list_files", "params": {"path": "~/Downloads", "pattern": "*.pdf"}, "output_var": "pdf_list"},

    {"id": "T2", "type": "primitive", "action": "condition", "params": {"var": "pdf_list", "operator": "is_empty"}, "on_true": "abort", "on_false": "continue"},

    {"id": "T3", "type": "compound", "method": "merge_pdfs", "params": {"files": "${pdf_list}", "output": "~/Desktop/merged.pdf"}}

  ],

  "methods": {

    "merge_pdfs": {

      "preconditions": ["tool(pdf_merger) available"],

      "subtasks": [{"type": "primitive", "action": "run_command", "params": {"command": "pdfmerge ${files} -o ${output}"}}]

    }

  }

}

```



### 局部微调

- 当 Layer 2 执行某个原子任务失败且恢复管道无法解决时，调用 Layer 1 的 `adjust(task_id, failure_context)`。

- 规划器基于当前状态（环境快照、记忆槽）生成一个替代子图（通常为 1-3 个任务），替换原任务或插入其前/后。



---



## 四、Layer 2：执行编排层（Harness）



### 职责

- 状态机管理（全局 + 任务级）

- 任务调度（支持顺序、并行、循环、条件，**循环带最大迭代上限**）

- GUI 全局互斥锁（**支持可抢占式轮询**）

- 错误恢复（多级管道）

- 上下文管理（滑动窗口 + 关键事实抽取）

- 持久化（断点续传）

- 安全控制（沙盒路径过滤、用户确认、审计日志）



### 全局状态机

```

INIT → SCHEDULING → EXECUTING → RECOVERING → WAITING_USER → COMPLETED/FAILED

```



### 调度器

- **任务队列**：就绪任务（依赖满足、状态为 `pending`）入队。

- **并行限制**：

  - GUI 任务必须持有 **GUI 互斥锁**，同时只能有一个 GUI 任务执行。

  - 纯后台任务（文件操作、无头命令）可并行，不争抢锁。

- **循环/条件执行**：调度器直接解释 `Repeat`、`If`、`ForEach` 节点，并强制检查迭代次数上限（超过则失败）。



### GUI 全局互斥锁（支持可抢占轮询）

- 一个线程锁，所有 `requires_gui=True` 的动作在执行前必须获取锁，执行后释放。

- 需要锁的操作：鼠标点击/移动/滚动、键盘输入、窗口激活、剪贴板写入。截图不加锁。

- **等待机制（可抢占）**：对于 `wait_for_element` 等操作，使用轮询循环：

  - 每次检查前获取锁，检查后立即释放锁。

  - 如果条件不满足，则 sleep（如 0.5 秒），期间锁被释放，其他任务（如错误恢复、处理弹窗）可以获取锁并执行。

  - 循环重新获取锁，继续检查。

  - 这样既保证了条件检查的一致性，又避免了长时间独占锁导致死锁。

- 超时：若锁被占用超过 `gui_lock_timeout`（如 10 秒），任务失败，进入恢复。



### 错误恢复管道

| 层级 | 策略 | 默认次数 | 说明 |

|------|------|----------|------|

| 1 | 本地重试 | 2-3 | 相同动作重试 |

| 2 | 替代动作 | 最多 2 种 | 如改用快捷键 |

| 3 | 局部微调（调用 Layer 1） | 1 | 生成替代子图 |

| 4 | 跳过任务 | 用户配置 | 非关键任务 |

| 5 | 用户介入 | 永久 | 弹出确认框 |



### 上下文管理（滑动窗口 + 关键事实抽取）

- **持久记忆槽（工作记忆）**：存储变量绑定、用户确认的决策、最近 N 条工具调用结果（**尽量保留具体值**，如文件路径，而非仅数量）。

- **滑动窗口**：保留最近 K 轮（例如 5 轮）的完整对话，其余压缩为上述关键事实。

- **压缩触发**：每完成一个高层里程碑或 token 使用量超过模型窗口 80%。

- **调试支持**：压缩前将完整上下文快照保存到磁盘（`~/.agent/context_snapshots/`），便于手动恢复或调试。



### 持久化（断点续传）

- 每个任务状态变更时写 JSON 文件到 `.tasks/` 目录。

- 检查点：初始规划完成、里程碑完成、局部微调后、用户中断时。

- 恢复：启动时检测是否有未完成任务，询问用户继续或重新开始。



### 安全控制



#### 沙盒路径过滤

- 配置文件 `~/.agent/config.yaml` 定义沙盒根目录 `root`（如 `~/agent_sandbox`）。

- 所有文件读写操作（`IFileSystemAdapter` 的方法）检查路径是否在沙盒内。

- 额外允许的只读路径可通过 `read_only_paths` 配置（如 `/etc/os-release`）。

- 如果路径不在允许范围内，抛出 `PermissionError`，触发 `WAITING_USER` 询问是否临时授权。



#### 删除操作转换

- 任何删除操作（`rm`、`delete_file`）自动转为 `move_to_trash`，目标目录为 `~/.agent_trash/`。

- 回收站保留原始路径元数据，提供恢复命令。



#### sudo 处理

- 检测命令中的 `sudo`，自动添加 `-n` 标志（非交互）。

- 如果执行失败且错误提示需要密码，则拒绝执行，提示用户配置 `NOPASSWD` 或使用密钥链。



#### 静态拦截（辅助）

- 保留对常见危险模式（`rm -rf`、`os.unlink`）的正则检测，用于提前警告，但不作为唯一防线。



#### 审计日志

- 所有操作（尤其是文件操作、命令执行、用户确认）写入 `~/.agent/audit.log`（JSON Lines 格式）。

- 包含：时间戳、任务 ID、操作类型、参数、结果、是否用户确认。



---



## 五、Layer 3：行动接地层



### 职责

- 将抽象动作（如 `click_button("提交")`）转换为具体可执行指令。

- 通过 A11y API 或 VLM 定位 UI 元素（**支持启发式缓存避免重复超时**）。

- 进行坐标 DPI 转换（逻辑 ↔ 物理），**仅支持主屏幕**。

- 过滤敏感信息（密码、token），避免发送给云端。

- 验证动作合法性（如元素是否存在、坐标是否在屏幕内）。



### 感知方案

- **主**：系统原生 A11y API（Linux AT-SPI / macOS AXAPI / Windows UIA）。

- **备**：云端 VLM（GPT-4o / Claude 3.5 Sonnet），仅用于 A11y 失败时的视觉定位。

- **回退策略**：A11y 查找超时或返回空 → 调用 VLM 定位。

- **启发式缓存（A11y 盲区标记）**：

  - 对于某个应用（窗口标题或进程名），如果连续两次 A11y 查询失败，则将该应用标记为“A11y 盲区”。

  - 后续针对该应用的动作直接绕过 A11y，首选 VLM 视觉定位，不再等待超时。

  - 盲区标记可定期清除（如每个新任务开始时重置），或由用户手动清除。



### 坐标 DPI 转换（CoordinateScaler）

- **仅支持主屏幕**。通过系统 API 获取主屏幕的 DPI 缩放因子（如 Windows `GetDpiForSystem`，macOS `NSScreen.main.backingScaleFactor`）。

- **转换规则**：

  - A11y API 返回的逻辑像素 → 转换为物理像素（用于 `click`、`move_mouse`）。

  - VLM 基于截图（物理像素）返回的坐标 → 转换为逻辑像素（用于 A11y 验证或存储）。

- **实现**：提供 `logical_to_physical(x, y)` 和 `physical_to_logical(x, y)` 方法。



### 敏感信息过滤

- 在动作参数（如 `type_text` 的文本）中检测敏感模式（`password`, `token`, `secret` 及用户自定义）。

- 若匹配，则不将文本发送给 VLM（VLM 仅用于定位）。

- 密码输入处理：

  - 如果用户已配置系统密钥链，则自动填充。

  - 否则触发 `WAITING_USER`，让用户手动输入。



### 终端命令处理

- 使用 **Prompt 注入 token** 精准判断命令结束：

  - 实际执行的命令包装为：`export PS1="[AGENT_DONE_$(uuid)] "; command; echo "[AGENT_DONE_$(uuid)]"`

  - 异步读取 stdout，匹配 token 后切割输出。

- **静默超时判定**（防止 REPL 挂起）：

  - 同时启动一个静默超时计时器（如 `silence_timeout=5.0` 秒）。

  - 如果在 token 匹配成功前，静默超时触发（且没有任何输出），则主动向 PTY 发送 `\x03` (Ctrl+C) 中断命令，然后等待命令退出。

  - 记录审计日志“命令可能进入了交互模式，已中断”。

- 保留超时兜底（如 300 秒）。

- 仅支持非交互模式。对 `sudo` 自动添加 `-n` 标志（见安全控制）。



### 动作验证

- 对 VLM 返回的坐标，通过 A11y 查询该位置元素，检查角色是否匹配预期（如点击按钮，实际是文本 → 不匹配）。

- 若验证失败，触发重试（替代描述或重新定位）。



---



## 六、Layer 4：环境交互层（抽象接口）



所有接口采用平台适配器模式，框架启动时根据 `platform.system()` 选择实现。



### 模块 4.1：GUI 自动化接口 (IGUIAdapter)

```python

class IGUIAdapter:

    def click(x, y, button="left") -> None

    def double_click(x, y) -> None

    def move_mouse(x, y) -> None

    def type_text(text, interval=0.05) -> None

    def press_key(key) -> None

    def hotkey(*keys) -> None

    def scroll(delta_x, delta_y) -> None

    def get_screen_size() -> (width, height)

    def capture_screen(region=None) -> Image

    def get_dpi_scale() -> float   # 主屏幕 DPI 缩放因子

```



### 模块 4.2：可访问性树接口 (IA11yAdapter)

```python

class IA11yAdapter:

    def find_element(description, role=None, timeout=5.0) -> ElementInfo

    def get_element_at_position(x, y) -> Optional[ElementInfo]

    def get_active_window_info() -> WindowInfo

    def get_focused_element() -> Optional[ElementInfo]

    # 启发式缓存支持

    def mark_app_as_a11y_dead(app_identifier) -> None

    def is_app_a11y_dead(app_identifier) -> bool

    def reset_a11y_cache() -> None

```

数据结构 `ElementInfo`：坐标（逻辑像素）、角色、名称、值、是否启用/可见。



### 模块 4.3：终端与 PTY 接口 (ITerminalAdapter)

```python

class ITerminalAdapter:

    def run_command(command, cwd=None, env=None, timeout=300.0, silence_timeout=5.0) -> CommandResult

    def open_terminal(cwd=None) -> session_id

    def send_to_terminal(session_id, text) -> None

    def read_terminal_output(session_id, timeout=1.0) -> str

    def close_terminal(session_id) -> None

```

`CommandResult` 包含 `stdout`, `stderr`, `returncode`, `timed_out`, `interrupted_by_silence`。



### 模块 4.4：文件系统接口 (IFileSystemAdapter)

```python

class IFileSystemAdapter:

    def read_file(path) -> str

    def write_file(path, content, encoding="utf-8") -> None

    def list_directory(path, pattern=None) -> List[FileInfo]

    def move_to_trash(path) -> str

    def restore_from_trash(trash_path) -> None

    def delete_permanently(path) -> None   # 仅回收站清理

    def get_file_info(path) -> FileInfo

```

所有方法首先经过沙盒路径检查。



### 模块 4.5：应用管理接口 (IAppManagerAdapter)

```python

class IAppManagerAdapter:

    def launch_app(app_name, args=None) -> str

    def close_app(app_identifier) -> None

    def get_running_apps() -> List[AppInfo]

    def switch_to_app(app_identifier) -> None

```



### 模块 4.6：系统信息与剪贴板接口 (ISystemAdapter)

```python

class ISystemAdapter:

    def get_clipboard_text() -> str

    def set_clipboard_text(text) -> None

    def get_environment_variable(name) -> Optional[str]

    def set_environment_variable(name, value) -> None

    def get_current_working_directory() -> str

    def set_current_working_directory(path) -> None

```



---



## 七、配置文件结构（`~/.agent/config.yaml`）



```yaml

# 通用

log_level: INFO

audit_log: ~/.agent/audit.log



# 模型

llm:

  provider: openai  # or anthropic

  model: gpt-4o

  api_key: env:OPENAI_API_KEY



# 沙盒

sandbox:

  enabled: true

  root: ~/agent_sandbox

  read_only_paths:

    - /etc/os-release

    - /proc/cpuinfo

  ask_for_permission: true



# GUI

gui:

  lock_timeout: 10.0

  wait_interval: 0.5          # wait_for_element 轮询间隔

  screenshot_without_lock: true



# 终端

terminal:

  command_timeout: 300

  silence_timeout: 5.0        # 静默超时（秒）

  use_prompt_token: true

  default_shell: /bin/bash



# 安全

security:

  auto_confirm_level: low     # none, low, medium, high

  trash_dir: ~/.agent_trash

  dangerous_patterns:

    - "rm -rf"

    - "del /f"



# 记忆

memory:

  sliding_window_size: 5      # 保留完整对话轮数

  max_facts_in_slot: 20       # 关键事实槽最大条目数



# 循环保护

loops:

  max_iterations: 100         # Repeat/ForEach 默认最大迭代次数



# A11y 启发式

a11y:

  consecutive_failures_threshold: 2   # 连续失败次数达到此值则标记盲区

  reset_cache_per_task: true          # 每个新任务重置盲区缓存

```



---



## 八、日志与审计



- 审计日志：`~/.agent/audit.log`，每行 JSON，包含 `timestamp`, `task_id`, `action`, `params`, `result`, `user_confirmed`。

- 调试快照：压缩上下文前保存完整快照到 `~/.agent/context_snapshots/<timestamp>.json`。



---



## 九、已知限制与后续扩展



| 限制 | 说明 | 计划 |

|------|------|------|

| 多屏 DPI | 仅支持主屏幕，副屏坐标可能错位 | 后期根据窗口所在屏幕动态获取缩放因子 |

| 完全沙盒虚拟化 | 使用路径前缀检查，非底层隔离 | 如需更强隔离，可改用 Docker/OverlayFS |

| 完全交错规划 | 采用静态图+局部微调，非每步重规划 | 若场景需要，可增加重规划频率 |

| 实体注册表 | 无，依赖 LLM 记忆具体路径 | 若幻觉严重，后续加入符号引用机制 |

| 纯 VLM 模式 | 未实现，仅混合模式 | 可作为可选模式后期添加 |



---