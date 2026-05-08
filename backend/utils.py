"""Shared utilities (logging setup)."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import LoggingConfig, get_config, get_env

_INITIALISED = False


def setup_logging(cfg: LoggingConfig, level: str = "INFO") -> None:
    """Configure root logger with rotating file + stderr handlers (idempotent)."""
    global _INITIALISED
    if _INITIALISED: return
    cfg.file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S%z")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fh = RotatingFileHandler(cfg.file, maxBytes=cfg.max_bytes, backupCount=cfg.backup_count, encoding="utf-8")
    fh.setFormatter(fmt); root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr); sh.setFormatter(fmt); root.addHandler(sh)
    for n in ("httpx", "httpcore", "urllib3"): logging.getLogger(n).setLevel(logging.WARNING)
    _INITIALISED = True


def get_logger(name: str) -> logging.Logger:
    """Shorthand for logging.getLogger(name)."""
    return logging.getLogger(name)


def init_default() -> None:
    """Convenience for CLI scripts: read config + env, set up logging."""
    setup_logging(get_config().logging, get_env().log_level)
