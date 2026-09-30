"""Read a mod's About/About.xml.

Lifted from rim-loadorder-agent's ingest/build.py (parse_about), switched to lxml with
recover=True so the unescaped ampersands and stray tags some authors ship still parse,
and changed to follow the game on versioned lists: a <fooByVersion><v1.6> block replaces
<foo> for that version instead of adding to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

_PARSER = etree.XMLParser(recover=True, remove_comments=True, resolve_entities=False)
_WORKSHOP_ID = re.compile(r"(\d{6,})")


@dataclass(frozen=True)
class Dependency:
    package_id: str
    alternatives: tuple[str, ...] = ()
    name: str | None = None


@dataclass
class Mod:
    package_id: str
    name: str
    folder: Path
    workshop_id: str | None = None
    official: bool = False
    dependencies: list[Dependency] = field(default_factory=list)
    load_after: list[str] = field(default_factory=list)
    load_before: list[str] = field(default_factory=list)
    incompatible_with: list[str] = field(default_factory=list)
    supported_versions: list[str] = field(default_factory=list)


def _texts(node, tag: str) -> list[str] | None:
    el = node.find(tag)
    if el is None:
        return None
    return [li.text.strip() for li in el.findall("li") if li.text and li.text.strip()]


def _for_version(root, tag: str, version: str):
    """The <v1.6> element under <tagByVersion>, or None."""
    holder = root.find(tag + "ByVersion")
    return holder.find("v" + version) if holder is not None else None


def _id_list(root, tag: str, version: str) -> list[str]:
    versioned = _for_version(root, tag, version)
    if versioned is not None:
        items = [li.text.strip() for li in versioned.findall("li") if li.text and li.text.strip()]
    else:
        items = _texts(root, tag) or []
    return [item.lower() for item in items]


def _dependencies(root, version: str) -> list[Dependency]:
    holder = _for_version(root, "modDependencies", version)
    if holder is None:
        holder = root.find("modDependencies")
    out: list[Dependency] = []
    for li in holder.findall("li") if holder is not None else []:
        pid = (li.findtext("packageId") or "").strip().lower()
        if not pid:
            continue
        alternatives = tuple(a.lower() for a in (_texts(li, "alternativePackageIds") or []))
        out.append(Dependency(pid, alternatives, (li.findtext("displayName") or "").strip() or None))
    return out


def _published_id(about_dir: Path) -> str | None:
    try:
        text = (about_dir / "PublishedFileId.txt").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    match = _WORKSHOP_ID.search(text)
    return match.group(1) if match else None


def parse_about(path: Path, version: str, workshop_id: str | None = None, official: bool = False) -> Mod | None:
    """Parse About.xml; None when it has no packageId (the game skips those too)."""
    root = etree.parse(str(path), _PARSER).getroot()
    if root is None:
        return None
    package_id = (root.findtext("packageId") or "").strip().lower()
    if not package_id:
        return None
    folder = path.parent.parent
    if workshop_id is None:
        workshop_id = _published_id(path.parent) or (folder.name if _WORKSHOP_ID.fullmatch(folder.name) else None)
    return Mod(
        package_id=package_id,
        name=(root.findtext("name") or "").strip() or folder.name,
        folder=folder,
        workshop_id=workshop_id,
        official=official,
        dependencies=_dependencies(root, version),
        load_after=_id_list(root, "loadAfter", version) + _id_list(root, "forceLoadAfter", version),
        load_before=_id_list(root, "loadBefore", version) + _id_list(root, "forceLoadBefore", version),
        incompatible_with=_id_list(root, "incompatibleWith", version),
        supported_versions=_texts(root, "supportedVersions") or [],
    )
