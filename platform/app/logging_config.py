"""Structured logging configuration.

When DEBUG=true:  human-readable format with timestamps
When DEBUG=false: JSON lines format for log aggregation tools
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per log line for structured log collection."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Inject correlation_id if available
        try:
            from app.middleware.request_logging import get_correlation_id
            cid = get_correlation_id()
            if cid != "-":
                log_entry["req_id"] = cid
        except Exception:
            pass

        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(debug: bool = True) -> None:
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if debug:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.setLevel(logging.DEBUG)
    else:
        handler.setFormatter(JSONFormatter())
        root.setLevel(logging.INFO)

    root.addHandler(handler)

    # Quiet noisy loggers
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
