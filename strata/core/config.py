"""TOML configuration loading, validation, and default-value filling.

Config file lives at ``~/.strata/config.toml`` by default.
API keys are only stored there (never in the repository).
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

import icontract

from strata.core._validators import VALID_AUTO_CONFIRM, VALID_OSWORLD_PROVIDERS, validate_literal
from strata.core.errors import ConfigError
from strata.core.paths import PathsConfig

# ── Sub-config dataclasses ──


@dataclass(frozen=True)
class LLMProviderConfig:
    api_key: str
    base_url: str
    model: str

    def __repr__(self) -> str:
        masked = "sk-***" if self.api_key else "<empty>"
        return (
            f"LLMProviderConfig(api_key={masked!r}, "
            f"base_url={self.base_url!r}, model={self.model!r})"
        )


@dataclass(frozen=True)
class LLMRolesConfig:
    planner: str
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
    enable_scroll_search: bool
    max_scroll_attempts: int
    scroll_step_pixels: int


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
    os_type: str
    screen_size: tuple[int, int]
    headless: bool
    action_space: str
    docker_image: str | None
    # CONVENTION: HTTP 直连 OSWorld Docker server（port 5000 是上游默认），
    # 避免安装 desktop_env Python 包带来的 torch/easyocr 依赖地狱。
    server_url: str = "http://localhost:5000"
    request_timeout: float = 30.0


@dataclass(frozen=True)
class DebugConfig:
    """Debug UI server configuration."""

    enabled: bool
    port: int
    token: str
    intercept_prompts: bool = False
    max_checkpoint_history: int = 50


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
    paths: PathsConfig
    max_loop_iterations: int
    dangerous_patterns: Sequence[str] = field(default_factory=tuple)
    auto_confirm_level: Literal["none", "low", "medium", "high"] = "low"
    debug: DebugConfig = field(default_factory=lambda: DebugConfig(enabled=False, port=0, token=""))

    def __repr__(self) -> str:
        providers_repr = {k: repr(v) for k, v in self.providers.items()}
        return (
            f"StrataConfig(log_level={self.log_level!r}, "
            f"providers={providers_repr}, roles={self.roles!r}, ...)"
        )


# ── Helpers ──


def _expand(p: str) -> str:
    return str(Path(p).expanduser())


def _require_key(table: Mapping[str, object], key: str, context: str) -> object:
    if key not in table:
        raise ConfigError(f"required field '{key}' missing in [{context}]")
    return table[key]


def _str(val: object, field_name: str, context: str) -> str:
    if not isinstance(val, str) or not val.strip():
        raise ConfigError(f"'{field_name}' in [{context}] must be a non-empty string, got {val!r}")
    return val


# ── Parsing ──


def _parse_providers(raw: object) -> dict[str, LLMProviderConfig]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("at least one [providers.<name>] section with api_key is required")
    providers: dict[str, LLMProviderConfig] = {}
    for name, section in raw.items():
        if not isinstance(section, dict):
            raise ConfigError(f"[providers.{name}] must be a table")
        providers[str(name)] = LLMProviderConfig(
            api_key=_str(section.get("api_key"), "api_key", f"providers.{name}"),
            base_url=_str(section.get("base_url"), "base_url", f"providers.{name}"),
            model=_str(section.get("model"), "model", f"providers.{name}"),
        )
    return providers


def _parse_roles(raw: object, providers: Mapping[str, LLMProviderConfig]) -> LLMRolesConfig:
    if not isinstance(raw, dict):
        raise ConfigError("[roles] section is required")
    roles = LLMRolesConfig(
        planner=_str(raw.get("planner"), "planner", "roles"),
        grounding=_str(raw.get("grounding"), "grounding", "roles"),
        vision=_str(raw.get("vision"), "vision", "roles"),
        search=_str(raw.get("search"), "search", "roles"),
    )
    for role_name in ("planner", "grounding", "vision", "search"):
        provider_name = getattr(roles, role_name)
        if provider_name not in providers:
            raise ConfigError(
                f"roles.{role_name} references provider '{provider_name}' "
                f"which is not defined in [providers]"
            )
    vision_provider = providers[roles.vision]
    if not vision_provider.api_key.strip():
        raise ConfigError(
            f"roles.vision provider '{roles.vision}' must have a non-empty api_key "
            f"(perception layer depends entirely on VLM)"
        )
    return roles


def _parse_sandbox(raw: object) -> SandboxConfig:
    if not isinstance(raw, dict):
        raise ConfigError("[sandbox] section is required")
    root = _str(raw.get("root"), "root", "sandbox")
    return SandboxConfig(
        enabled=bool(raw.get("enabled", True)),
        root=_expand(root),
        read_only_paths=tuple(str(p) for p in (raw.get("read_only_paths") or ())),
        ask_for_permission=bool(raw.get("ask_for_permission", True)),
    )


def _parse_gui(raw: object) -> GUIConfig:
    section = raw if isinstance(raw, dict) else {}
    return GUIConfig(
        lock_timeout=float(section.get("lock_timeout", 10.0)),
        wait_interval=float(section.get("wait_interval", 0.5)),
        screenshot_without_lock=bool(section.get("screenshot_without_lock", False)),
        enable_scroll_search=bool(section.get("enable_scroll_search", True)),
        max_scroll_attempts=int(section.get("max_scroll_attempts", 10)),
        scroll_step_pixels=int(section.get("scroll_step_pixels", 300)),
    )


def _parse_terminal(raw: object) -> TerminalConfig:
    if not isinstance(raw, dict):
        raise ConfigError("[terminal] section with default_shell is required")
    default_shell = _str(raw.get("default_shell"), "default_shell", "terminal")
    silence_raw = raw.get("silence_timeout", 30.0)
    return TerminalConfig(
        command_timeout=float(raw.get("command_timeout", 300.0)),
        silence_timeout=float(silence_raw) if silence_raw is not None else None,
        default_shell=default_shell,
    )


def _parse_memory(raw: object) -> MemoryConfig:
    section = raw if isinstance(raw, dict) else {}
    return MemoryConfig(
        sliding_window_size=int(section.get("sliding_window_size", 5)),
        max_facts_in_slot=int(section.get("max_facts_in_slot", 20)),
    )


def _parse_paths(raw: object) -> PathsConfig:
    section = raw if isinstance(raw, dict) else {}
    run_root = str(section.get("run_root", "~/.strata/runs-fallback"))
    keep = int(section.get("keep_last_runs", 5))
    return PathsConfig(
        run_root=_expand(run_root),
        keep_last_runs=keep,
    )


def _parse_debug(raw: object) -> DebugConfig:
    section = raw if isinstance(raw, dict) else {}
    enabled = bool(section.get("enabled", False))
    port = int(section.get("port", 0))
    token = str(section.get("token", ""))
    intercept_prompts = bool(section.get("intercept_prompts", False))
    max_checkpoint_history = int(section.get("max_checkpoint_history", 50))
    if enabled:
        if port < 1024 or port > 65535:
            raise ConfigError(
                f"debug.port must be in range 1024-65535 when debug is enabled, got {port}"
            )
        if not token.strip():
            raise ConfigError("debug.token must be a non-empty string when debug is enabled")
    return DebugConfig(
        enabled=enabled,
        port=port,
        token=token,
        intercept_prompts=intercept_prompts,
        max_checkpoint_history=max_checkpoint_history,
    )


def _parse_osworld(raw: object) -> OSWorldConfig:
    section = raw if isinstance(raw, dict) else {}
    screen_raw = section.get("screen_size", [1920, 1080])
    screen_list = list(screen_raw) if isinstance(screen_raw, (list, tuple)) else [1920, 1080]
    docker_img = section.get("docker_image")
    return OSWorldConfig(
        enabled=bool(section.get("enabled", False)),
        provider=cast(
            Literal["vmware", "virtualbox", "docker"],
            validate_literal(
                str(section.get("provider", "docker")),
                VALID_OSWORLD_PROVIDERS,
                "osworld.provider",
                config_error=True,
            ),
        ),
        os_type=str(section.get("os_type", "Ubuntu")),
        screen_size=(int(screen_list[0]), int(screen_list[1])),
        headless=bool(section.get("headless", True)),
        action_space=str(section.get("action_space", "computer_13")),
        docker_image=str(docker_img) if docker_img is not None else None,
        server_url=str(section.get("server_url", "http://localhost:5000")),
        request_timeout=float(section.get("request_timeout", 30.0)),
    )


# ── Public API ──


@icontract.require(
    lambda path: path is None or Path(path).expanduser().is_file(),
    "config path must be None or an existing file",
)
@icontract.ensure(
    lambda result: all(p.api_key.strip() for p in result.providers.values()),
    "every provider must have a non-empty api_key",
)
def load_config(path: str | None = None) -> StrataConfig:
    """Load and validate configuration from a TOML file.

    Resolves ``~`` in paths. Uses ``~/.strata/config.toml`` when *path* is None.
    Raises ConfigError for missing required fields or invalid values.
    """
    resolved = Path(path).expanduser() if path else Path.home() / ".strata" / "config.toml"
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file: {exc}") from exc

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc

    providers = _parse_providers(data.get("providers"))
    roles = _parse_roles(data.get("roles"), providers)
    sandbox = _parse_sandbox(data.get("sandbox"))
    terminal = _parse_terminal(data.get("terminal"))
    gui = _parse_gui(data.get("gui"))
    memory = _parse_memory(data.get("memory"))
    paths = _parse_paths(data.get("paths"))
    osworld = _parse_osworld(data.get("osworld"))
    debug = _parse_debug(data.get("debug"))

    dangerous_raw = data.get("dangerous_patterns", ())
    dangerous: tuple[str, ...]
    if isinstance(dangerous_raw, (list, tuple)):
        dangerous = tuple(str(p) for p in dangerous_raw)
    else:
        dangerous = ()

    return StrataConfig(
        log_level=str(data.get("log_level", "INFO")),
        audit_log=_expand(str(data.get("audit_log", "~/.strata/audit.jsonl"))),
        trash_dir=_expand(str(data.get("trash_dir", "~/.strata/trash"))),
        providers=providers,
        roles=roles,
        sandbox=sandbox,
        gui=gui,
        terminal=terminal,
        memory=memory,
        osworld=osworld,
        paths=paths,
        max_loop_iterations=int(data.get("max_loop_iterations", 50)),
        dangerous_patterns=dangerous,
        auto_confirm_level=cast(
            Literal["none", "low", "medium", "high"],
            validate_literal(
                str(data.get("auto_confirm_level", "low")),
                VALID_AUTO_CONFIRM,
                "auto_confirm_level",
                config_error=True,
            ),
        ),
        debug=debug,
    )


def get_default_config() -> StrataConfig:
    """Return a StrataConfig with placeholder API keys and sensible defaults.

    Useful for testing without a real config file.
    """
    providers = {
        "default": LLMProviderConfig(
            api_key="sk-placeholder",
            base_url="https://api.example.com/v1",
            model="default-model",
        ),
    }
    return StrataConfig(
        log_level="INFO",
        audit_log=_expand("~/.strata/audit.jsonl"),
        trash_dir=_expand("~/.strata/trash"),
        providers=providers,
        roles=LLMRolesConfig(
            planner="default",
            grounding="default",
            vision="default",
            search="default",
        ),
        sandbox=SandboxConfig(
            enabled=True,
            root=_expand("~/strata-sandbox"),
            read_only_paths=(),
            ask_for_permission=True,
        ),
        gui=GUIConfig(
            lock_timeout=10.0,
            wait_interval=0.5,
            screenshot_without_lock=False,
            enable_scroll_search=True,
            max_scroll_attempts=10,
            scroll_step_pixels=300,
        ),
        terminal=TerminalConfig(
            command_timeout=300.0,
            silence_timeout=30.0,
            default_shell="/bin/bash",
        ),
        memory=MemoryConfig(
            sliding_window_size=5,
            max_facts_in_slot=20,
        ),
        osworld=OSWorldConfig(
            enabled=False,
            provider="docker",
            os_type="Ubuntu",
            screen_size=(1920, 1080),
            headless=True,
            action_space="computer_13",
            docker_image=None,
        ),
        paths=PathsConfig(
            run_root=_expand("~/.strata/runs-fallback"),
            keep_last_runs=5,
        ),
        max_loop_iterations=50,
        dangerous_patterns=("rm -rf", "mkfs", "dd if=", "> /dev/", "chmod 777"),
        auto_confirm_level="low",
        debug=DebugConfig(enabled=False, port=0, token=""),
    )
