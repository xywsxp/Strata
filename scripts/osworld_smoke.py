"""Smoke-run a few OSWorld benchmark tasks through Strata's OSWorldGUIAdapter.

# CONVENTION: 这不是正式的 eval harness — 仅作为 **Strata ↔ OSWorld wire-format
# 冒烟验证**。策略层（planner / vision / grounding loop）尚未在 __main__ 中接线，
# 所以这里用"按任务写死动作序列"的方式驱动，重点验证：
#
#   * EnvironmentFactory 能构造 OSWorldGUIAdapter
#   * Adapter 动作真的落到容器内 VM
#   * OSWorld 官方 evaluator 能对 Strata 留下的结果打分通过
#
# 跑法：
#   STRATA_OSWORLD_URL=http://localhost:5000 uv run python \
#       scripts/osworld_smoke.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from strata.core.config import load_config
from strata.env.factory import EnvironmentFactory
from strata.env.protocols import IGUIAdapter

CLIENT_PASSWORD = "password"  # matches OSWorld container default


@dataclass
class TaskResult:
    task_id: str
    instruction: str
    passed: bool
    detail: str


class OSWorldRawClient:
    """Direct HTTP helpers for OSWorld setup / evaluator calls.

    Strata's own adapter only needs ``/screen_size``, ``/run_python``, and
    ``/screenshot`` — the setup / evaluator layer speaks ``/execute`` which
    is intentionally kept out of the adapter surface.
    """

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def execute(self, command: list[str] | str, shell: bool = False) -> dict[str, object]:
        payload = json.dumps({"command": command, "shell": shell}).encode()
        req = urllib.request.Request(
            self._base + "/execute",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            raw = resp.read()
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        return parsed


def task_rename_directory(gui: IGUIAdapter, raw: OSWorldRawClient) -> TaskResult:
    task_id = "e0df059f (rename directory)"
    instruction = 'Rename "todo_list_Jan_1" on Desktop to "todo_list_Jan_2".'

    # Setup (from evaluation_examples/.../e0df059f...json) — both dirs cleared
    # so the test is idempotent.
    raw.execute(
        f"echo {CLIENT_PASSWORD} | sudo -S rm -rf "
        "~/Desktop/todo_list_Jan_1 ~/Desktop/todo_list_Jan_2",
        shell=True,
    )
    raw.execute(
        f"echo {CLIENT_PASSWORD} | sudo -S mkdir ~/Desktop/todo_list_Jan_1",
        shell=True,
    )

    # Strata-driven action: open a terminal and type the rename command.
    gui.hotkey("ctrl", "alt", "t")
    time.sleep(2.5)
    gui.type_text(
        "mv ~/Desktop/todo_list_Jan_1 ~/Desktop/todo_list_Jan_2",
        interval=0.02,
    )
    time.sleep(0.3)
    gui.press_key("enter")
    time.sleep(1.0)

    # Evaluate per the benchmark spec.
    eval_resp = raw.execute(
        "[ -d ~/Desktop/todo_list_Jan_2 ] && echo 'Directory exists.' "
        "|| echo 'Directory does not exist.'",
        shell=True,
    )
    output = str(eval_resp.get("output", ""))
    passed = output.strip() == "Directory exists."

    # Close the terminal so subsequent tasks start clean.
    gui.hotkey("ctrl", "d")
    time.sleep(0.5)

    return TaskResult(task_id, instruction, passed, output.strip())


def task_append_br(gui: IGUIAdapter, raw: OSWorldRawClient) -> TaskResult:
    task_id = "5ced85fc (append <br/>)"
    instruction = 'Append "<br/>" to each line of "1\\n2\\n3" and save in output.txt'

    raw.execute("rm -f /home/user/output.txt", shell=True)

    gui.hotkey("ctrl", "alt", "t")
    time.sleep(2.5)
    # # CONVENTION: pyautogui.typewrite() cannot type `<` / `>` (shift-combos
    # not supported), so we emit them via printf octal escapes: \\074=< \\076=>.
    # This keeps the Strata → adapter → VM path honest (no out-of-band writes)
    # while dodging a pyautogui keyboard-layout limitation.
    gui.type_text(
        "printf '1\\074br/\\076\\n2\\074br/\\076\\n3\\074br/\\076\\n' > /home/user/output.txt",
        interval=0.015,
    )
    time.sleep(0.3)
    gui.press_key("enter")
    time.sleep(1.0)

    eval_resp = raw.execute("cat /home/user/output.txt", shell=True)
    output = str(eval_resp.get("output", ""))
    passed = output == "1<br/>\n2<br/>\n3<br/>\n"

    gui.hotkey("ctrl", "d")
    time.sleep(0.5)

    return TaskResult(task_id, instruction, passed, repr(output))


def task_count_php_lines(gui: IGUIAdapter, raw: OSWorldRawClient) -> TaskResult:
    task_id = "4127319a-like (count php lines)"
    instruction = "Count all PHP lines under /tmp/php_project recursively."

    # We synthesise a tiny PHP project in /tmp so we don't depend on
    # OSWorld's downloaded setup.sh (keeps the smoke hermetic).
    raw.execute("rm -rf /tmp/php_project", shell=True)
    raw.execute(
        "mkdir -p /tmp/php_project/src && "
        "printf '<?php\\necho 1;\\necho 2;\\n' > /tmp/php_project/a.php && "
        "printf '<?php\\n$x=1;\\n$y=2;\\n$z=3;\\n' "
        "> /tmp/php_project/src/b.php",
        shell=True,
    )

    gui.hotkey("ctrl", "alt", "t")
    time.sleep(2.5)
    gui.type_text(
        "cd /tmp/php_project && find . -name '*.php' | xargs wc -l | tee /tmp/php_count.txt",
        interval=0.015,
    )
    time.sleep(0.3)
    gui.press_key("enter")
    time.sleep(1.0)

    eval_resp = raw.execute("cat /tmp/php_count.txt", shell=True)
    output = str(eval_resp.get("output", ""))
    # total = 3 lines in a.php + 4 lines in b.php = 7
    passed = "7 total" in output or " 7 total" in output

    gui.hotkey("ctrl", "d")
    time.sleep(0.5)

    return TaskResult(task_id, instruction, passed, output.strip())


def main() -> int:
    cfg = load_config("./config.toml")
    print(f"[+] osworld.server_url = {cfg.osworld.server_url}")
    bundle = EnvironmentFactory.create(cfg)
    gui = bundle.gui
    raw = OSWorldRawClient(cfg.osworld.server_url, cfg.osworld.request_timeout)

    tasks = [task_rename_directory, task_append_br, task_count_php_lines]
    results: list[TaskResult] = []
    for t in tasks:
        print(f"\n[>] {t.__name__}")
        try:
            r = t(gui, raw)
        except urllib.error.URLError as exc:
            r = TaskResult(t.__name__, "", False, f"network: {exc}")
        except Exception as exc:  # noqa: BLE001  # smoke harness, report any
            r = TaskResult(t.__name__, "", False, f"{type(exc).__name__}: {exc}")
        verdict = "PASS" if r.passed else "FAIL"
        print(f"    [{verdict}] {r.task_id}")
        print(f"    instruction: {r.instruction}")
        print(f"    detail:      {r.detail}")
        results.append(r)

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n[=] Summary: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
