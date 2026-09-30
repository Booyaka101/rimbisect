"""Find the RimWorld install, its Steam library and the player's Config folder."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import UserError

APP_ID = "294100"
EXE_NAME = "RimWorldWin64.exe"


@dataclass(frozen=True)
class Game:
    root: Path
    version: str  # full string from Version.txt, e.g. "1.6.4871 rev590"

    @property
    def exe(self) -> Path:
        return self.root / EXE_NAME

    @property
    def short_version(self) -> str:
        """"1.6", the key About.xml uses in <v1.6> blocks."""
        match = re.match(r"(\d+\.\d+)", self.version)
        return match.group(1) if match else self.version

    @property
    def mods_dir(self) -> Path:
        return self.root / "Mods"

    @property
    def data_dir(self) -> Path:
        return self.root / "Data"

    @property
    def workshop_dir(self) -> Path | None:
        """<library>/steamapps/workshop/content/294100 when the game sits in a Steam library."""
        if self.root.parent.name.lower() == "common" and self.root.parent.parent.name.lower() == "steamapps":
            return self.root.parent.parent / "workshop" / "content" / APP_ID
        return None


def parse_vdf(text: str) -> dict:
    """Valve's KeyValues text format: quoted keys, quoted values or nested { } blocks."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    stack: list[dict] = [{}]
    key: str | None = None
    for quoted, brace in tokens:
        if brace == "{":
            child: dict = {}
            stack[-1][key or ""] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = quoted
        else:
            stack[-1][key] = quoted.replace("\\\\", "\\")
            key = None
    return stack[0]


def steam_root() -> Path | None:
    try:
        import winreg
    except ImportError:
        winreg = None
    if winreg is not None:
        for hive, sub in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, sub) as handle:
                    for name in ("SteamPath", "InstallPath"):
                        try:
                            value, _ = winreg.QueryValueEx(handle, name)
                        except OSError:
                            continue
                        if value and Path(value).is_dir():
                            return Path(value)
            except OSError:
                continue
    default = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"
    return default if default.is_dir() else None


def library_with_app(vdf_text: str, app_id: str = APP_ID) -> list[Path]:
    """Library paths from libraryfolders.vdf, the ones listing app_id first."""
    folders = parse_vdf(vdf_text).get("libraryfolders", {})
    having, rest = [], []
    for entry in folders.values():
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        path = Path(entry["path"])
        (having if app_id in entry.get("apps", {}) else rest).append(path)
    return having + rest


def read_version(root: Path) -> str:
    try:
        return (root / "Version.txt").read_text(encoding="utf-8-sig").strip()
    except OSError:
        return "unknown"


def find_game(explicit: Path | None) -> Game:
    if explicit is not None:
        root = explicit.expanduser().resolve()
        if not (root / EXE_NAME).is_file():
            raise UserError(f"{root} does not contain {EXE_NAME}. Pass the RimWorld install folder with --game.")
        return Game(root, read_version(root))
    steam = steam_root()
    if steam is None:
        raise UserError("could not find Steam. Pass the RimWorld install folder with --game.")
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    try:
        libraries = library_with_app(vdf.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        libraries = [steam]
    for library in libraries:
        root = library / "steamapps" / "common" / "RimWorld"
        if (root / EXE_NAME).is_file():
            return Game(root, read_version(root))
    raise UserError(f"RimWorld (app {APP_ID}) is not in any Steam library listed in {vdf}. "
                    "Pass the install folder with --game.")


def default_config_dir() -> Path:
    base = Path(os.environ.get("USERPROFILE", str(Path.home())))
    return base / "AppData" / "LocalLow" / "Ludeon Studios" / "RimWorld by Ludeon Studios" / "Config"


def process_running(image: str) -> bool | None:
    """Whether a process with this image name runs; None when tasklist is unavailable."""
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return image.lower() in out.lower()
