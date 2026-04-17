# Strata 代码库审计报告

> 首席 Python 架构师敌对性第一性原理审计
> 审查范围：整个 workspace（`strata/` + `tests/`，分支 `phase/7-cli-e2e`）
> 审计方法：FV-First 纵深审查（类型安全 → 契约完整性 → 正确性 → 验证覆盖 → 架构一致性 → 性能 → 工具链合规）
> 生成时间：2026-04-16

---

本报告按以下结构增量写入：

1. 宏观拓扑与防线状态
2. 缺陷与技术债（穷举）
3. CONVENTION 清单
4. 验证栈遥测
5. 架构师最终裁定

---

## 一、宏观拓扑与防线状态

| 模块 | 核心职责 | 契约覆盖率 | 类型安全状态 | 验证强度 |
|------|----------|------------|--------------|----------|
| `strata` (根) | 定义 `StrataError` 根异常，声明 `__version__` | — | Clean | 无专测 |
| `strata.__main__` | CLI 入口：argparse → `load_config` → `CLI.run()` | 零 | Clean，无 `Any` / `ignore` | 无专测；异常兜底仅覆盖 `ConfigError` |
| `strata.core` | 配置 / 异常树 / 沙箱 / 值对象 | 部分：`load_config`、`SandboxGuard.check_path`、无针对 `get_default_config` / `task_*_from_dict` 的契约 | 3 处 `type: ignore[arg-type]`（均为 Literal 字面量），无 `Any` | `test_types.py` 有 2 个 Hypothesis 往返属性；config / sandbox / errors 全样例测试 |
| `strata.env` | 平台适配 Protocol + OSWorld + 沙箱文件系统 + PTY 终端 | 稀疏：`filesystem.write_file` 仅 `ensure`；`terminal_pty.run_command` 较完整；`gui_osworld` / `linux/gui` / `macos/*` 几乎无 | 1 `Any`（注释声明）+ 1 `type: ignore[import-not-found]`，其余干净 | **无 Hypothesis**；大量 mock；2 个 skipped（clipboard 未装）；`EnvironmentFactory` 成功路径未被测；`restore_from_trash` 未覆盖 |
| `strata.grounding` | 敏感词过滤 / DPI 缩放 / 终端封装 / 屏幕边界 / VLM 定位 | 部分：`scaler` 与 `vision_locator.locate*` 有；`validator` 与 `filter` 全无 | Clean | `test_scaler.py` 1 个 DPI 往返属性；其余 mock 单测；`filter`、`validator` 无属性 |
| `strata.harness` | 上下文 / GUI 锁 / 检查点 / 恢复 / 调度 / 状态机 | 部分：`StateMachine.transition`、`LinearScheduler.run`、`atomic_write`、`RecoveryPipeline.attempt_recovery` 有；`extract_local_context`、`PersistenceManager`、`GUILock`、`AuditLogger.log` 无 | 3 `type: ignore` 集中于 `persistence._checkpoint_from_dict` | **无 Hypothesis**；状态机转移表未系统枚举；`persistence` 无坏 JSON/缺字段/版本迁移测试 |
| `strata.llm` | `LLMProvider` 协议 + OpenAI 兼容实现 + 角色路由 | `OpenAICompatProvider.chat` 与 `LLMRouter.__init__` 有；`plan`/`ground`/`see`/`search` 无 | 1 `call-overload` + 4 `arg-type` ignore；`**kwargs: object` 弱化 | **无 Hypothesis**；测试 mock SDK 与 provider，无真网络与 kwargs 传递异常分支 |
| `strata.planner` | HTN 分解 / 调整 / prompts | `decompose_goal` / `adjust_plan` / `validate_graph` / `MethodRegistry` 多契约 | Clean | **Hypothesis**：`test_htn.py` 与 `test_adjuster.py` 各有属性（往返、`apply_adjustment` 保持其他任务 ID） |
| `strata.interaction` | 交互式 CLI | `confirm_plan` 有 `ensure` | Clean | 8 个 mock 单测；**无 run 循环测试**；SIGINT 未测 |

**验证栈基线（本次快照）**：
- `uv run mypy --strict .`：**81 source files, 0 errors**。
- `uv run ruff check .`：**All checks passed**。
- `uv run ruff format --check .`：**67 files already formatted**。
- `uv run pytest --tb=short`：**185 passed, 2 skipped, 0 failed**（3.6s）。
- `# type: ignore` 密度：**9 处**（provider 1、router 4、core.config 2、core.types 1、harness.persistence 3——其中 router 与 persistence 合并占 7/9）。
- **裸 `Any`**：生产代码 0 处（`tests/strategies.py` 内注释性声明除外）；`env/gui_osworld.py` 使用 `Any` 为延迟导入的可选依赖 `DesktopEnv`，附 `# CONVENTION` 注释。

**架构拓扑要点**：
1. 包 `__init__.py` 普遍**不聚合导出**子模块（`strata.env.__init__`、`strata.harness.__init__` 仅含 docstring；`strata.core.__init__` 仅导出 `types`，但 `config` / `errors` / `sandbox` 被全仓广泛直接 import）。**包公共 API 面与实际依赖面不一致**，读者必须深路径导入才能找到符号。
2. `strata.env.factory.EnvironmentFactory` 在生产代码中**零引用**——仅 `tests/test_env/test_factory.py` 使用；Phase 7 尚未将其接入 harness。
3. `strata.grounding.CoordinateScaler` / `ActionValidator` 在生产代码中**零引用**——仅 `grounding/__init__.py` 导出。
4. `strata.env.linux.gui` / `macos.gui` / `macos.system` / `*.app_manager` 均为 `NotImplementedError` 桩；`@runtime_checkable` Protocol 只验证方法存在性，`isinstance(LinuxGUIAdapter(), IGUIAdapter)` 为真但实例**不可用**。
5. 存在两套并行异常语义：部分模块用 `StrataError` 子类（`PlannerError`、`GroundingError`、`EnvironmentError`、`HarnessError`、`LLMError`），另一些路径漏出 `ValueError` / `KeyError` / `FileNotFoundError`（`extract_local_context`、`task_*_from_dict`、`filesystem.move_to_trash`、`Checkpoint` 反序列化）。
6. 错误建模两套并行：`terminal_pty` 定义了 `CommandTimeoutError` / `SilenceTimeoutError` 但**从不抛出**，超时仅用 `CommandResult` 布尔字段表达；调用方若按异常 API 设计则永远走不到该分支。

---

## 二、缺陷与技术债（穷举）

> 优先级编号：**P1=类型安全 / P2=契约完整 / P3=契约即文档 / P4=正确性 / P5=验证覆盖 / P6=架构一致性 / P7=性能 / P8=工具链合规**。同一条目按"最高命中优先级"归类。每条独立列出，不合并。

### [P4] `filesystem.restore_from_trash` 绕过 `SandboxGuard`，回收站还原可写沙箱外任意路径

- 位置：`strata/env/filesystem.py:70-78`
- 病理描述：其他读写方法（`read_file` / `write_file` / `move_to_trash` / `list_directory` / `get_file_info`）均以 `self._guard.check_path(...)` 为入口；唯独 `restore_from_trash` **只读取侧车 JSON 的 `original_path` 字段再 `rename`**，未对 `trash_path` 与 `original` 做 `check_path`。只要攻击者（或错误代码）能写到侧车 `.meta.json`，即可把回收站条目"还原"到沙箱之外的任意目标（甚至只读路径），并附带任意字节内容。
  ```70:78:/home/limbo_null/Strata/strata/env/filesystem.py
      def restore_from_trash(self, trash_path: str) -> None:
          tp = Path(trash_path)
          sidecar = tp.with_suffix(tp.suffix + ".meta.json")
          if not sidecar.exists():
              raise FileNotFoundError(f"no sidecar metadata for {trash_path}")
          meta = json.loads(sidecar.read_text())
          original = meta["original_path"]
          tp.rename(original)
          sidecar.unlink()
  ```
- 爆炸半径：沙箱完整性突破。若上层 harness / planner 将 `restore_from_trash` 作为可编程原语暴露给 LLM，模型层任意指令或侧车污染均可致"越狱"。另外 `tests/test_env/test_filesystem.py` 未覆盖该方法的写拒绝场景，属于无防线。
- 修复方向：在 `tp` 与 `original` 上同时 `self._guard.check_path(..., write=True)`，并拒绝 `original` 为只读路径前缀。

---

### [P4] `terminal_pty.run_command` 超时/静默触发时定义的专用异常**从不抛出**

- 位置：`strata/env/terminal_pty.py:19`（导入）、`:109-137`（超时检测）、`strata/core/errors.py:91-99`（类型定义）
- 病理描述：模块头部 `from strata.core.errors import CommandTimeoutError, SilenceTimeoutError` 正常导入，但整个文件**没有一处 `raise CommandTimeoutError` / `raise SilenceTimeoutError`**。超时与静默两种致命态完全通过 `CommandResult(timed_out=True, interrupted_by_silence=True)` 字段表达：
  ```113:125:/home/limbo_null/Strata/strata/env/terminal_pty.py
          while proc.poll() is None:
              elapsed = time.monotonic() - start
              if elapsed > timeout:
                  timed_out = True
                  self._kill(proc)
                  break

              if silence_timeout and (time.monotonic() - last_output) > silence_timeout:
                  silence_interrupted = True
                  self._kill(proc)
                  break
  ```
