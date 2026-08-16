"""
Logging configuration using loguru.

Configures structured logging with rotation, compression, and retention.
- Log level can be controlled via ENERGYID_LOG_LEVEL environment variable.
- Log file path can be controlled via ENERGYID_LOG_FILE environment variable.
- Console logging can be controlled via ENERGYID_CONSOLE_LOGGING environment variable.
All can be specified in .env file or as environment variables.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from energyid_monitor import common

load_dotenv(override=True)

DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVEL_ENV_VAR = "ENERGYID_LOG_LEVEL"
LOG_FILE_ENV_VAR = "ENERGYID_LOG_FILE"
CONSOLE_LOGGING_ENV_VAR = "ENERGYID_CONSOLE_LOGGING"
DEFAULT_LOG_FILE = "/var/log/energyid/energyid.log"
FALLBACK_LOG_FILE = Path.home() / ".local" / "log" / "energyid" / "energyid.log"
LOG_RETENTION_DAYS = 30
DEFAULT_CONSOLE_LOGGING = "false"


def mask_token(bearer_token: str) -> str:
    """
    Mask a bearer token for safe logging.
    Shows first 10 characters and last 4 characters, with ellipsis in between.
    """
    if not bearer_token:
        return ""

    token = bearer_token.replace("Bearer ", "").strip()

    if len(token) <= 14:
        return (
            f"Bearer {token[:2]}...{token[-2:]}"
            if len(token) > 4
            else "Bearer [REDACTED]"
        )

    return f"Bearer {token[:10]}...{token[-4:]}"


def get_log_level() -> str:
    """Get log level from environment variable or return default."""
    level = common._require_env(LOG_LEVEL_ENV_VAR, default=DEFAULT_LOG_LEVEL).upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if level not in valid_levels:
        logger.warning(
            f"Invalid log level '{level}', defaulting to {DEFAULT_LOG_LEVEL}"
        )
        return DEFAULT_LOG_LEVEL
    return level


def setup_logging(
    log_file: str | Path | None = None, log_level: str | None = None
) -> None:
    """Configure loguru logger with file rotation, compression, and retention."""
    logger.remove()

    if log_file is None:
        log_file = common._require_env(LOG_FILE_ENV_VAR, default=DEFAULT_LOG_FILE)
    log_file_path = Path(log_file)
    level = log_level or get_log_level()

    try:
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        fallback_path = FALLBACK_LOG_FILE
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        print(
            f"Warning: Permission denied creating log directory at {log_file_path.parent}. "
            f"Using fallback location: {fallback_path}",
            file=sys.stderr,
        )
        log_file_path = fallback_path

    log_format = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "{name}:{function}:{line} - {message}"
    )

    logger.add(
        log_file_path,
        rotation="00:00",
        retention=f"{LOG_RETENTION_DAYS} days",
        compression="gz",
        format=log_format,
        level=level,
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )

    console_logging = common._require_env(
        CONSOLE_LOGGING_ENV_VAR, default=DEFAULT_CONSOLE_LOGGING
    ).lower()
    if console_logging in ("true", "1", "yes", "on"):
        logger.add(
            lambda msg: print(msg, end=""),
            format=log_format,
            level=level,
            colorize=True,
        )

    logger.info(f"Logging initialized: level={level}, file={log_file_path}")
