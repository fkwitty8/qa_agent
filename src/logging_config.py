"""
logging_config.py — Structured JSON-lines logging for the QA Agent.

Configures Python's logging module to write JSON-lines to
evidence/traces/app.log in addition to the console.

Each log record is serialised as a single JSON object per line with fields:
  timestamp   ISO 8601 UTC
  level       DEBUG | INFO | WARNING | ERROR | CRITICAL
  logger      logger name (e.g. src.agents, src.tools)
  message     the log message string
  event       structured event name (from extra= dict, if provided)
  ...         any other fields from extra=

Usage:
    from src.logging_config import configure_logging
    configure_logging()   # call once at application startup
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_LOG_PATH = _PROJECT_ROOT / "evidence" / "traces" / "app.log"


class _JsonLinesFormatter(logging.Formatter):
    """Format a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level":   record.levelname,
            "logger":  record.name,
            "message": record.getMessage(),
        }

        # Merge any extra= fields the caller passed
        skip = {
            "args", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "message",
            "module", "msecs", "msg", "name", "pathname", "process",
            "processName", "relativeCreated", "stack_info", "thread",
            "threadName", "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in skip:
                try:
                    json.dumps(value)   # ensure serialisable
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    log_path: Path | None = None,
    level: str | None = None,
) -> None:
    """Configure root logger with a JSON-lines file handler and a console handler.

    Safe to call multiple times — duplicate handlers are not added.

    Args:
        log_path: Path to the JSON-lines log file.
                  Defaults to LOG_FILE env var → evidence/traces/app.log.
        level:    Log level string (DEBUG/INFO/WARNING/ERROR).
                  Defaults to LOG_LEVEL env var → INFO.
    """
    resolved_path = Path(
        log_path or os.getenv("LOG_FILE", str(_DEFAULT_LOG_PATH))
    )
    resolved_level_str = level or os.getenv("LOG_LEVEL", "INFO")
    resolved_level = getattr(logging, resolved_level_str.upper(), logging.INFO)

    root = logging.getLogger()

    # Avoid adding duplicate handlers on repeated calls
    handler_paths = {
        getattr(h, "baseFilename", None) for h in root.handlers
    }
    if str(resolved_path) in handler_paths:
        return

    root.setLevel(resolved_level)

    # JSON-lines file handler
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(resolved_path, encoding="utf-8")
    file_handler.setFormatter(_JsonLinesFormatter())
    file_handler.setLevel(resolved_level)
    root.addHandler(file_handler)

    # Human-readable console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s")
    )
    console_handler.setLevel(logging.WARNING)   # only warnings+ to console
    root.addHandler(console_handler)
