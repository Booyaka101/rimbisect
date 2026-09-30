"""Turn logged errors into signatures, and decide whether a trial reproduced one."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX = re.compile(r"\b0x[0-9a-f]+\b|\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{8,}\b", re.I)
_QUOTED = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_FRAME = re.compile(r"^\s*at\s")


def normalize(line: str) -> str:
    line = _GUID.sub("<GUID>", line)
    line = _HEX.sub("<HEX>", line)
    line = _QUOTED.sub("<Q>", line)
    line = _NUMBER.sub("<N>", line)
    return " ".join(line.split())


def signature_of(text: str) -> str:
    """First line of the error plus its first stack frame, normalized.

    The frame keeps two NullReferenceExceptions from different methods apart; the rest of
    the trace varies with load order and is left out.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    key = normalize(lines[0])
    frame = next((line for line in lines[1:] if _FRAME.match(line)), None)
    return f"{key} | {normalize(frame)}" if frame else key


@dataclass
class ErrorGroup:
    signature: str
    example: str
    count: int = 0

    @property
    def headline(self) -> str:
        return next((line.strip() for line in self.example.splitlines() if line.strip()), "")


def group_errors(errors: list[tuple[str, int]]) -> list[ErrorGroup]:
    """(text, repeats) pairs grouped by signature, most frequent first, ties in log order."""
    groups: dict[str, ErrorGroup] = {}
    for text, repeats in errors:
        sig = signature_of(text)
        if not sig:
            continue
        group = groups.setdefault(sig, ErrorGroup(sig, text))
        group.count += repeats
    return sorted(groups.values(), key=lambda g: -g.count)


@dataclass
class Criterion:
    """What makes a trial FAIL. At most one of the three is set; with none, only a crash
    counts, and only under --crash-is-fail."""

    signature: str | None = None
    pattern: re.Pattern | None = None
    slower_than: float | None = None
    label: str = field(default="")

    def error_matches(self, text: str) -> bool:
        if self.signature is not None:
            return signature_of(text) == self.signature
        if self.pattern is not None:
            return self.pattern.search(text) is not None
        return False

    def line_matches(self, line: str) -> bool:
        return self.pattern is not None and self.pattern.search(line) is not None

    def describe(self) -> dict:
        if self.signature is not None:
            return {"kind": "signature", "value": self.signature, "example": self.label}
        if self.pattern is not None:
            return {"kind": "regex", "value": self.pattern.pattern}
        if self.slower_than is not None:
            return {"kind": "slower_than", "value": self.slower_than}
        return {"kind": "crash", "value": None}
