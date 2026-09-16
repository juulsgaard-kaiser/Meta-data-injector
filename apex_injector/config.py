"""
Apex Meta-Injector — Global configuration.

Provides a central dataclass-based config used by all components.
Supports TOML file overrides and environment variable fallbacks.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ToolPaths:
    """Paths to required external CLI tools."""

    exiftool: str | None = None
    mkvpropedit: str | None = None
    mkvmerge: str | None = None
    mkvextract: str | None = None
    bmxtranswrap: str | None = None
    ffmpeg: str | None = None
    ffprobe: str | None = None

    def __post_init__(self):
        """Auto-detect tools on PATH if not explicitly set."""
        self.exiftool = self.exiftool or shutil.which("exiftool")
        self.mkvpropedit = self.mkvpropedit or shutil.which("mkvpropedit")
        self.mkvextract = self.mkvextract or shutil.which("mkvextract")
        self.mkvmerge = self.mkvmerge or shutil.which("mkvmerge")
        self.bmxtranswrap = self.bmxtranswrap or shutil.which("bmxtranswrap")
        self.ffmpeg = self.ffmpeg or shutil.which("ffmpeg")
        self.ffprobe = self.ffprobe or shutil.which("ffprobe")

    def get_status(self) -> dict[str, dict]:
        """Return availability status for each tool."""
        tools = {
            "exiftool": {"path": self.exiftool, "required": True, "url": "https://exiftool.org/"},
            "mkvpropedit": {"path": self.mkvpropedit, "required": True, "url": "https://mkvtoolnix.download/"},
            "mkvextract": {"path": self.mkvextract, "required": False, "url": "https://mkvtoolnix.download/"},
            "mkvmerge": {"path": self.mkvmerge, "required": False, "url": "https://mkvtoolnix.download/"},
            "bmxtranswrap": {"path": self.bmxtranswrap, "required": False, "url": "https://github.com/bbc/bmx"},
            "ffmpeg": {"path": self.ffmpeg, "required": False, "url": "https://ffmpeg.org/download.html"},
            "ffprobe": {"path": self.ffprobe, "required": False, "url": "https://ffmpeg.org/download.html"},
        }
        for name, info in tools.items():
            info["available"] = bool(info["path"] and shutil.which(info["path"]))
        return tools


@dataclass
class EngineConfig:
    """Configuration for the injection engine."""

    # Thread pool
    max_workers: int = 0  # 0 = auto-detect based on CPU count
    max_workers_cap: int = 64  # Upper limit for auto-detect

    # Atomic transaction
    create_backups: bool = True
    backup_suffix: str = ".apex_backup"
    staging_suffix: str = ".apex_tmp"

    # Retry policy for FileInUse errors
    file_in_use_retries: int = 3
    file_in_use_backoff_base: float = 0.5  # seconds

    # Hashing
    hash_algorithm: str = "xxhash"  # or "sha256" for maximum safety
    verify_bitstream: bool = True

    def get_worker_count(self) -> int:
        """Calculate effective worker count."""
        if self.max_workers > 0:
            return min(self.max_workers, self.max_workers_cap)
        cpu_count = os.cpu_count() or 4
        # Conservative disk concurrency; throughput is not auto-benchmarked.
        auto = min(cpu_count, 4, self.max_workers_cap)
        return max(auto, 1)


@dataclass
class ServerConfig:
    """Configuration for the GUI backend server."""

    host: str = "127.0.0.1"
    port: int = 0  # 0 = auto-find free port
    log_level: str = "warning"


@dataclass
class AppConfig:
    """Top-level application configuration."""

    tools: ToolPaths = field(default_factory=ToolPaths)
    engine: EngineConfig = field(default_factory=EngineConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    log_level: str = "INFO"
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("APEX_DATA_DIR", Path.home() / ".apex-meta-injector"))
    )

    def __post_init__(self):
        """Ensure data directory exists."""
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls, config_path: Path | None = None) -> AppConfig:
        """Load configuration from TOML file, falling back to defaults."""
        config = cls()

        # Try loading from explicit path or default location
        toml_path = config_path or config.data_dir / "config.toml"
        if toml_path.exists():
            try:
                import tomllib

                with open(toml_path, "rb") as f:
                    data = tomllib.load(f)
                config._apply_overrides(data)
                logger.info("Loaded config from %s", toml_path)
            except Exception as e:
                logger.warning("Failed to load config from %s: %s", toml_path, e)

        # Environment variable overrides
        if env_workers := os.environ.get("APEX_MAX_WORKERS"):
            config.engine.max_workers = int(env_workers)
        if env_log := os.environ.get("APEX_LOG_LEVEL"):
            config.log_level = env_log.upper()

        config.validate()
        return config

    def _apply_overrides(self, data: dict):
        import copy
        from dataclasses import fields

        draft = copy.deepcopy(self)
        if set(data) - {"tools", "engine", "server", "log_level"}:
            raise ValueError("Unknown configuration section")
        for section in ("tools", "engine", "server"):
            target = getattr(draft, section)
            allowed = {f.name for f in fields(target)}
            for key, value in data.get(section, {}).items():
                if key not in allowed:
                    raise ValueError(f"Unknown setting: {section}.{key}")
                old = getattr(target, key)
                expected = str if section == "tools" else type(old)
                if not isinstance(value, expected) or (expected in (int, float) and isinstance(value, bool)):
                    raise ValueError(f"Invalid value for {section}.{key}")
                setattr(target, key, value)
        draft.log_level = data.get("log_level", draft.log_level).upper()
        draft.validate()
        self.tools, self.engine, self.server, self.log_level = draft.tools, draft.engine, draft.server, draft.log_level

    def validate(self):
        e = self.engine
        if not 0 <= e.max_workers <= 64 or not 1 <= e.max_workers_cap <= 64:
            raise ValueError("Worker count must be 0..64 and cap must be 1..64")
        if not 0 <= e.file_in_use_retries <= 10 or not 0 <= e.file_in_use_backoff_base <= 30:
            raise ValueError("Invalid retry settings")
        if e.hash_algorithm not in ("xxhash", "sha256"):
            raise ValueError("Hash algorithm must be xxhash or sha256")
        for suffix in (e.backup_suffix, e.staging_suffix):
            if not suffix.startswith(".") or any(c in suffix for c in "/\\:"):
                raise ValueError("Invalid backup/staging suffix")
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError("Invalid log level")
        if not 0 <= self.server.port <= 65535 or self.server.host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Server must bind to loopback on a valid port")

    def save(self):
        import json
        import tempfile
        from dataclasses import asdict

        self.validate()
        lines = ["log_level = " + json.dumps(self.log_level), ""]
        for section in ("tools", "engine", "server"):
            lines.append(f"[{section}]")
            for key, value in asdict(getattr(self, section)).items():
                if value is not None:
                    lines.append(f"{key} = {json.dumps(value)}")
            lines.append("")
        fd, path = tempfile.mkstemp(dir=self.data_dir, suffix=".toml.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
                f.flush()
                os.fsync(f.fileno())
            os.replace(path, self.data_dir / "config.toml")
        finally:
            Path(path).unlink(missing_ok=True)


# Singleton for global access
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Get or create the global application config."""
    global _config
    if _config is None:
        _config = AppConfig.load()
    return _config


def set_config(config: AppConfig):
    """Set the global application config (for testing or manual setup)."""
    global _config
    _config = config