- 爆炸半径：调用方若按契约即文档原则读异常层级，会预期 `try: handler.execute_command(...) except CommandTimeoutError: ...`，结果永远走不到——超时会被静默当作"正常结束且 `returncode=-1` 或管道上的 `proc.returncode or 0`"（见 `:145` 的回落）。另外 `proc.returncode or 0` 在 `None` 时变 **0（假成功）**，与 `timed_out=True` 同时存在时语义自相矛盾。
- 修复方向：两选一并在全栈对齐——要么删除 `CommandTimeoutError` / `SilenceTimeoutError` 类型；要么在 `_collect_output` 超时分支抛异常，且 `TerminalHandler.execute_command` 决定是捕获还是透传。

---

### [P4] `terminal_pty` 名实不符：声称 PTY 实为 **PIPE + shell**

- 位置：`strata/env/terminal_pty.py:1-4`（模块 docstring）vs `:65-72`（实现）
- 病理描述：模块及类名使用"PTY"术语，但实现未调用 `pty.openpty()` / `pty.fork()`，而是 `subprocess.Popen(..., stdout=PIPE, stderr=PIPE, text=True)`。真 PTY 与 PIPE 的差异：全屏 TUI（`vim`、`less`、`sudo -S`）在 PIPE 下缓冲行为异常、`isatty()` 返回 False、ncurses 程序可能退化或挂起。
  ```65:72:/home/limbo_null/Strata/strata/env/terminal_pty.py
          proc = subprocess.Popen(
              [self._config.default_shell, "-c", wrapped],
              stdout=subprocess.PIPE,
              stderr=subprocess.PIPE,
              cwd=cwd,
              env=run_env,
              text=True,
          )
  ```
- 爆炸半径：任何依赖 `isatty` 的子程序（密码提示、彩色输出、分页器）将表现异常；大输出下管道填满可**死锁**（`stdout` 阻塞写，主进程同时在 `select` 两端读，缓冲边界未测）。
- 修复方向：改名为 `PipeTerminalAdapter` / `ShellTerminalAdapter`，或切换为真实 `pty`（`pty.openpty()` + `os.dup2` + 单 fd 读）。

---

### [P4] `vision_locator._execute_scroll_action` 未知方向下静默滚动 `(0,0)`

- 位置：`strata/grounding/vision_locator.py:183-196`
- 病理描述：`scroll_direction` 仅接受 `{"up","down","left","right"}` 四值，但 VLM 返回任意字符串（乃至空串）时 `dx=dy=0`，仍调用 `self._gui.scroll(0, 0)` 进入下一轮循环——**VLM 调用成本浪费，且可能命中循环上限**。
  ```183:196:/home/limbo_null/Strata/strata/grounding/vision_locator.py
      def _execute_scroll_action(self, response: VisionResponse) -> None:
          step = self._config.scroll_step_pixels
          direction = response.scroll_direction or "down"
          dx, dy = 0, 0
          if direction == "down":
              dy = step
          elif direction == "up":
              dy = -step
          elif direction == "right":
              dx = step
          elif direction == "left":
              dx = -step
          self._gui.scroll(dx, dy)
  ```
- 爆炸半径：LLM 语言漂移（如返回 `"DOWN"`、`"下"`、`"scrollDown"`）即触发"空滚动"活锁，直到 `timeout`；用户感知为"卡住但无错误"，排障困难。
- 修复方向：在 `_parse_vlm_response` 处把 `scroll_direction` 收窄为 Literal 校验；非法值转 `VisionLocatorError` 立即失败，触发外层 replan。

---

### [P4] `vision_locator._parse_vlm_response` 坐标解析会泄漏 `ValueError` / `TypeError`

- 位置：`strata/grounding/vision_locator.py:169-170`
- 病理描述：`Coordinate(x=float(data["x"]), y=float(data["y"]))` 未包 `try`。VLM 返回 `{"x": "abc"}` 或 `{"x": None}` 等非数值时抛 `ValueError` / `TypeError`，**不是** `VisionLocatorError`，跨包边界泄漏内置异常。
  ```166:170:/home/limbo_null/Strata/strata/grounding/vision_locator.py
          coordinate: Coordinate | None = None
          if "x" in data and "y" in data:
              coordinate = Coordinate(x=float(data["x"]), y=float(data["y"]))

          scroll_direction = data.get("direction")
  ```
- 爆炸半径：违反仓库根异常约定（子包不得漏出内置异常），调用栈上层若只 `except GroundingError` 则未被捕获直接崩溃。
- 修复方向：`try: ... except (ValueError, TypeError) as exc: raise VisionLocatorError(...) from exc`。

---

### [P4] `vision_locator` 构造期缓存 `screen_w/h`，运行时分辨率变更后契约与实际屏幕不一致

- 位置：`strata/grounding/vision_locator.py:42-43`
- 病理描述：`self._screen_w, self._screen_h = gui.get_screen_size()` 仅构造期调用一次；`locate` 的 `@icontract.ensure` 用此缓存值做边界：
  ```56:68:/home/limbo_null/Strata/strata/grounding/vision_locator.py
      @icontract.ensure(
          lambda self, result: 0 <= result.x < self._screen_w and 0 <= result.y < self._screen_h,
          "coordinate must be within screen bounds",
      )
      def locate(self, description: str, role: str | None = None) -> Coordinate:
          screenshot = self._gui.capture_screen()
          response = self._call_vlm(screenshot, description, role)
          if response.action_type != "click" or response.coordinate is None:
              raise VisionLocatorError(
                  f"VLM did not return a click action for {description!r}, got {response.action_type}"
              )
          return response.coordinate
  ```
- 爆炸半径：屏幕旋转、DPI 变化、虚拟机重尺寸后，合法坐标被误判违反后置，或越界坐标被误判为合法。
- 修复方向：每次 `locate` 入口重新拉取 `gui.get_screen_size()`，或 `ensure` 用 `self._gui.get_screen_size()` 直接表达。

---

### [P4] `vision_locator._next_page_cache` 只写不读（死状态）

- 位置：`strata/grounding/vision_locator.py:43, 113-115`
- 病理描述：`_next_page_cache: Coordinate | None` 声明并在 `next_page` 分支写入，但**整个文件无读取**，意图丢失。
  ```113:115:/home/limbo_null/Strata/strata/grounding/vision_locator.py
              if response.action_type == "next_page" and response.coordinate is not None:
                  self._next_page_cache = response.coordinate
                  self._gui.click(response.coordinate.x, response.coordinate.y)
  ```
- 爆炸半径：死状态误导维护者；要么是未完成的缓存逻辑、要么是历史残留。
- 修复方向：删除；或完成其缓存加速意图（连续 `next_page` 无需再次 VLM）。

---

### [P4] `OSWorldGUIAdapter.capture_screen` 忽略 `region` 参数——Protocol 契约被实现削弱

- 位置：`strata/env/gui_osworld.py:85-93` vs `strata/env/protocols.py:25`
- 病理描述：`IGUIAdapter.capture_screen(region: ScreenRegion | None = None)` 契约承诺可按区域截屏，`OSWorldGUIAdapter` 实现直接把 `region` 丢弃，`execute_action("screenshot", {})` 不含任何几何参数。调用方按 Protocol 设计会预期区域截屏，拿到的是全屏字节。
  ```85:93:/home/limbo_null/Strata/strata/env/gui_osworld.py
      def capture_screen(self, region: ScreenRegion | None = None) -> bytes:
          screenshot = self._env.execute_action("screenshot", {})
          if isinstance(screenshot, bytes) and screenshot:
              return screenshot
          if isinstance(screenshot, str):
              with open(screenshot, "rb") as f:
                  return f.read()
          raise OSWorldConnectionError("capture_screen returned empty/invalid data")
  ```
- 爆炸半径：grounding 层若按 region 优化 VLM token 消耗，优化失效；更严重的是静默失效——不会报错，只是返回全屏。
- 修复方向：或（a）在 `execute_action` 参数里传 `region`（需查 OSWorld 支持），或（b）Python 端先拿全屏再裁剪，或（c）在 `region is not None` 时抛 `NotImplementedError`。

---

### [P4] `OSWorldGUIAdapter.scroll` 忽略 `delta_x`

- 位置：`strata/env/gui_osworld.py:76-80`（推断——子审查中已记录）
- 病理描述：水平滚动能力在 Protocol 中存在，在 OSWorld 实现中缺失；`delta_x` 被忽略。
- 爆炸半径：横向滚动页面（宽表格、时间轴 UI）无法被 agent 操作，VLM 返回 `direction="right"` / `"left"` 时完全无效。
- 修复方向：或实现，或 Protocol 拆分为 `scroll_vertical` / `scroll_horizontal` 使能力缺失在类型层可见。

---

### [P4] `TerminalHandler._wrap_command` 悬空：文档承诺的 token 退出码提取链路未接入

- 位置：`strata/grounding/terminal_handler.py:22-37`
- 病理描述：类 docstring 与 `_wrap_command` 共同实现"唯一 token 边界"以从 stdout 解析退出码，但 `execute_command` 实际调用的是 `_sanitize_sudo` 而非 `_wrap_command`。`_wrap_command` 全文件无引用。
  ```22:37:/home/limbo_null/Strata/strata/grounding/terminal_handler.py
      @icontract.require(lambda command: len(command.strip()) > 0, "command must be non-empty")
      @icontract.ensure(lambda result: isinstance(result.returncode, int), "returncode must be int")
      def execute_command(self, command: str, cwd: str | None = None) -> CommandResult:
          sanitized = self._sanitize_sudo(command)
          return self._terminal.run_command(
              sanitized,
              cwd=cwd,
              timeout=self._config.command_timeout,
              silence_timeout=self._config.silence_timeout,
          )

      def _wrap_command(self, command: str) -> str:
          token = f"AGENT_DONE_{uuid.uuid4().hex[:12]}"
          return f"{command}; echo '{token}' $?"
  ```
