from __future__ import annotations

import logging
import re
from typing import Iterable

REDACTED = "[REDACTED]"

_SHAPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{35,}\b"),
    re.compile(r"\b(?:[a-z]{3,10}\s+){10,}[a-z]{3,10}\b"),
    re.compile(r"https?://\S*bioauth\S*"),
    re.compile(r"https?://[A-Za-z0-9-]+\.ngrok(?:-free)?\.app\S*"),
    re.compile(r"wss?://[A-Za-z0-9-]+\.ngrok(?:-free)?\.app\S*"),
)


class RedactionFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._exact: set[str] = set()

    def register_exact(self, value: str | None) -> None:
        if value and len(value) >= 8:
            self._exact.add(value)

    def register_many(self, values: Iterable[str | None]) -> None:
        for v in values:
            self.register_exact(v)

    def _redact(self, text: str) -> str:
        for v in self._exact:
            if v in text:
                text = text.replace(v, REDACTED)
        for pat in _SHAPE_PATTERNS:
            text = pat.sub(REDACTED, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def configure_logging(level: int = logging.INFO) -> RedactionFilter:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    redaction = RedactionFilter()
    handler.addFilter(redaction)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)

    return redaction
