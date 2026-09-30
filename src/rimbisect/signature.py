"""Turn logged errors into signatures, and decide whether a trial reproduced one."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX = re.compile(r"\b0x[0-9a-f]+\b|\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{8,}\b", re.I)
_QUOTED = re.compile(r"(?<!\w)'[^'\n]*'(?!\w)|\"[^\"\n]*\"")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_FRAME = re.compile(r"^\s*at\s+(?:\(wrapper [^)]*\)\s*)?([^(\s]+)")
_EXCEPTION = re.compile(r"\b(?:[A-Za-z_]\w*\.)+\w*Exception\b")
_GENERIC = re.compile(r"\[[^\]]*\]")
_DMD = re.compile(r"DMD<.*::([^>]+)>+")
_HARMONY = re.compile(r"^MonoMod\.Utils\.DynamicMethodDefinition\.|_Patch\d+$")
# Framework frames are shared by unrelated errors, a dictionary lookup failing for one.
_FRAMEWORK = ("System.", "UnityEngine.", "Mono.")
# RimWorld logs a stack trace it has logged before as a reference to the first one.
_REF = re.compile(r"^\[Ref ([0-9A-F]+)\]( Duplicate stacktrace)?", re.M)


def normalize(line: str) -> str:
    line = _GUID.sub("<GUID>", line)
    line = _HEX.sub("<HEX>", line)
    line = _QUOTED.sub("<Q>", line)
    line = _NUMBER.sub("<N>", line)
    return " ".join(line.split())


def _method(frame: re.Match) -> str:
    """Type and method name of a stack frame, the same whether Harmony patched it or not.

    Harmony renames a patched method to `Verse.Pawn.Verse.Pawn.Tick_Patch1` or
    `Verse.Pawn.DMD<DMD<Tick_Patch1>?123::Tick_Patch1>`, so whether some other mod in the
    trial patches it would otherwise change the signature.
    """
    name = _HARMONY.sub("", _DMD.sub(r"\1", _GENERIC.sub("", frame.group(1))))
    return normalize(".".join(name.split(".")[-2:]))


def signature_of(text: str) -> str:
    """First line of the error plus the method of its first stack frame outside the
    framework, normalized.

    The frame keeps two NullReferenceExceptions from different methods apart; the rest of
    the trace varies with load order and is left out. With a stack trace, an exception type
    in the first line stands for the whole line: the text around it often names a pawn, and
    -quicktest makes new colonists every launch.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    frames = [m for m in map(_FRAME.match, lines[1:]) if m]
    frame = next((m for m in frames if not m.group(1).startswith(_FRAMEWORK)), frames[0] if frames else None)
    if not frame:
        return normalize(lines[0])
    exception = _EXCEPTION.search(lines[0])
    return f"{exception.group(0) if exception else normalize(lines[0])} | {_method(frame)}"


@dataclass
class ErrorGroup:
    signature: str
    example: str
    count: int = 0

    @property
    def headline(self) -> str:
        return next((line.strip() for line in self.example.splitlines() if line.strip()), "")


def group_errors(errors: list[tuple[str, int]]) -> list[ErrorGroup]:
    """(text, repeats) pairs grouped by signature, exceptions first, then most frequent
    first, ties in log order. Exceptions are rare next to missing-def errors, which some
    mod lists log thousands of."""
    groups: dict[str, ErrorGroup] = {}
    refs: dict[str, str] = {}
    for text, repeats in errors:
        ref = _REF.search(text)
        sig = refs.get(ref.group(1), "") if ref and ref.group(2) else ""
        sig = sig or signature_of(text)
        if ref and not ref.group(2):
            refs[ref.group(1)] = sig
        if not sig:
            continue
        group = groups.setdefault(sig, ErrorGroup(sig, text))
        group.count += repeats
    return sorted(groups.values(), key=lambda g: ("Exception" not in g.example, -g.count))


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
        if self.pattern is not None and self.label:
            return {"kind": "text", "value": self.label}
        if self.pattern is not None:
            return {"kind": "regex", "value": self.pattern.pattern}
        if self.slower_than is not None:
            return {"kind": "slower_than", "value": self.slower_than}
        return {"kind": "crash", "value": None}