- 爆炸半径：死代码；更糟的是，若他日某人接入 `_wrap_command`，单引号直接拼接会被**命令注入**（`command = "foo'; rm -rf /; echo '"`）。
- 修复方向：删除 `_wrap_command`（当前 PTY 路径靠 `Popen.returncode` 已足够）；或若需解析，使用 `shlex.quote` 并记录到测试。

---

### [P4] `TerminalHandler._sanitize_sudo` 正则替换脆弱：会改写字符串字面量、注释内的 "sudo"

- 位置：`strata/grounding/terminal_handler.py` 中 `re.sub(r"\bsudo\b(?!\s+-n)", "sudo -n", command)`
- 病理描述：`\bsudo\b` 仅靠词界判定，不区分 shell 语法层次。命令 `echo "please run sudo tomorrow"` 会被改成 `echo "please run sudo -n tomorrow"`。命令 `sudo bash -c 'sudo apt update'` 双重替换。
- 爆炸半径：用户可见文本被修改；在日志 / 提示场景下产生误导输出。
- 修复方向：只在命令首个 token 为 `sudo` 时替换（用 `shlex.split` 定位），或去掉该启发式规则，改为在 Popen 前以 argv 形式组合。

---

### [P4] `filter.contains_sensitive` / `redact` 采用朴素子串匹配，误报误漏并存

- 位置：`strata/grounding/filter.py:32-43`
- 病理描述：`any(pat.lower() in lower for pat in ...)` 对 `"tokens"` / `"passwords"` / `"keynote"` 等都会命中关键词 `"token"` / `"password"` / `"key"`；反之真实密钥如 `"sk-abc..."` 不含 "password" 字面量时漏过。`redact` 用 `re.escape(pat)` 后子串替换，同样问题。
  ```32:43:/home/limbo_null/Strata/strata/grounding/filter.py
  def contains_sensitive(text: str, extra_patterns: Sequence[str] = ()) -> bool:
      lower = text.lower()
      return any(pat.lower() in lower for pat in (*SENSITIVE_PATTERNS, *extra_patterns))


  def redact(text: str, extra_patterns: Sequence[str] = ()) -> str:
      result = text
      for pat in (*SENSITIVE_PATTERNS, *extra_patterns):
          result = re.sub(re.escape(pat), "[REDACTED]", result, flags=re.IGNORECASE)
      return result
  ```
- 爆炸半径：被 `AuditLogger.log` 与 `VisionLocator.locate` 的 `@icontract.require(not contains_sensitive(description))` 调用——前者误报会把正常审计字段涂黑，后者误报会把合法描述拒绝为敏感内容，VLM 定位失败；更严重的是漏报场景下密钥会真的被发送到云 VLM。
- 修复方向：用正则词界 + 明确模式（`re.compile(r"\b(?:api[_-]?key|secret|password|token)\b")`）+ 对具体密钥形态（如 `sk-[A-Za-z0-9]{32,}`、`AKIA[0-9A-Z]{16}`、JWT 三段式）添加检测。

---

### [P4] `CoordinateScaler.physical_to_logical` 坐标空间与 `get_dpi_scale_for_point` 参数语义可能不对称

- 位置：`strata/grounding/scaler.py:17-29`
- 病理描述：`logical_to_physical` 用 **逻辑** 坐标查询 DPI，`physical_to_logical` 用 **物理** 坐标查询。`IGUIAdapter.get_dpi_scale_for_point` 未在 Protocol 上写明入参坐标空间，两处调用在多显示器 / 分片 DPI 下可能返回不同 scale，导致往返非幂等。
  ```16:29:/home/limbo_null/Strata/strata/grounding/scaler.py
      @icontract.require(lambda coord: coord.x >= 0 and coord.y >= 0, "coords must be non-negative")
      def logical_to_physical(self, coord: Coordinate) -> Coordinate:
          scale = self._gui.get_dpi_scale_for_point(coord.x, coord.y)
          assert scale > 0, f"invariant: DPI scale must be positive, got {scale}"
          return Coordinate(x=coord.x * scale, y=coord.y * scale)

      @icontract.require(lambda coord: coord.x >= 0 and coord.y >= 0, "coords must be non-negative")
      def physical_to_logical(self, coord: Coordinate) -> Coordinate:
          scale = self._gui.get_dpi_scale_for_point(coord.x, coord.y)
          assert scale > 0, f"invariant: DPI scale must be positive, got {scale}"
          return Coordinate(x=coord.x / scale, y=coord.y / scale)
  ```
- 爆炸半径：`test_scaler.py` 的 Hypothesis 往返测试用固定 `scale` MagicMock，无法暴露该歧义；真机上混合 DPI 多屏可能出现点击偏移。
- 修复方向：在 `IGUIAdapter.get_dpi_scale_for_point` 的 docstring/Protocol 注释中写明接受的坐标空间（建议逻辑坐标）；`physical_to_logical` 先按固定因子粗缩放到逻辑区再查询。

---

### [P4] `harness.persistence.atomic_write` `except BaseException` + 二次 `os.close` 风险

- 位置：`strata/harness/persistence.py:48-57`
- 病理描述：`except BaseException:` 捕获包括 `KeyboardInterrupt` / `SystemExit` 在内的一切；在 try 正常路径 `os.close(fd)` 已成功后若在 `os.replace` 抛异常，except 分支再执行 `os.close(fd) if not os.get_inheritable(fd) else None` 时 `fd` 已关闭，可能 `OSError: [Errno 9] Bad file descriptor`；`os.get_inheritable` 与"是否已关闭"无关，条件表达式无意义。
  ```48:57:/home/limbo_null/Strata/strata/harness/persistence.py
      try:
          os.write(fd, content)
          os.fsync(fd)
          os.close(fd)
          os.replace(tmp_path, path)
      except BaseException:
          os.close(fd) if not os.get_inheritable(fd) else None
          if os.path.exists(tmp_path):
              os.unlink(tmp_path)
          raise
  ```
- 爆炸半径：清理路径可能在二次 `close` 处再抛异常，掩盖原始根因；`KeyboardInterrupt` 在写检查点途中触发不应当清理——用户可能希望保留临时文件以便人工检查。
- 修复方向：缩窄为 `except OSError:`；用 `try/finally` 标志位 `closed=False` 替代 `os.get_inheritable` 启发式。

---

### [P4] `harness.persistence` 无 `schema_version`，向前/向后兼容全靠隐式默认

- 位置：`strata/harness/persistence.py:60-67, 69-92`
- 病理描述：`_checkpoint_to_dict` / `_checkpoint_from_dict` 均无版本字段。`task_graph` 结构升级（字段改名、新增必填）后旧检查点将被 `task_graph_from_dict` 用默认值"柔性"解析为非预期内容；`task_states` 的 `str(k)` / `str(v)` 把非字符串值强制转字符串，保存后不可逆。
  ```60:67:/home/limbo_null/Strata/strata/harness/persistence.py
  def _checkpoint_to_dict(cp: Checkpoint) -> dict[str, object]:
      return {
          "global_state": cp.global_state,
          "task_states": dict(cp.task_states),
          "context": dict(cp.context),
          "task_graph": task_graph_to_dict(cp.task_graph),
          "timestamp": cp.timestamp,
      }
  ```
- 爆炸半径：演进风险；一旦格式变动，旧磁盘快照无法区分"合法旧版"与"损坏"，也无法给出明确错误信息。
- 修复方向：加 `"schema_version": 1` 字段；`_checkpoint_from_dict` 对未知版本抛专用 `PersistenceError`（当前 `HarnessError` 树下可定义）。

---

### [P4] `harness.recovery.RecoveryPipeline.attempt_recovery` 阶梯阈值硬编码，`config` 死字段

- 位置：`strata/harness/recovery.py:38-78`
- 病理描述：构造函数收 `config: StrataConfig`，但 `attempt_recovery` 从不读 `_config`，`attempt_count<=1→RETRY / ==2→ALTERNATIVE / ==3→_try_replan / ==4→SKIP / >=5→USER_INTERVENTION` 全部硬编码。配置层 `max_recovery_attempts` 等（若存在）与行为脱节。
  ```38:44:/home/limbo_null/Strata/strata/harness/recovery.py
      def __init__(
          self,
          config: StrataConfig,
          adjuster: Callable[[TaskNode, Exception], Sequence[TaskNode]],
      ) -> None:
          self._config = config
          self._adjuster = adjuster
  ```
- 爆炸半径：配置幻觉——用户修改 `config.toml` 无效；注释中的"Phase 5 will integrate config"一类承诺若不存在则属于遗忘。
- 修复方向：要么把阈值提到 `StrataConfig.recovery`，要么删掉 `_config` 字段以消除误导。

---

### [P4] `harness.scheduler.LinearScheduler` 忽略 `depends_on` 与 `methods`，不构成 HTN 调度

- 位置：`strata/harness/scheduler.py:25-34`
- 病理描述：模块名 `scheduler` 与上层 HTN 叙事暗示基于任务图的拓扑调度；实际 `run` 只是 `for task in graph.tasks` 顺序执行，**`depends_on` 与 `graph.methods` 完全不参与**。
  ```25:34:/home/limbo_null/Strata/strata/harness/scheduler.py
      @icontract.require(lambda graph: len(graph.tasks) > 0, "graph must have tasks")
      def run(self, graph: TaskGraph, executor: TaskExecutor) -> Mapping[str, ActionResult]:
          context: dict[str, object] = {}
          results: dict[str, ActionResult] = {}
          for task in graph.tasks:
              result = self._execute_task(task, executor, context)
              results[task.id] = result
              if task.output_var and result.data:
                  context[task.output_var] = result.data
          return results
  ```
