"""
Apex Meta-Injector — Global configuration.

Provides a central dataclass-based config used by all components.
Supports TOML file overrides and environment variable fallbacks.
"""

from __future__ import annotations

import os
import shutil
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolPaths:
    """Paths to required external CLI tools."""

    exiftool: Optional[str] = None
    mkvpropedit: Optional[str] = None
    mkvmerge: Optional[str] = None
    bmxtranswrap: Optional[str] = None
    ffmpeg: Optional[str] = None
    ffprobe: Optional[str] = None

    def __post_init__(self):
        """Auto-detect tools on PATH if not explicitly set."""
        self.exiftool = self.exiftool or shutil.which("exiftool")
        self.mkvpropedit = self.mkvpropedit or shutil.which("mkvpropedit")
        self.mkvmerge = self.mkvmerge or shutil.which("mkvmerge")
        self.bmxtranswrap = self.bmxtranswrap or shutil.which("bmxtranswrap")
        self.ffmpeg = self.ffmpeg or shutil.which("ffmpeg")
        self.ffprobe = self.ffprobe or shutil.which("ffprobe")

    def get_status(self) -> dict[str, dict]:
        """Return availability status for each tool."""
        tools = {
            "exiftool": {"path": self.exiftool, "required": True, "url": "https://exiftool.org/"},
            "mkvpropedit": {"path": self.mkvpropedit, "required": True, "url": "https://mkvtoolnix.download/"},
            "mkvmerge": {"path": self.mkvmerge, "required": False, "url": "https://mkvtoolnix.download/"},
            "bmxtranswrap": {"path": self.bmxtranswrap, "required": True, "url": "https://github.com/bbc/bmx"},
            "ffmpeg": {"path": self.ffmpeg, "required": False, "url": "https://ffmpeg.org/download.html"},
            "ffprobe": {"path": self.ffprobe, "required": False, "url": "https://ffmpeg.org/download.html"},
        }
        for name, info in tools.items():
            info["available"] = info["path"] is not None
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
        # I/O-bound heuristic: more workers than cores
        auto = min(cpu_count * 4, self.max_workers_cap)
        return max(auto, 4)


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
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get(
        "APEX_DATA_DIR",
        Path.home() / ".apex-meta-injector"
    )))

    def __post_init__(self):
        """Ensure data directory exists."""
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> AppConfig:
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

        return config

    def _apply_overrides(self, data: dict):
        """Apply TOML overrides to this config."""
        if tools := data.get("tools"):
            for key, val in tools.items():
                if hasattr(self.tools, key) and val:
                    setattr(self.tools, key, val)

        if engine := data.get("engine"):
            for key, val in engine.items():
                if hasattr(self.engine, key):
                    setattr(self.engine, key, val)

        if server := data.get("server"):
            for key, val in server.items():
                if hasattr(self.server, key):
                    setattr(self.server, key, val)

        if log_level := data.get("log_level"):
            self.log_level = log_level


# Singleton for global access
_config: Optional[AppConfig] = None


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
