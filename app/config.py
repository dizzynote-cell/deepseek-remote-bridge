# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Runtime configuration for the DeepSeek Remote Bridge.

Every value is optional and can come from, in order of precedence:

1. an explicit process environment variable,
2. ``config/.env`` beside the bridge,
3. the built-in default.

Nothing here reads DeepSeek Harness credentials; the harness runtime keeps its
own credential store, and the bridge only asks that runtime for account state.
"""

from __future__ import annotations

import os
import secrets
import shlex
from dataclasses import dataclass, field
from pathlib import Path


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return values
    except OSError:
        return values
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


@dataclass(frozen=True)
class BridgeConfig:
    """One immutable snapshot of the bridge configuration."""

    root: Path
    home: Path
    web_dir: Path
    host: str = "127.0.0.1"
    port: int = 8766
    base_path: str = "/"
    site_title: str = "DeepSeek Remote Bridge"
    site_subtitle: str = "网页、飞书与主机统一协作"
    version: str = "0.0.0"
    provider_id: str = "deepseek-harness"
    display_name: str = "DeepSeek Harness"
    peak_windows: str = "09:00-12:00,14:00-18:00"
    peak_days: str = "mon-fri"
    peak_offset_minutes: int = 480
    sibling_provider: str = "codex"
    sibling_name: str = "Codex"
    sibling_url: str = ""
    # Runtime selection.
    dsh_exe: Path = field(default_factory=lambda: Path(""))
    dsh_cli: Path = field(default_factory=lambda: Path(""))
    dsh_home: Path | None = None
    provider: str = "deepseek-account"
    model: str = "deepseek-flash"
    reasoning_effort: str = ""
    max_tokens: int = 0
    permission_preset: str = "workspace-write"
    approval_timeout_ms: int = 900_000
    question_timeout_ms: int = 900_000
    # Workspaces offered for new sessions.
    projects_root: Path = field(default_factory=lambda: Path.home() / "DSH-Bridge" / "projects")
    standalone_dir: Path = field(default_factory=lambda: Path.home() / "DSH-Bridge" / "sessions")
    # Modes.
    local_ui: bool = True
    feishu_enabled: bool = False
    cloud_enabled: bool = False
    relay_url: str = ""
    relay_token_file: Path | None = None
    voice_enabled: bool = False
    voice_env_file: Path | None = None
    attachments_max_bytes: int = 10 * 1024 * 1024
    attachments_max_batch_bytes: int = 30 * 1024 * 1024

    @property
    def data_dir(self) -> Path:
        return self.home / "data"

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def runtime_dir(self) -> Path:
        return self.home / "runtime"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    def ensure_dirs(self) -> None:
        for path in (
            self.home,
            self.data_dir,
            self.cache_dir,
            self.uploads_dir,
            self.runtime_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def load_token(self, name: str) -> str:
        """Read or create a persistent local token for one bridge-owned secret."""
        path = self.data_dir / f"{name}.token"
        try:
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass
        created = secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(created, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return created

    @property
    def adapter_token(self) -> str:
        return self.load_token("adapter")

    @property
    def session_token(self) -> str:
        return self.load_token("session")

    @property
    def normalized_base_path(self) -> str:
        """Base path with a leading and trailing slash, e.g. ``/`` or ``/dsh/``."""
        value = (self.base_path or "/").strip()
        if not value.startswith("/"):
            value = "/" + value
        if not value.endswith("/"):
            value = value + "/"
        if value == "//":
            value = "/"
        return value

    def external_url(self, path: str = "") -> str:
        base = self.normalized_base_path
        suffix = path.lstrip("/")
        host = self.host
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"http://{host}:{self.port}{base}{suffix}"


def _env(env_file: dict[str, str], name: str, default: str = "") -> str:
    if name in os.environ and os.environ[name] != "":
        return os.environ[name]
    return env_file.get(name, default)


def _env_int(env_file: dict[str, str], name: str, default: int) -> int:
    raw = _env(env_file, name, "")
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(env_file: dict[str, str], name: str, default: bool) -> bool:
    raw = _env(env_file, name, "")
    if raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_path(env_file: dict[str, str], name: str) -> Path | None:
    raw = _env(env_file, name, "")
    return Path(raw).expanduser() if raw else None


def _read_version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


#: Where a DeepSeek Harness desktop install may live. Checked in order.
DSH_EXE_CANDIDATES = (
    Path(r"C:\Program Files\DSH\DeepSeek Harness.exe"),
    Path(r"C:\Program Files\DeepSeek Harness\DeepSeek Harness.exe"),
    Path(r"C:\Program Files (x86)\DSH\DeepSeek Harness.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DSH" / "DeepSeek Harness.exe",
)

DSH_CLI_SUFFIX = Path("resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js")
DSH_CLI_UNPACKED_SUFFIX = Path(
    "resources/app.asar.unpacked/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js"
)


def discover_dsh(env_file: dict[str, str]) -> tuple[str, str]:
    """Resolve the harness executable and its CLI entry point.

    ``DSH_EXE`` and ``DSH_CLI`` win; otherwise the usual install locations are
    probed. An empty result is reported by ``config_problems`` instead of being
    guessed at.
    """
    explicit_exe = _env(env_file, "DSH_EXE", "")
    explicit_cli = _env(env_file, "DSH_CLI", "")
    exe = Path(explicit_exe) if explicit_exe else Path("")
    if not explicit_exe:
        for candidate in DSH_EXE_CANDIDATES:
            try:
                if str(candidate) and candidate.is_file():
                    exe = candidate
                    break
            except OSError:
                continue
    if explicit_cli:
        return str(exe), explicit_cli
    if str(exe) and exe.is_file():
        for suffix in (DSH_CLI_SUFFIX, DSH_CLI_UNPACKED_SUFFIX):
            candidate = exe.parent / suffix
            try:
                if candidate.is_file():
                    return str(exe), str(candidate)
            except OSError:
                continue
        return str(exe), str(exe.parent / DSH_CLI_SUFFIX)
    return str(exe), ""


def _archive_exists(path: str) -> bool:
    if not path:
        return False
    marker = path.lower().find(".asar")
    if marker > 0:
        return Path(path[: marker + 5]).is_file()
    return Path(path).is_file()


def config_problems(config: "BridgeConfig") -> list[str]:
    """Human-readable reasons the bridge cannot start the harness."""
    problems: list[str] = []
    if not str(config.dsh_exe) or not Path(str(config.dsh_exe)).is_file():
        problems.append(
            "DSH_EXE does not point at DeepSeek Harness.exe; set it in config/.env "
            "(see config/.env.example)."
        )
    if not _archive_exists(str(config.dsh_cli)):
        problems.append(
            "DSH_CLI does not point at the harness CLI entry point; set it in config/.env "
            "(see config/.env.example)."
        )
    return problems


def load_config(root: Path | None = None) -> BridgeConfig:
    """Build the configuration used by one bridge process."""
    root = (root or Path(__file__).resolve().parent.parent).resolve()
    env_file = _read_env_file(root / "config" / ".env")
    home = _env_path(env_file, "DSH_BRIDGE_HOME") or (root / "state")
    dsh_home = _env_path(env_file, "DSH_HOME")
    standalone = _env_path(env_file, "DSH_BRIDGE_STANDALONE_DIR") or Path.home() / "DSH-Bridge" / "sessions"
    projects = _env_path(env_file, "DSH_BRIDGE_PROJECTS_ROOT") or Path.home() / "DSH-Bridge" / "projects"
    voice_env = _env_path(env_file, "VOLC_TTS_ENV_FILE")
    max_tokens = _env_int(env_file, "DSH_BRIDGE_MAX_TOKENS", 0)
    dsh_exe, dsh_cli = discover_dsh(env_file)
    return BridgeConfig(
        root=root,
        home=home,
        web_dir=root / "web",
        host=_env(env_file, "DSH_BRIDGE_HOST", "127.0.0.1"),
        port=_env_int(env_file, "DSH_BRIDGE_PORT", 8766),
        base_path=_env(env_file, "DSH_BRIDGE_BASE_PATH", "/"),
        site_title=_env(env_file, "DSH_BRIDGE_SITE_TITLE", "DeepSeek Remote Bridge"),
        site_subtitle=_env(env_file, "DSH_BRIDGE_SITE_SUBTITLE", "网页、飞书与主机统一协作"),
        version=_read_version(root),
        provider_id=_env(env_file, "DSH_BRIDGE_PROVIDER_ID", "deepseek-harness"),
        display_name=_env(env_file, "DSH_BRIDGE_DISPLAY_NAME", "DeepSeek Harness"),
        peak_windows=_env(env_file, "DSH_BRIDGE_PEAK_WINDOWS", "09:00-12:00,14:00-18:00"),
        peak_days=_env(env_file, "DSH_BRIDGE_PEAK_DAYS", "mon-fri"),
        peak_offset_minutes=_env_int(env_file, "DSH_BRIDGE_PEAK_OFFSET_MINUTES", 480),
        sibling_provider=_env(env_file, "DSH_BRIDGE_SIBLING_PROVIDER", "codex"),
        sibling_name=_env(env_file, "DSH_BRIDGE_SIBLING_NAME", "Codex"),
        sibling_url=_env(env_file, "DSH_BRIDGE_SIBLING_URL", ""),
        dsh_exe=Path(dsh_exe),
        dsh_cli=Path(dsh_cli),
        dsh_home=dsh_home,
        provider=_env(env_file, "DSH_BRIDGE_PROVIDER", "deepseek-account"),
        model=_env(env_file, "DSH_BRIDGE_MODEL", "deepseek-flash"),
        reasoning_effort=_env(env_file, "DSH_BRIDGE_REASONING_EFFORT", ""),
        max_tokens=max_tokens,
        permission_preset=_env(env_file, "DSH_BRIDGE_PERMISSION_PRESET", "workspace-write"),
        approval_timeout_ms=_env_int(env_file, "DSH_BRIDGE_APPROVAL_TIMEOUT_MS", 900_000),
        question_timeout_ms=_env_int(env_file, "DSH_BRIDGE_QUESTION_TIMEOUT_MS", 900_000),
        projects_root=projects,
        standalone_dir=standalone,
        local_ui=_env_bool(env_file, "DSH_BRIDGE_LOCAL_UI", True),
        feishu_enabled=_env_bool(env_file, "DSH_BRIDGE_FEISHU", False),
        cloud_enabled=_env_bool(env_file, "DSH_BRIDGE_CLOUD", False),
        relay_url=_env(env_file, "DSH_BRIDGE_RELAY_URL", ""),
        relay_token_file=_env_path(env_file, "DSH_BRIDGE_RELAY_TOKEN_FILE"),
        voice_enabled=_env_bool(env_file, "DSH_BRIDGE_VOICE", False),
        voice_env_file=voice_env,
        attachments_max_bytes=_env_int(env_file, "DSH_BRIDGE_ATTACHMENT_MAX_BYTES", 10 * 1024 * 1024),
        attachments_max_batch_bytes=_env_int(
            env_file, "DSH_BRIDGE_ATTACHMENT_BATCH_MAX_BYTES", 30 * 1024 * 1024
        ),
    )


def shell_join(parts: list[str]) -> str:
    """Quote a command line for display in logs and docs."""
    return " ".join(shlex.quote(part) for part in parts)