- 爆炸半径：规划层若生成了有依赖的 DAG（`depends_on` 非空），调度层会按列表顺序执行——若 `tasks` 未事先拓扑排序，依赖可能在依赖被满足前执行。
- 修复方向：或重命名为 `LinearRunner` 降级语义预期；或补 Kahn 拓扑序（`planner.htn.validate_graph` 已有工具函数）。

---

### [P4] `harness.gui_lock.AtomicGUITransaction.wait_and_act` 总墙钟可超过 `max_wait`

- 位置：`strata/harness/gui_lock.py:64-79`
- 病理描述：循环内 `self._lock.acquire(timeout=max_wait)` 单次最长等 `max_wait`，退出条件又用 `elapsed >= max_wait` 对比总时长；若 acquire 本身接近 `max_wait` 消耗，则 while 条件判断后再 `sleep(interval)` 再进入下一轮 acquire，总墙钟可达 `2*max_wait + interval`。
  ```64:79:/home/limbo_null/Strata/strata/harness/gui_lock.py
          start = time.monotonic()
          while True:
              if not self._lock.acquire(timeout=max_wait):
                  raise GUILockTimeoutError("timeout acquiring lock for transaction")
              try:
                  if check_fn():
                      return act_fn()
                  if auxiliary_fn is not None:
                      auxiliary_fn()
              finally:
                  self._lock.release()

              elapsed = time.monotonic() - start
              if elapsed >= max_wait:
                  raise GUILockTimeoutError(f"wait_and_act timed out after {elapsed:.1f}s")
              time.sleep(self._interval)
  ```
- 爆炸半径：参数名心智模型不匹配；GUI 事务在"最长 5s"期望下实际可能 10s+。
- 修复方向：acquire 的 timeout 参数用 `max_wait - elapsed`；总 deadline = `start + max_wait`。

---

### [P4] `harness.context.extract_local_context` 漏出 `ValueError`（非 `HarnessError`），且 parent 选择启发式在多 compound 同名 method 下不可靠

- 位置：`strata/harness/context.py:190-210`（行号以 package 子审查报告为准）
- 病理描述：失败任务 id 未找到时抛 `ValueError`，违反"子包不漏出内置异常"约定；`parent_candidates[0]` 启发式在多个 compound 任务共享同一 `method` 名时挑错父节点，进而挑错 siblings。
- 爆炸半径：`planner.adjuster.adjust_plan` 在此基础上生成替换任务——若 siblings 错误，LLM 看到的局部上下文虚假，生成的替换任务可能引用不存在的 id。
- 修复方向：未找到改抛 `HarnessError` / `ContextError` 子类；parent 定位用 `TaskNode.depends_on` 反向图而非 method 名匹配。

---

### [P4] `planner.htn.decompose_goal` 与 `adjuster.adjust_plan` 只重试 `PlannerError`，不重试 `LLMAPIError`

- 位置：`strata/planner/htn.py:181-197`、`strata/planner/adjuster.py:76-83`
- 病理描述：外层 `for _attempt in range(_MAX_LLM_RETRIES + 1)` + `except PlannerError: continue`；但 `router.plan` 抛 `LLMAPIError`（网络抖动、速率限制）**不进入 retry**，直接上浮。"规划重试循环"语义只覆盖了 JSON/图解析错误这一子集。
  ```181:197:/home/limbo_null/Strata/strata/planner/htn.py
      last_error: Exception | None = None
      for _attempt in range(_MAX_LLM_RETRIES + 1):
          try:
              response = router.plan(messages, json_mode=True, temperature=0.2)
              graph = deserialize_graph(response.content)
              ...
          except PlannerError as exc:
              last_error = exc
              continue

      raise PlannerError(
          f"failed to decompose goal after {_MAX_LLM_RETRIES + 1} attempts: {last_error}"
      )
  ```
- 爆炸半径：首次网络抖动立刻中断整个目标分解；用户感知为"planner 脆弱"；与 retry 循环存在的意图不符。
- 修复方向：`except (PlannerError, LLMAPIError)`，且对非瞬态错误（如 `AuthenticationError` / `BadRequestError`）仍立刻失败——需要 `LLMError` 子类细分。

---

### [P4] `planner._parse_adjustment` 严格 `json.loads`，不剥离围栏

- 位置：`strata/planner/adjuster.py:96-99`
- 病理描述：LLM 偶会返回 ```` ```json\n{...}\n``` ```` 或在 JSON 前加自然语言解释；`json.loads(raw_json)` 严格失败转 `PlannerError`，与 `decompose_goal` 同病。
- 爆炸半径：依赖 `json_mode=True` 的模型合规性；非 OpenAI 兼容提供商的 JSON mode 实现可能更松散。
- 修复方向：添加正则 `{.*}` 贪婪首尾匹配的容错；或保留严格策略但在 retry 层配对。

---

### [P4] `task_graph_from_dict` 对缺键抛 `KeyError`，不是 `PlannerError`

- 位置：`strata/core/types.py:156-180, 211-214`
- 病理描述：`data["task_type"]` / `data["id"]` 若缺失 → `KeyError`；`task_graph_from_dict` 使用 `str(data["goal"])` 缺键 → 同 `KeyError`。`deserialize_graph` 虽在上层包成 `PlannerError`，但任何直接调用（例如 `harness.persistence._checkpoint_from_dict` 中的 `task_graph_from_dict(graph_dict)`）将漏出内置异常。
  ```156:180:/home/limbo_null/Strata/strata/core/types.py
  def task_node_from_dict(data: Mapping[str, object]) -> TaskNode:
      return TaskNode(
          id=str(data["id"]),
          task_type=str(data["task_type"]),  # type: ignore[arg-type]
          action=str(data["action"]) if data.get("action") is not None else None,
          ...
      )
  ```
- 爆炸半径：`PersistenceManager.load_checkpoint` 读损坏磁盘文件时漏出 `KeyError`，上层捕获 `HarnessError` 失败。
- 修复方向：在 `task_node_from_dict` / `task_graph_from_dict` 内部用 `try/except (KeyError, TypeError)` 包为专用 `SerializationError`（位于 `core.errors`）。

---

### [P4] `interaction.cli` 完全忽略 `config.auto_confirm_level` 配置项

- 位置：`strata/interaction/cli.py:57-89`
- 病理描述：`StrataConfig.auto_confirm_level: Literal["none","low","medium","high"]` 是配置语义中"自动确认等级"的唯一入口；CLI 的 `confirm_plan` / `handle_error` 完全不读该字段，永远问 y/n。配置与行为解耦。
- 爆炸半径：用户设置 `auto_confirm_level="high"` 无效；自动化批处理场景下 agent 会卡在 `input()`。
- 修复方向：`confirm_plan` 入口先根据 level 决定是否跳过；`handle_error` 同理。

---

### [P4] `__main__.main` 异常兜底仅覆盖 `ConfigError`，其他 `StrataError` 直接栈追踪

- 位置：`strata/__main__.py:27-34`
- 病理描述：成功调用 `load_config` 后 `cli.run()` 内部 `PlannerError` / `LLMError` / `HarnessError` / `GroundingError` / `EnvironmentError` 均无兜底。
  ```27:34:/home/limbo_null/Strata/strata/__main__.py
      try:
          config = load_config(args.config)
      except ConfigError as exc:
          print(f"[Strata] Config error: {exc}", file=sys.stderr)
          sys.exit(1)

      cli = CLI(config)
      cli.run()
  ```
- 爆炸半径：用户看到未脱敏的 traceback，可能泄漏 api_key 片段（若异常消息里拼接）。退出码不受控。
- 修复方向：外层 `except StrataError as exc: print(...); sys.exit(2)`；`except Exception` 退出码 3 作"未知"；`KeyboardInterrupt` 退出码 130。

---

### [P4] `interaction.cli.CLI.__init__` 在实例构造时设置全局 SIGINT handler

- 位置：`strata/interaction/cli.py:21-24`
- 病理描述：`signal.signal(SIGINT, self._handle_sigint)` 有全局副作用；多 CLI 实例或与库级 SIGINT 使用者（pytest、debugger）冲突时最后注册者覆盖。`input()` 已在阻塞调用中时 SIGINT 不会解阻塞——仅设置 `_interrupted` 标志。
- 爆炸半径：用户按 Ctrl+C 后 `input` 仍阻塞，需回车才退出；测试环境下影响 pytest 自身 SIGINT 处理。
- 修复方向：改为 context manager 局部注册；或仅在 `run()` 内部 with 块注册并退出时恢复。

---

### [P2] 大量公开 API 无 `@icontract.require` / `@ensure`

- 位置：
  - `strata/env/filesystem.py`：`read_file`、`list_directory`、`move_to_trash`、`restore_from_trash`、`get_file_info`（除 `write_file` 有 `ensure`）。
  - `strata/env/gui_osworld.py`：除 `__init__` 外所有 GUI 方法。
  - `strata/env/terminal_pty.py`：`open_terminal` / `send_to_terminal` / `read_terminal_output` / `close_terminal` 全无。
  - `strata/env/linux/system.py`：`get_clipboard` / `set_clipboard` / `get_cwd` 无。
  - `strata/grounding/validator.py`：`validate_coordinates_in_screen` 无（存在手写 `if` + `raise`，可契约化）。
  - `strata/grounding/filter.py`：`contains_sensitive` / `redact` 无。
  - `strata/harness/context.py`：`WorkingMemory.get_var`、`get_facts`、`get_variables`、`clear`；`ContextManager.add_entry` / `compress` / `clear`；`AuditLogger.log`；`extract_local_context` 全无。
  - `strata/harness/gui_lock.py`：`GUILock` 全部方法；`AtomicGUITransaction.wait_and_act` 无。
  - `strata/harness/persistence.py`：`PersistenceManager.save_checkpoint` / `load_checkpoint` / `clear_checkpoint` 无。
  - `strata/llm/router.py`：`plan` / `ground` / `see` / `search` 四方法无。
