"""SandboxGuard — path canonicalization and sandbox boundary enforcement.

安全边界：SandboxGuard 是框架内所有文件 I/O 的唯一授权检查点。任何文件操作
必须通过 SandboxedFileSystemAdapter（其内部注入 SandboxGuard）执行。框架其他
组件严禁绕过 filesystem 适配器直接使用 pathlib / open / os.* 进行文件读写。
此约束无运行时强制，依赖架构纪律。

# CONVENTION: 不防御硬链接穿透和 TOCTOU — 自用非对抗环境
"""

from __future__ import annotations

import os

import icontract

from strata.core.config import SandboxConfig
from strata.core.errors import SandboxViolationError


class SandboxGuard:
    """Canonicalize paths and enforce sandbox boundaries."""

    def __init__(self, config: SandboxConfig) -> None:
        self._enabled = config.enabled
        self._root = os.path.realpath(os.path.expanduser(config.root))
        self._read_only: frozenset[str] = frozenset(
            os.path.realpath(os.path.expanduser(p)) for p in config.read_only_paths
        )

    @icontract.require(lambda path: len(path.strip()) > 0, "path must be non-empty")
    @icontract.ensure(
        lambda self, result, write: (
            os.path.isabs(result)
            and (
                not self._enabled
                or self._is_under(result, self._root)
                or (not write and self._is_read_only(result))
            )
        ),
        "result must be absolute and within sandbox boundary",
    )
    def check_path(self, path: str, write: bool = False) -> str:
        """Single authorization checkpoint for all file I/O in the framework.

        Centralizes path canonicalization so callers never handle raw user paths
        directly. The sandbox boundary logic lives here (rather than scattered
        across adapters) to guarantee a single audit point.
        """
        if not self._enabled:
            return os.path.realpath(os.path.expanduser(path))

        resolved = os.path.realpath(os.path.expanduser(path))

        in_sandbox = self._is_under(resolved, self._root)
        in_read_only = self._is_read_only(resolved)

        if write and in_read_only:
            raise SandboxViolationError(f"write denied: {resolved} is in read-only paths")

        if in_sandbox:
            return resolved

        if not write and in_read_only:
            return resolved

        raise SandboxViolationError(f"path {resolved} is outside sandbox root {self._root}")

    def is_within_sandbox(self, path: str) -> bool:
        """Check whether *path* resolves to within the sandbox root."""
        if not self._enabled:
            return True
        resolved = os.path.realpath(os.path.expanduser(path))
        return self._is_under(resolved, self._root)

    def _is_under(self, resolved: str, root: str) -> bool:
        try:
            os.path.commonpath([resolved, root])
        except ValueError:
            return False
        return resolved == root or resolved.startswith(root + os.sep)

    def _is_read_only(self, resolved: str) -> bool:
        return any(self._is_under(resolved, ro) or resolved == ro for ro in self._read_only)
