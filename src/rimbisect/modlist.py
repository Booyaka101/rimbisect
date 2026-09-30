"""Installed mods, the player's load order, and dependency-safe subsets of it."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree

from .about import Mod, parse_about
from .errors import UserError
from .install import Game

CORE = "ludeon.rimworld"
PROBE_ID = "rimbisect.probe"
PROBE_FOLDER = "rimbisect-probe"
STEAM_POSTFIX = "_steam"


def _about(folder: Path) -> Path | None:
    for name in ("About.xml", "about.xml"):
        candidate = folder / "About" / name
        if candidate.is_file():
            return candidate
    return None


def _read(folder: Path, version: str, warnings: list[str], **kwargs) -> Mod | None:
    about = _about(folder)
    if about is None:
        warnings.append(f"skipped {folder}: no About/About.xml")
        return None
    try:
        mod = parse_about(about, version, **kwargs)
    except (OSError, etree.XMLSyntaxError) as exc:
        warnings.append(f"skipped {folder}: could not read About.xml ({exc})")
        return None
    if mod is None:
        warnings.append(f"skipped {folder}: About.xml has no packageId")
    return mod


def _subfolders(path: Path | None) -> list[Path]:
    if path is None or not path.is_dir():
        return []
    return sorted((p for p in path.iterdir() if p.is_dir()), key=lambda p: p.name.lower())


def scan_mods(game: Game, warnings: list[str]) -> dict[str, Mod]:
    """Every installed mod by lowercased packageId, the way the game indexes them.

    Local copies win over workshop copies of the same packageId; the game then gives the
    workshop copy a "_steam" postfix, and so do we.
    """
    version = game.short_version
    mods: dict[str, Mod] = {}
    for folder in _subfolders(game.data_dir):
        mod = _read(folder, version, warnings, official=True)
        if mod:
            mods[mod.package_id] = mod
    for folder in _subfolders(game.mods_dir):
        if folder.name == PROBE_FOLDER:
            continue
        mod = _read(folder, version, warnings)
        if mod is None:
            continue
        if mod.package_id in mods:
            warnings.append(f"{mod.package_id} is installed twice ({mods[mod.package_id].folder} and {folder}); "
                            "the game ignores the second copy and so does rimbisect")
            continue
        mods[mod.package_id] = mod
    for folder in _subfolders(game.workshop_dir):
        mod = _read(folder, version, warnings, workshop_id=folder.name)
        if mod is None:
            continue
        if mod.package_id in mods:
            local = mods[mod.package_id]
            warnings.append(f"{mod.package_id} is both a local mod ({local.folder}) and a workshop mod ({folder}); "
                            f"the local copy is {mod.package_id}, the workshop copy {mod.package_id}{STEAM_POSTFIX}")
            mod.package_id += STEAM_POSTFIX
            if mod.package_id in mods:
                continue
        mods[mod.package_id] = mod
    return mods


@dataclass
class ModsConfig:
    version: str
    active: list[str]  # as written in the file, original case
    known_expansions: list[str]

    def to_xml(self, active: list[str] | None = None) -> str:
        def items(values: list[str]) -> str:
            return "".join(f"    <li>{escape(v)}</li>\n" for v in values)

        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<ModsConfigData>\n"
            f"  <version>{escape(self.version)}</version>\n"
            f"  <activeMods>\n{items(self.active if active is None else active)}  </activeMods>\n"
            f"  <knownExpansions>\n{items(self.known_expansions)}  </knownExpansions>\n"
            "</ModsConfigData>\n"
        )


def read_mods_config(path: Path) -> ModsConfig:
    if not path.is_file():
        raise UserError(f"no ModsConfig.xml at {path}. Pass the right file with --config.")
    try:
        root = etree.parse(str(path), etree.XMLParser(recover=True)).getroot()
    except (OSError, etree.XMLSyntaxError) as exc:
        raise UserError(f"could not read {path}: {exc}") from exc
    if root is None or root.find("activeMods") is None:
        raise UserError(f"{path} has no <activeMods> list; is it a ModsConfig.xml?")

    def items(tag: str) -> list[str]:
        holder = root.find(tag)
        if holder is None:
            return []
        return [li.text.strip() for li in holder.findall("li") if li.text and li.text.strip()]

    return ModsConfig((root.findtext("version") or "").strip(), items("activeMods"), items("knownExpansions"))


def folder_mtime(folder: Path) -> float:
    """Newest modification time of anything in the folder. A folder's own mtime misses
    edits to files nested below it, which is where a workshop update lands."""
    newest = 0.0
    for current, _dirs, files in os.walk(folder):
        try:
            newest = max(newest, os.stat(current).st_mtime)
        except OSError:
            pass
        for name in files:
            try:
                newest = max(newest, os.stat(os.path.join(current, name)).st_mtime)
            except OSError:
                pass
    return newest


@dataclass
class LoadOrder:
    """The player's active mods, in order, restricted to what is installed."""

    config: ModsConfig
    mods: dict[str, Mod]
    order: list[str] = field(default_factory=list)  # lowercased ids, player's order
    spelling: dict[str, str] = field(default_factory=dict)  # lowercased id -> as written
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, config: ModsConfig, mods: dict[str, Mod], warnings: list[str]) -> LoadOrder:
        lo = cls(config, mods, warnings=warnings)
        for entry in config.active:
            key = entry.lower()
            if key == PROBE_ID or key in lo.spelling:
                continue
            if key not in mods:
                warnings.append(f"{entry} is in the active list but not installed; the game skips it and so does rimbisect")
                continue
            lo.order.append(key)
            lo.spelling[key] = entry
        if CORE not in lo.spelling:
            raise UserError("the active mod list does not include Core (ludeon.rimworld)")
        active = set(lo.order)
        for pid in lo.order:
            for dep in mods[pid].dependencies:
                if not ({dep.package_id, *dep.alternatives} & active):
                    warnings.append(f"{mods[pid].name} ({pid}) needs {dep.name or dep.package_id} ({dep.package_id}), "
                                    "which is not in the active list; rimbisect will not add it")
        return lo

    def closure(self, ids) -> set[str]:
        """ids plus their hard dependencies, transitively, taken from the active list only."""
        active = set(self.order)
        out = {i for i in ids if i in active}
        stack = list(out)
        while stack:
            for dep in self.mods[stack.pop()].dependencies:
                options = (dep.package_id, *dep.alternatives)
                if any(o in out for o in options):
                    continue
                chosen = next((o for o in options if o in active), None)
                if chosen is not None:
                    out.add(chosen)
                    stack.append(chosen)
        return out

    def dependents(self, ids) -> list[str]:
        """Active mods that stop loading if ids are removed, in load order."""
        gone = set(ids)
        changed = True
        while changed:
            changed = False
            remaining = set(self.order) - gone
            for pid in self.order:
                if pid in gone:
                    continue
                for dep in self.mods[pid].dependencies:
                    options = {dep.package_id, *dep.alternatives}
                    if options & set(self.order) and not options & remaining:
                        gone.add(pid)
                        changed = True
                        break
        return [pid for pid in self.order if pid in gone and pid not in set(ids)]

    def official(self) -> list[str]:
        return [pid for pid in self.order if self.mods[pid].official]

    def trial_list(self, ids) -> list[str]:
        """Player's order filtered to closure(ids), with the probe last."""
        keep = self.closure(ids)
        return [pid for pid in self.order if pid in keep] + [PROBE_ID]

    def written(self, ids: list[str]) -> list[str]:
        return [self.spelling.get(pid, pid) for pid in ids]

    def fixed_config(self, remove) -> str:
        gone = {pid.lower() for pid in remove}
        return self.config.to_xml([entry for entry in self.config.active if entry.lower() not in gone])