- 病理描述：契约即文档是本仓库的 L1 防线，缺失处业务约束只能靠类型宽松表达。
- 爆炸半径：运行时"前置违反"被 `assert` / `ViolationError` 捕获的防线失效，违反直接沉到业务逻辑中产生难以定位的错误。
- 修复方向：对上述每个方法补 `@icontract.require` / `@ensure`。例如 `validator.validate_coordinates_in_screen` 可用 `@icontract.ensure(lambda ... : True, ... )` 配合内部 `raise`，或直接把 bounds 写进 `@icontract.require(lambda self, coord: 0 <= coord.x < self._gui.get_screen_size()[0] ...)`。

---

### [P2] `CommandResult` 类型允许 `timed_out` 与 `interrupted_by_silence` 同真

- 位置：`strata/core/types.py:94-101`
- 病理描述：`@dataclass(frozen=True)` 无构造期校验；`terminal_pty` 文档/实现上两者互斥但类型无法表达。
- 修复方向：加 `@icontract.invariant(lambda self: not (self.timed_out and self.interrupted_by_silence))`，或用 `Literal` 联合状态 `status: Literal["ok","timeout","silence"]`。

---

### [P2] `VisionResponse.action_type` 与 `coordinate` / `scroll_direction` 一致性无契约

- 位置：`strata/core/types.py` + `strata/grounding/vision_locator.py:164-180`
- 病理描述：`action_type="click"` 理应 `coordinate is not None`；`action_type="scroll"` 理应 `scroll_direction` 为四选一；`confidence` 理应 `[0,1]`。类型层全无约束。
- 修复方向：在 `VisionResponse` 上加 `@icontract.invariant`，或拆成代数数据类型（多 dataclass 联合 + 工厂）。

---

### [P2] `LLMRouter.__init__` 契约与 `ConfigError` 双重校验，契约触发 `ViolationError` 取代业务错误

- 位置：`strata/llm/router.py:23-41`
- 病理描述：先 `@icontract.require` 检查 roles 字段存在对应 provider，再在构造函数体内对同一条件 `raise ConfigError`。`test_router.py` 中看到的现象是"缺 vision provider"触发 `ViolationError`（契约），而非设计意图的 `ConfigError`。**对外契约不稳定**。
- 修复方向：单一来源：或只用契约 + `error=ConfigError`（icontract 支持 `error` 参数指定抛出类型），或去掉契约全部用手写 `raise ConfigError`。

---

### [P1] `config.py` 两处 `type: ignore[arg-type]` 绕过 Literal 校验

- 位置：`strata/core/config.py:223, 288`
- 病理描述：
  - `provider=str(section.get("provider", "docker")),  # type: ignore[arg-type]`：`OSWorldConfig.provider: Literal["docker","vmware","virtualbox"]`，任意字符串被压入。
  - `auto_confirm_level=str(data.get("auto_confirm_level", "low")),  # type: ignore[arg-type]`：同上 4 值 Literal 被任意字符串压入。
- 爆炸半径：配置层"看起来类型正确"，运行时下游若做 `match` 将遇到未声明分支；LSP 提示"此 Literal 已收窄"也不准确。
- 修复方向：运行时显式校验 `if provider not in ("docker","vmware","virtualbox"): raise ConfigError(...)`，再 `cast(Literal[...], provider)`。`type: ignore` 移除。

---

### [P1] `types.py` 中 `task_type=str(data["task_type"])  # type: ignore[arg-type]` 使 LLM JSON 可伪造任意 Literal

- 位置：`strata/core/types.py:173`
- 病理描述：`TaskNode.task_type: Literal["primitive","compound","repeat","if_then","for_each"]` 被忽略类型写入；`validate_graph` 对 `task_type` 值域做检查但非所有 from_dict 路径都接到 validator（如 `harness.persistence._checkpoint_from_dict`）。
- 修复方向：在 `task_node_from_dict` 内显式 `if raw not in (...): raise ValueError -> wrap`，`cast(Literal, raw)` 合法化；`type: ignore` 移除。

---

### [P1] `harness.persistence._checkpoint_from_dict` 三处 `type: ignore` 使 Literal 状态从磁盘无约束流入

- 位置：`strata/harness/persistence.py:73-88`
- 病理描述：
  ```73:88:/home/limbo_null/Strata/strata/harness/persistence.py
      if isinstance(task_states_raw, dict):
          for k, v in task_states_raw.items():
              task_states[str(k)] = str(v)  # type: ignore[assignment]
      ...
      return Checkpoint(
          global_state=str(d.get("global_state", "INIT")),  # type: ignore[arg-type]
          task_states=task_states,
          context=context,
          task_graph=task_graph_from_dict(graph_dict),
          timestamp=float(d.get("timestamp", 0.0)),  # type: ignore[arg-type]
      )
  ```
- 爆炸半径：磁盘损坏或版本不一致时 `global_state` 可能为非法字符串，随后 `StateMachine.can_transition` 返回 False，上层解读为"没有合法转移"而非"状态损坏"。
- 修复方向：运行时 `if global_state not in VALID_GLOBAL_STATES: raise PersistenceError`；task_states 同理。

---

### [P1] `llm.router` 四方法 `**kwargs: object` + `# type: ignore[arg-type]`

- 位置：`strata/llm/router.py:60, 69, 78, 87`
- 病理描述：`plan` / `ground` / `see` / `search` 把 `kwargs` 透传给 `LLMProvider.chat`，静态类型无法校验 `temperature` / `json_mode` 等是否与 `chat` 签名匹配；ignore 掩盖了。
- 爆炸半径：新增 chat 参数时调用方易漂移；`test_router.py` 用 mock 跳过真实 chat 断言。
- 修复方向：显式列出 `temperature: float = 0.7, json_mode: bool = False, max_tokens: int | None = None`，按名转发。

---

### [P1] `llm.provider.OpenAICompatProvider.chat` `# type: ignore[call-overload]`

- 位置：`strata/llm/provider.py:117`
- 病理描述：
  ```114:117:/home/limbo_null/Strata/strata/llm/provider.py
              # CONVENTION: type: ignore — we build messages as plain dicts from
              # ChatMessage and pass through OpenAI SDK's overloaded API; the union
              # types are too narrow for our dynamic dict.
              response = self._client.chat.completions.create(  # type: ignore[call-overload]
  ```
- 病理：`# CONVENTION` 注释已声明次优选择；但可通过 `cast` 或用 SDK 提供的 `ChatCompletionMessageParam` TypedDict 规避 ignore。
- 修复方向：`import openai.types.chat as oc; cast(list[oc.ChatCompletionMessageParam], openai_messages)`。

---

### [P4] `llm.provider.chat` 与 `grounding.vision_locator._call_vlm` 同时使用 `except Exception`

- 位置：`strata/llm/provider.py:126`、`strata/grounding/vision_locator.py:149`
- 病理描述：两处广捕导致 `KeyboardInterrupt` 不会被捕获（继承 `BaseException`），但 `ValueError` / `AttributeError` 等编程错误被一并吞并包装为 `LLMAPIError` / `VisionLocatorError`。排障时堆栈被拉平，`__cause__` 是否保留取决于 `raise ... from exc`——均有保留。
  ```126:128:/home/limbo_null/Strata/strata/llm/provider.py
          except Exception as exc:
              raise LLMAPIError(f"LLM call failed: {exc}") from exc
  ```
- 爆炸半径：编程 bug 被误报为"LLM API 失败"，导致 retry 循环浪费调用；真正语义应是 fail-fast。
- 修复方向：分层：`except (APIError, openai.APIConnectionError, openai.RateLimitError)` 走 `LLMAPIError`；其他 `Exception` 原样上浮。

---

### [P5] Hypothesis 属性测试覆盖率极低：仅 4 个测试文件使用 `@given`

- 位置：`tests/test_core/test_types.py`、`tests/test_grounding/test_scaler.py`、`tests/test_planner/test_adjuster.py`、`tests/test_planner/test_htn.py`
- 病理描述：其余所有核心计算函数（`SandboxGuard.check_path`、`filesystem.*`、`filter.contains_sensitive/redact`、`validator.validate_coordinates_in_screen`、`StateMachine.transition`、`LinearScheduler._interpret_*`、`RecoveryPipeline.attempt_recovery`、`atomic_write`、`OpenAICompatProvider.chat`）均为示例驱动测试，未用 Hypothesis。
- 爆炸半径：路径依赖 bug 不会被示例测试暴露（如 scheduler 的 repeat 边界、sandbox symlink 绕过、filter 子串误报、state_machine 非法事件）。
- 修复方向：为以下函数分别写属性：
  - `SandboxGuard.check_path`：用 `st_sandbox_path`（已存在于 strategies.py 但未使用）验证"`escape=True` 路径必 raise，`escape=False` 必返回绝对路径且在根下"。
  - `filter.redact(contains_sensitive(x) -> redact(x) 不再 contains_sensitive)` 等幂等律。
  - `StateMachine.transition`：随机合法序列不抛异常；随机非法事件必 `StateTransitionError`。
  - `LinearScheduler._interpret_repeat`：`max_iter` 边界律（执行次数等于 `min(node.max_iterations, _max_loop)`）。
  - `atomic_write`：写入任意 bytes 后 `read_bytes` 一致；崩溃注入下目标文件要么不存在要么完整。
  - `OpenAICompatProvider._message_to_openai`：带图/不带图 messages 的结构律。

