from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._~+\-/]+=*"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)(token|secret|password|credential|api[_-]?key)(\s*[=:]\s*)([^\s,;]+)"),
]


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("(?i)(authorization"):
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        elif "token|secret" in pattern.pattern:
            redacted = pattern.sub(r"\1\2[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def set_request_id(value: str | None = None) -> contextvars.Token[str | None]:
    return _request_id.set(value or uuid.uuid4().hex)


def get_request_id() -> str | None:
    return _request_id.get()


def reset_request_id(token: contextvars.Token[str | None]) -> None:
    _request_id.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        service = getattr(record, "service", None)
        if service:
            payload["service"] = service
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_logging(*, service: str, level: str = "INFO", json_logs: bool = True) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(_ServiceFilter(service))
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


class _ServiceFilter(logging.Filter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "service"):
            record.service = self.service
        return True


@dataclass
class MetricsRegistry:
    _counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(default_factory=lambda: defaultdict(float))
    _gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    @staticmethod
    def _key(name: str, labels: dict[str, str] | None = None) -> tuple[str, tuple[tuple[str, str], ...]]:
        return name, tuple(sorted((labels or {}).items()))

    def inc(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self._counters[self._key(name, labels)] += value

    def set(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self._gauges[self._key(name, labels)] = value

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            items = list(self._counters.items()) + list(self._gauges.items())
        for (name, labels), value in sorted(items, key=lambda item: item[0]):
            label_text = ""
            if labels:
                encoded = ",".join(f'{k}="{v.replace(chr(34), chr(92)+chr(34))}"' for k, v in labels)
                label_text = "{" + encoded + "}"
            lines.append(f"{name}{label_text} {value}")
        return "\n".join(lines) + ("\n" if lines else "")


metrics = MetricsRegistry()
