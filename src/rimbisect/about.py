"""Read a mod's About/About.xml.

Lifted from rim-loadorder-agent's ingest/build.py (parse_about), and changed to read the
file the way the game does (Verse.ModMetaData): text decoded like File.ReadAllText, tags
matched in any case, and a file it cannot parse, or one without a packageId, gives the id
the game makes up. A <fooByVersion><v1.6> block replaces <foo> for that version instead
of adding to it.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

_PARSER = etree.XMLParser(remove_comments=True, resolve_entities=False, encoding="utf-8")
_LISTS = ("modDependencies", "loadAfter", "forceLoadAfter", "loadBefore", "forceLoadBefore", "incompatibleWith")
_TAGS = {tag.lower(): tag for tag in ("packageId", "name", "author", "description", "displayName",
                                      "alternativePackageIds", "supportedVersions", *_LISTS,
                                      *(tag + "ByVersion" for tag in _LISTS))}
_BOMS = ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"), (codecs.BOM_UTF8, "utf-8-sig"),
         (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"))
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
    problem: str | None = None  # why the game makes up this mod's packageId


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


def _utf16(text: str) -> memoryview:
    return memoryview(text.encode("utf-16-le")).cast("H")


def _made_up_id(author: str, name: str, description: str) -> str:
    """GenText.StableStringHash and ConvertToASCII, as ModMetaData.TryParsePackageId uses them."""
    text = "none"
    if description:
        number = 23
        for unit in _utf16(description):
            number = (number * 31 + unit) & 0xFFFFFFFF
        text = str(number - (1 << 32) if number >= 1 << 31 else number).replace("-", "")[:3]
    ascii_ = lambda part: "".join(chr(u) if u < 0x80 and chr(u).isalnum() else chr(u % 25 + 65)  # noqa: E731
                                  for u in _utf16(part))
    return (ascii_(author + text) + "." + ascii_(name)).lower()


def _root(path: Path):
    """The parsed file with tags in the case this module looks for, or None where the game
    falls back to defaults (no file, or XML it cannot parse)."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    codec = next((codec for bom, codec in _BOMS if raw.startswith(bom)), "utf-8")
    try:
        root = etree.fromstring(raw.decode(codec, errors="replace").encode("utf-8"), _PARSER)
    except etree.XMLSyntaxError:
        return None
    for el in root.iter(tag=etree.Element):
        el.tag = _TAGS.get(el.tag.lower(), el.tag)
    return root


def _inner(root, tag: str, default: str = "") -> str:
    el = root.find(tag)
    return default if el is None else "".join(el.itertext())


def parse_about(path: Path, version: str, workshop_id: str | None = None, official: bool = False) -> Mod:
    """Parse About.xml, which may not exist; the game still loads such a folder as a mod."""
    folder = path.parent.parent
    root = _root(path)
    if root is None:
        root = etree.Element("ModMetaData")
        problem = "About.xml could not be read" if path.exists() else "there is no About/About.xml"
    else:
        problem = None
    name = _inner(root, "name") or (f"Workshop mod {folder.name}" if workshop_id else folder.name)
    package_id = _inner(root, "packageId").strip().lower()
    if not package_id:
        package_id = _made_up_id(_inner(root, "author", "Anonymous"), name,
                                 _inner(root, "description", "No description provided."))
        problem = problem or "About.xml has no packageId"
    if workshop_id is None:
        workshop_id = _published_id(path.parent) or (folder.name if _WORKSHOP_ID.fullmatch(folder.name) else None)
    return Mod(
        package_id=package_id,
        name=name.strip(),
        problem=problem,
        folder=folder,
        workshop_id=workshop_id,
        official=official,
        dependencies=_dependencies(root, version),
        load_after=_id_list(root, "loadAfter", version) + _id_list(root, "forceLoadAfter", version),
        load_before=_id_list(root, "loadBefore", version) + _id_list(root, "forceLoadBefore", version),
        incompatible_with=_id_list(root, "incompatibleWith", version),
        supported_versions=_texts(root, "supportedVersions") or [],
    )