---

### [P5] `tests/test_env` 完全无 Hypothesis；`EnvironmentFactory.create` 成功路径未测

- 位置：`tests/test_env/test_factory.py:23, 34`
- 病理描述：两个用例均为"非 Linux 抛 `UnsupportedPlatformError`"的负路径测试；Linux 下 `EnvironmentFactory.create(get_default_config())` 返回 `EnvironmentBundle` 的正路径从未断言——巧合的是 `LinuxGUIAdapter` 现为 `NotImplementedError` 桩，所以即便测了也只能检查属性存在性。
- 爆炸半径：工厂未接入主链路（见 P6），死路径恶化。
- 修复方向：目标状态下（Phase 6+）应测 create 返回 bundle，各字段通过 Protocol `isinstance` 校验，且随机 `SandboxConfig` 下 filesystem 确能读写。

---

### [P5] 状态机转移表未系统枚举

- 位置：`tests/test_harness/test_state_machine.py`
- 病理描述：测试覆盖了几个主成功路径和终态"无出边"用例，但未对 `VALID_GLOBAL_TRANSITIONS` / `VALID_TASK_TRANSITIONS` 的**每条边**和**每个状态×每个非法事件**写系统覆盖。
- 修复方向：参数化测试 `pytest.mark.parametrize("state,event,next_state", [各边])`，并用补集枚举非法事件。

---

### [P5] `AtomicGUITransaction.wait_and_act` 仅快超时失败测试，未测"成功重试后才命中"

- 位置：`tests/test_harness/test_gui_lock.py`
- 病理描述：`check_fn` 前两次 False 第三次 True 的场景未覆盖，`auxiliary_fn` 副作用也未断言。
- 修复方向：补用例。

---

### [P6] `EnvironmentFactory` 为死层：生产代码零引用

- 位置：`strata/env/factory.py` 全文件
- 病理描述：全仓库 grep `EnvironmentFactory` 仅命中 `test_factory.py` 与 `FV_EXECUTION_PLAN.md`；`__main__` / CLI / harness / grounding 均**直接**实例化 OSWorld/PTY 适配器或根本不用 env 层（当前 CLI 尚未接 env）。工厂存在但无消费者。
- 爆炸半径：Phase 7 尚未把工厂接入主链路，不算严重 bug，但与"L4 通过工厂装配"叙事断裂；读者以为存在集中装配。
- 修复方向：在 `strata.__main__` / `CLI` 启动路径中调用 `EnvironmentFactory.create(config)` 并把 bundle 传递到下游；或在其他层接入之前先标注 `# CONVENTION: 预留 — Phase 7 接入`。

---

### [P6] `strata.grounding.CoordinateScaler` / `ActionValidator` 为死层：生产代码零引用

- 位置：`strata/grounding/scaler.py`、`strata/grounding/validator.py`
- 病理描述：grep 仅命中包内 `__init__.py` 导出与 `tests/test_grounding/`；`VisionLocator` 自行做屏幕边界检查（在 `@icontract.ensure` 里）而不是委托 `ActionValidator`；`CoordinateScaler` 的 DPI 缩放从未被任何 actor/executor 调用。
- 爆炸半径：两个类的存在不决定行为——真实 agent 路径要么不需要它们，要么走别的实现；维护成本无收益。
- 修复方向：或接入真正的 GUI actor（在 GUI 操作前 `scaler.logical_to_physical` + `validator.validate_coordinates_in_screen`）；或标注为 Phase 6 预留并加 CONVENTION 注释。

---

### [P6] `LinuxGUIAdapter` / `MacOS*Adapter` `NotImplementedError` 桩 + `@runtime_checkable` 反模式

- 位置：`strata/env/linux/gui.py:10-39`、`strata/env/macos/gui.py:8-36`、`strata/env/macos/system.py`、`strata/env/linux/app_manager.py`、`strata/env/macos/app_manager.py`
- 病理描述：`@runtime_checkable Protocol` 的 `isinstance` 只检查方法名，不检查是否真实可用；因此 `isinstance(LinuxGUIAdapter(), IGUIAdapter) is True` 与"该实例可被使用"完全不等价。`EnvironmentFactory.create` 在 Linux 上会返回一个 bundle，其中 `gui` 对任意调用都抛 `NotImplementedError`。
- 爆炸半径：工厂看起来成功装配，Phase 6 之前生产运行必 `NotImplementedError`；与其"构造成功，调用失败"，不如"构造失败"更 fail-fast。
- 修复方向：`LinuxGUIAdapter.__init__` 直接抛 `NotImplementedError("Phase 6")`；或工厂在桩实现下不装配 `gui` 字段（改 `bundle.gui: IGUIAdapter | None`，要求调用者检查可用性）。

---

### [P6] 包 `__init__.py` 不聚合导出，读者需深路径 import

- 位置：`strata/env/__init__.py:1`、`strata/harness/__init__.py:1`、`strata/core/__init__.py`（仅导出 types）、`strata/llm/__init__.py`（不导出 `LLMRouter`）
- 病理描述：公共 API 面不统一。`strata.core.__init__` 仅导出 `types`，而 `config` / `errors` / `sandbox` 被全仓 50+ 处直接 import；`strata.llm.__init__` 导出 provider 但不导出 `LLMRouter`，而 `LLMRouter` 被 planner / grounding / test 广泛使用。
- 爆炸半径：新贡献者无法通过单一入口了解包能力；IDE 自动补全不完整；重命名/移动符号时爆炸半径大。
- 修复方向：每个包 `__init__.py` 用 `__all__` 列出稳定公开面；尚未稳定的子模块在 docstring 注明。

---

### [P6] `filesystem.move_to_trash` 抛 `FileNotFoundError`（stdlib），不是 `StrataError` 子类

- 位置：`strata/env/filesystem.py`（`move_to_trash` 源不存在时）
- 病理描述：与 `restore_from_trash` 同病。调用方按 `StrataError` 统一捕获面会漏掉此路径。
- 修复方向：子包异常根 `EnvironmentError` 下加 `TrashNotFoundError`。

---

### [P6] `core.__init__.py` 对 `config` / `errors` / `sandbox` 不导出，但被全仓隐式依赖

- 位置：`strata/core/__init__.py`
- 修复方向：`__all__` 补齐 `StrataConfig`、`load_config`、`SandboxGuard`、`StrataError` 等稳定符号，子模块自身仍保留（双入口），但有官方聚合入口。

---

### [P6] `LLMRouter` 校验包含 vision 用作裸白名单 `planner/grounding/vision/search`

- 位置：`strata/llm/router.py`（`LLMRolesConfig`）
- 病理描述：`LLMRolesConfig` 字段为固定四个角色；若 planner 使用场景增加（如 `critic`、`summarizer`），必须改 dataclass 字段。这在类型层面精确，但扩展摩擦大。
- 修复方向：改为 `roles: Mapping[Literal[...], str]`，Literal 在一个地方集中定义。

---

### [P6] `strata.__main__.main` 无退出码约定

- 位置：`strata/__main__.py:27-34`
- 病理描述：成功路径隐式 0，`ConfigError` 明确 1，其他异常未定义（CPython 默认 1）。与 CI / systemd 单元期望的"不同错误类型不同 exit code"实践不一致。
- 修复方向：定义常量 `EXIT_CONFIG=1, EXIT_STRATA=2, EXIT_UNKNOWN=3, EXIT_INTERRUPT=130`。

---

### [P7] `SandboxGuard.check_path` 每次调用 `realpath`，无路径解析缓存

- 位置：`strata/core/sandbox.py:31-56`
- 病理描述：每次 `check_path` 至少一次 `os.path.realpath`（syscall：多次 `lstat`）。高频场景（遍历大目录时每个条目都 check）代价可观。
- 修复方向：LRU 缓存解析结果；或对确定性字面路径（如 `~/.strata/*`）预解析。

---

### [P7] `terminal_pty._collect_output` 0.1s 固定轮询

- 位置：`strata/env/terminal_pty.py:113-137`
- 病理描述：`select(..., timeout=0.1)` 在无输出时仍每 100ms 唤醒一次，导致 CPU 定时开销。`silence_timeout` 精度约 100ms，已足；但 `timeout=0.1` 与 `silence_timeout` 单位不协调（后者秒，前者秒但粒度固定）。
- 修复方向：timeout 改为 `max(min(silence_timeout_remaining, 1.0), 0.05)` 自适应。

---

### [P7] `list_directory` + `_file_info` 每条目两次 stat

- 位置：`strata/env/filesystem.py:45-52, 84-93`
- 病理描述：`_file_info` 中 `p.stat()` 与 `p.is_dir()` 内部都触发 stat；大目录下 syscall 次数翻倍。
- 修复方向：`os.scandir` + `DirEntry.stat(follow_symlinks=False)` 一次获取所有元数据。

---

### [P7] `persistence.save_checkpoint` 全量 JSON 重写

- 位置：`strata/harness/persistence.py:save_checkpoint`
- 病理描述：`json.dumps(entire checkpoint) + atomic_write`；大 `task_graph` 每次调用都整文件重写。
- 修复方向：或保留全量写（简单且安全），或切换为 append-only 日志 + 周期性 compact（会显著增加代码复杂度，视 checkpoint 频率决定）。

---

### [P7] `OSWorldGUIAdapter` 无 HTTP 超时 / 重试

