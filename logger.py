"""Structured JSON logger for Amy. Write to amy.log, grep/jq for debugging."""
import json
import logging
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "amy.log")


class _JSONFormatter(logging.Formatter):
    def format(self, record):
        doc = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "event": record.getMessage(),
        }
        if hasattr(record, "data"):
            doc.update(record.data)
        return json.dumps(doc)


def _get_logger():
    log = logging.getLogger("amy")
    if not log.handlers:
        h = logging.FileHandler(LOG_PATH)
        h.setFormatter(_JSONFormatter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


def log(event: str, **kwargs):
    """Write a structured JSON log entry."""
    logger = _get_logger()
    record = logging.LogRecord("amy", logging.INFO, "", 0, event, (), None)
    record.data = kwargs
    logger.handle(record)


class Timer:
    """Context manager: log elapsed ms on exit."""
    def __init__(self, event: str, **kwargs):
        self._event = event
        self._kwargs = kwargs

    def __enter__(self):
        self._t = time.monotonic()
        return self

    def __exit__(self, *_):
        ms = round((time.monotonic() - self._t) * 1000)
        log(self._event, duration_ms=ms, **self._kwargs)