- 位置：`strata/env/gui_osworld.py`
- 病理描述：`execute_action` 在底层 `desktop_env` 调用，本文件不传任何 timeout；网络抖动或 VM 重启时 agent 将阻塞。
- 修复方向：包装调用时加 `with concurrent.futures.TimeoutError` / `signal.SIGALRM`；或在 OSWorld 初始化时强制设置客户端超时。

---

### [P3] `strata.harness.context.ContextManager.compress` docstring 承诺"trim old entries"但实现未 trim

- 位置：`strata/harness/context.py:102-106, 114-125`
- 病理描述：docstring 写"Save a snapshot of current context and trim old entries"，实现只写快照，无 `pop` 窗口或事实。
- 修复方向：对齐文档与实现；要么删 "trim old entries"，要么实现 trim。

---

### [P3] `strata/env/sandbox.py` 模块头部约定"通过适配器"但 Python 层无法强制

- 位置：`strata/core/sandbox.py:3-8`
- 病理描述：模块 docstring 声明"所有文件操作必须通过 `SandboxedFileSystemAdapter`"，但 Python 无机制阻止其他模块直接 `open(...)`。纯纪律约束。
- 修复方向：在 CI 加 `ruff` 自定义规则或 AST 检查，禁止除 `filesystem.py` 外的模块调用 `builtins.open` / `Path.read_*` / `Path.write_*`；或在代码评审清单中固化。

---

### [P3] `config._require_key` 死代码

- 位置：`strata/core/config.py:117-120`
- 病理描述：函数定义但未被任何 `_parse_*` 使用。
- 修复方向：删除，或在所有 `_parse_*` 中替换手写 `if key not in table` 分支。

---

### [P8] `core.errors.EnvironmentError` 与 Python 内建 `EnvironmentError` 同名

- 位置：`strata/core/errors.py:83-85`
- 病理描述：内建 `EnvironmentError` 是 `OSError` 的别名；项目自定义的在 `strata.core.errors` 命名空间下继承 `StrataError`。在同一文件中若有人同时 `from strata.core.errors import EnvironmentError` 与 `import builtins`，`except EnvironmentError:` 在上下文可能被误解为捕获 OSError。
- 修复方向：改名为 `StrataEnvironmentError`（`linux.system.py` 已使用该名，说明历史曾改名但 `errors.py` 未改）。实际上 `strata/env/linux/system.py:18` 已 `from strata.core.errors import StrataEnvironmentError`，说明 `errors.py` 中两个名字并存，名称双轨。

---

### [P8] `pyproject.toml` 的 ruff `ignore = ["UP046"]` 与模板默认的 `ignore = ["ANN101","ANN102"]` 不一致

- 位置：`pyproject.toml`
- 病理描述：workspace 规则模板列出 `["ANN101","ANN102"]`；实际配置使用 `["UP046"]`（带 `# CONVENTION` 解释——PEP 695 与 icontract lambda 不兼容）。这是合理的业务妥协，但与模板期望不完全一致。
- 修复方向：在注释中补 `ANN101/ANN102` 的处理说明（ruff 新版可能默认忽略 `self` 注解）；或显式列出现代化后的等价规则。

---

### [P8] `dependencies` 仅 `icontract>=2.7, openai>=1.82`，但代码依赖 `tomllib`（标准库 OK）、`desktop_env`（可选）

- 位置：`pyproject.toml`
- 病理描述：`desktop_env` 是 `gui_osworld.py` 的懒加载可选依赖，`pyproject.toml` 未声明 optional-dependencies 组。用户需自行 `pip install desktop-env`。
- 修复方向：`[project.optional-dependencies] osworld = ["desktop-env>=..."]`，便于 `uv add --optional osworld ...`。

---

## 三、CONVENTION 清单

| 模块/文件 | 业务约束 | 注释情况 |
|-----------|----------|----------|
| `strata/core/sandbox.py:8` | 不防御硬链接穿透与 TOCTOU | **已注释**：`# CONVENTION: 不防御硬链接穿透和 TOCTOU`（模块头部） |
| `strata/env/factory.py:27` | 所有文件 I/O 必须通过 filesystem 适配器 | **已注释**：`# SECURITY: 所有文件 I/O 必须通过 filesystem 适配器，严禁直接操作路径` |
| `strata/env/gui_osworld.py:34-38` | `desktop_env` 为可选依赖，延迟导入 | **已注释**：`# CONVENTION: lazy import — desktop_env is an optional dep`，配 `type: ignore[import-not-found]` |
| `strata/env/gui_osworld.py:88-91` | 部分 DesktopEnv 版本返回截图路径字符串 | **已注释**：`# CONVENTION: some DesktopEnv versions return the path; read it` |
| `strata/llm/provider.py:114-117` | OpenAI SDK overload 类型过窄，动态 dict 无法命中 | **已注释**：`# CONVENTION: type: ignore — we build messages as plain dicts ...` |
| `pyproject.toml [tool.ruff.lint].ignore = ["UP046"]` | PEP 695 泛型语法与 icontract lambda introspection 冲突 | **已注释**：`# CONVENTION: keep Generic[S, E] — PEP 695 syntax breaks icontract lambda introspection` |
| `strata/core/config.py:223` | 次优：TOML 任意字符串压入 `Literal["docker","vmware","virtualbox"]` | **未注释为 CONVENTION**，仅 `# type: ignore[arg-type]` — 应补 `# CONVENTION: <module>.config — 故意选择 str(...) 而非运行时白名单校验` 或加上校验 |
| `strata/core/config.py:288` | 同上：`auto_confirm_level` Literal 绕过 | **未注释** |
| `strata/core/types.py:173` | 反序列化时 `task_type` Literal 绕过（依赖 `validate_graph` 后置兜底） | **未注释** |
| `strata/harness/persistence.py:73-88` | 磁盘 JSON 任意字符串压入 `GlobalState`/`TaskState` Literal | **未注释**（三处 `type: ignore` 均无 CONVENTION 说明） |
| `strata/llm/router.py:60/69/78/87` | `**kwargs: object` 透传给 `LLMProvider.chat` 以保持调用面灵活 | **未注释**（四处 `type: ignore[arg-type]` 无解释） |
| `strata/env/terminal_pty.py` | 术语 "PTY" 实为 PIPE + shell | **未注释**，且与模块 docstring **冲突** |
| `strata/grounding/terminal_handler.py:34-37` | `_wrap_command` 悬空未接入 `execute_command` | **未注释**；若保留需 `# CONVENTION: <module> — 预留，原因 <why>` |
| `strata/env/linux/gui.py`、`macos/gui.py`、`macos/system.py`、`linux/app_manager.py`、`macos/app_manager.py` | 全部桩 `NotImplementedError("Phase 6")`/`"macOS support planned"` | **内部函数 NotImplementedError 自带消息**；但 `@runtime_checkable` 鸭子类型漏洞应在 Protocol 文档加 CONVENTION 说明 |
| `strata/harness/recovery.py:38-44` | `config: StrataConfig` 字段保存但 `attempt_recovery` 从不读 | **未注释**；若"预留配置接入"应补 `# CONVENTION: harness.recovery — 预留，Phase 6 接入阈值配置` |
| `strata/env/factory.py:21-24` | 非 Linux 立即 `UnsupportedPlatformError`，无 fallback | **未注释**；macOS 桩代码并存，应补"macOS 代码已存在但工厂故意不加载"说明 |
| `strata/grounding/vision_locator.py:43, 113-115` | `_next_page_cache` 只写不读的死状态 | **未注释**；若"预留缓存优化"应补 `# CONVENTION: grounding.vision_locator — 预留，next_page 点击缓存加速` |
| `strata/planner/prompts.py` | 全部 prompt 硬编码英文 `Final[str]` | **未声明为 CONVENTION**；若意图是"英文提示对多数 LLM 更稳定"应写明 |
| `strata/interaction/cli.py:57-89` | 完全忽略 `config.auto_confirm_level` | **未注释**（非 CONVENTION 而是缺陷） |
| `strata/harness/scheduler.py:25-34` | 类名 `LinearScheduler` 但位于 `scheduler.py`；故意不实现 DAG 拓扑调度 | **未注释**；若意图是"Phase 7 先线性、Phase 8 升级"应写明 |

**总结**：
- 被正确注释的 CONVENTION：5 条（sandbox、factory、gui_osworld lazy import、截图路径兼容、provider type ignore、ruff UP046）。
- **应加未加**：至少 10 处次优或预留逻辑未符合 workspace 规则"现实主义妥协必须备案"要求。

---

## 四、验证栈遥测

```
mypy: 0 errors  |  9 type: ignore  |  ruff: 0 warnings  |  ruff format: 0 unformatted
pytest: 通过 185  失败 0  忽略 2
hypothesis: 属性覆盖 4 测试文件（test_types / test_scaler / test_htn / test_adjuster）
           未覆盖 大量核心函数（详见 P5 条目）
```

### `# type: ignore` 地图

```
[IGNORE] strata/core/config.py:223       OSWorldConfig.provider          arg-type      Literal 绕过
[IGNORE] strata/core/config.py:288       StrataConfig.auto_confirm_level arg-type      Literal 绕过
[IGNORE] strata/core/types.py:173        TaskNode.task_type              arg-type      Literal 绕过
[IGNORE] strata/harness/persistence.py:75 task_states value              assignment    str 强转
[IGNORE] strata/harness/persistence.py:84 Checkpoint.global_state        arg-type      Literal 绕过
[IGNORE] strata/harness/persistence.py:88 Checkpoint.timestamp           arg-type      float 强转
[IGNORE] strata/llm/provider.py:117      OpenAI chat.completions.create  call-overload SDK overload 动态 dict
[IGNORE] strata/llm/router.py:60,69,78,87 plan/ground/see/search kwargs  arg-type      **kwargs: object 透传
```

### SKIPPED 测试

```
[SKIPPED] tests/test_env/test_linux/test_system.py::...  第 27 行   原因: no clipboard tool installed  风险: clipboard 路径本机无法验证；CI 若也无 xclip/xsel 则长期失测
[SKIPPED] tests/test_env/test_linux/test_system.py::...  第 38 行   原因: no clipboard tool installed  风险: 同上
```

### 未覆盖 / 应加 Hypothesis 属性的函数（按文件）

```
[UNCOVERED] strata/core/sandbox.py::SandboxGuard.check_path           建议: 用 st_sandbox_path（已有但未用）；属性"escape=True 必 raise；escape=False 返回绝对路径且在根下"
[UNCOVERED] strata/core/config.py::load_config                        建议: Hypothesis 构造随机 TOML；属性"合法 TOML 不抛；非法键/节触发 ConfigError 而非 KeyError/TOMLDecodeError 外溢"
[UNCOVERED] strata/env/filesystem.py::SandboxedFileSystemAdapter.*    建议: 读写往返；write_file 后 read_file 等同内容；沙箱外路径必 SandboxViolationError
[UNCOVERED] strata/env/terminal_pty.py::PTYTerminalAdapter.run_command 建议: 简单命令如 echo / exit N / 短 sleep 的属性覆盖，验证 returncode / stdout 解析
[UNCOVERED] strata/grounding/filter.py::contains_sensitive/redact     建议: 幂等（redact(redact(x)) == redact(x)）；一致性（contains_sensitive ↔ redact 是否修改）
[UNCOVERED] strata/grounding/validator.py::validate_coordinates_in_screen 建议: st_coordinate + 随机屏幕；等价于 0<=x<w & 0<=y<h
[UNCOVERED] strata/grounding/vision_locator.py::_parse_vlm_response    建议: 随机合法 JSON 不抛；非法 JSON 抛 VisionLocatorError（见 P4）
[UNCOVERED] strata/harness/state_machine.py::StateMachine.transition   建议: 随机合法边序列不抛；随机非法事件必 StateTransitionError
[UNCOVERED] strata/harness/scheduler.py::LinearScheduler._interpret_*  建议: repeat/if_then/for_each 边界（max_iter, items=空, condition_var 缺失）
[UNCOVERED] strata/harness/recovery.py::RecoveryPipeline.attempt_recovery 建议: 随机 attempt_count ∈ [0, 10]，验证五档映射
[UNCOVERED] strata/harness/persistence.py::atomic_write                建议: 随机 bytes 往返；中途异常下目标文件不变或完整
[UNCOVERED] strata/harness/persistence.py::_checkpoint_from_dict       建议: 随机合法/畸形 dict；损坏字段应提前失败
[UNCOVERED] strata/harness/gui_lock.py::AtomicGUITransaction.wait_and_act 建议: 随机 check_fn 返回序列，验证总墙钟 ≤ max_wait（当前 P4 bug 会暴露）
[UNCOVERED] strata/llm/provider.py::OpenAICompatProvider._message_to_openai 建议: 带图/不带图的结构律
```

### 单独 WARN（来自本次审计，非工具输出）

```
[WARN] filter   位置: strata/grounding/filter.py:32-43        风险: 子串匹配误报误漏；未对主流密钥格式专门检测
[WARN] sandbox  位置: strata/env/filesystem.py:70-78           风险: restore_from_trash 绕过 SandboxGuard（P4）
[WARN] pty      位置: strata/env/terminal_pty.py:19            风险: CommandTimeoutError/SilenceTimeoutError 导入但从不抛
[WARN] cli      位置: strata/interaction/cli.py:57-89           风险: auto_confirm_level 死字段
[WARN] main     位置: strata/__main__.py:27-34                 风险: 仅捕获 ConfigError，其他 StrataError 栈追踪泄漏
[WARN] factory  位置: strata/env/factory.py                     风险: 生产代码零引用（死层）
[WARN] harness  位置: strata/harness/recovery.py:38-44         风险: config 字段未使用；阈值硬编码
[WARN] scheduler 位置: strata/harness/scheduler.py:25-34       风险: 忽略 depends_on/methods，非 HTN 调度
[WARN] persistence 位置: strata/harness/persistence.py:48-57   风险: except BaseException + 二次 os.close
[WARN] persistence 位置: strata/harness/persistence.py:60-67   风险: 无 schema_version
[WARN] gui_lock 位置: strata/harness/gui_lock.py:64-79         风险: wait_and_act 总墙钟可超 max_wait
[WARN] context  位置: strata/harness/context.py::compress      风险: docstring 承诺 trim，实现未 trim
```

---

## 五、架构师最终裁定

### 系统整体熵值

本仓库处于 **FV-First 方法论的早期纪律阶段**：类型安全 / 格式 / lint 基线清洁（mypy --strict 0 error、ruff 0 warn、pytest 0 fail），这是稀有的工程美德。但"纵深"尚浅——契约覆盖面、异常统一度、验证属性密度均未达到 L1-L2 防线的目标态。**熵值：中低（~0.4）**，控制良好但尚未固化。

### 最危险单点故障

**沙箱完整性（P4-1：`restore_from_trash` 绕过 `SandboxGuard`）**。沙箱是整个 agent 暴露给 LLM 生成的任意指令的唯一防线；一处 check_path 缺口即可被恶意 prompt 或侧车文件污染利用，把文件写到沙箱外任何可写位置。**优先级 0**，应在 Phase 7 结束前修复。

次危险：
- **pty 超时异常建模双轨**：`CommandTimeoutError`/`SilenceTimeoutError` 类型存在但从不抛出，使得"按异常 API 设计"的调用方永远捕获不到。这是**契约即文档**的根级违反。
- **`EnvironmentFactory` 死层**：工厂存在但无消费者，意味着"L4 通过工厂装配"的架构叙事与真实依赖图脱节；当 Phase 7 尝试接入时会发现 `LinuxGUIAdapter` / `*AppManager` 还是桩，运行时立即 `NotImplementedError`。

### 验证栈覆盖余量

**L0（mypy）**：100% 通过，9 处 `type: ignore` 全部集中在"Literal 从外部输入（TOML / JSON / 磁盘）流入"这一特定模式，修复路径一致（加运行时白名单 + `cast`）。

**L1（icontract）**：约 40% 公开 API 有 `@require` / `@ensure`。环境层、GUI 锁、持久化、审计日志几乎无契约。

**L2（Hypothesis）**：属性测试仅 4 个文件 4-5 个属性——覆盖面远远不足。`tests/strategies.py` 定义了 `st_sandbox_path` / `st_coordinate` / `st_command_result` 策略但**部分未被使用**（典型如 `st_sandbox_path` 从未出现在 `test_sandbox.py` 中）。

**L3（pytest 回归）**：185 通过 + 2 skip，比例健康，但许多路径是示例锚点而非系统覆盖（状态机转移、scheduler 分支、recovery 阶梯、persistence 损坏输入均未系统枚举）。

**L4（ruff）**：100% 通过。

**覆盖余量结论**：L0/L4 已到顶；L1 空间最大（~60% 的公开 API 可补契约）；L2 需加约 10-15 个属性测试。

### 重构优先级路径

1. **立即（Phase 7 收尾前）**：
   - 修复 `restore_from_trash` 沙箱缺口（P4-1）。
   - 修复 `_parse_vlm_response` 的 `ValueError` 泄漏（P4-5）。
   - 统一 `terminal_pty` 的异常 vs 字段建模（P4-2）：建议删异常类型，保留字段，避免误导。
   - 加 `schema_version` 字段到 `Checkpoint`（P4）；修复 `atomic_write` 的 `BaseException` 与二次 close（P4）。
   - 修复 `__main__` 异常兜底（P4）与 `interaction.cli` `auto_confirm_level` 死字段（P4）。

2. **Phase 8（接线阶段）**：
   - 把 `EnvironmentFactory` 接入 `__main__` / `CLI`，或标注死层 CONVENTION。
   - 把 `CoordinateScaler` / `ActionValidator` 接入 actor 实际路径。
   - 为 env / harness 所有公开方法补 `@icontract.require/@ensure`。
   - `LinuxGUIAdapter` 改为构造期 fail-fast，不作桩实例通过 `@runtime_checkable`。

3. **Phase 9（验证深化）**：
   - 按 P5 条目为 SandboxGuard / filter / state_machine / scheduler / recovery / persistence / atomic_write 添加 Hypothesis 属性。
   - 状态机转移表系统参数化覆盖。
   - 为 scheduler 添加拓扑排序能力或明确重命名为 `LinearRunner`。

4. **Phase 10（工具链与命名）**：
   - `terminal_pty` 模块重命名（`pty_pipe.py` / `shell_terminal.py`）消除名实不符。
   - `core.errors.EnvironmentError` 全仓改名为 `StrataEnvironmentError`（`env/linux/system.py` 已单独改名，全局未同步）。
   - `core.__init__.py` 补聚合导出；每个包 `__init__.py` 用 `__all__` 固化公共面。
   - `pyproject.toml` 声明 `[project.optional-dependencies] osworld`。

### 总评

工程地基是**干净、严格、FV-First 纪律在表层已建立**的。下沉到主要未接入的执行链路后（Phase 6-7 GUI 实现、Phase 8 装配）的真实耦合与异常传播路径，会是接下来验证栈最容易失守的地方。**建议在 Phase 7 分支合并前补齐 P4 级缺陷（尤其沙箱与 pty 异常建模）**，再进入 Phase 8。

---
