"""Keep a bisect run on disk, so a stopped run can go on where it was."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from . import __version__
from .errors import UserError
from .modlist import folder_mtime
from .signature import Criterion
from .trial import UNRESOLVED, Launcher, Trial

RUN_FILE = "run.json"
TRIALS_FILE = "trials.jsonl"
PATH_OPTIONS = ("game", "config", "workdir")


class StaleRun(UserError):
    """The game or the mod list changed, so the recorded trials no longer answer for it."""


class Journal:
    """Records every finished trial in the run folder. On a resumed run it answers from
    those records, in the order they ran, until the search asks for a mod list it did not
    ask for before; from there on the trials run for real."""

    def __init__(self, launcher: Launcher, run_dir: Path, log: Callable[[str], None]):
        self.launcher = launcher
        self.run_dir = run_dir
        self.path = run_dir / TRIALS_FILE
        self.log = log
        recorded = read_trials(self.path)
        # Trials that gave no answer just before the run stopped get another chance.
        while recorded and recorded[-1].outcome == UNRESOLVED:
            recorded.pop()
        self.pending = recorded
        self.replayed: list[Trial] = []
        # Also drops a line cut short when the run was killed while writing it.
        self._rewrite(recorded)
        launcher.numbered = max((t.number for t in recorded), default=0)
        # What the game could not load in the first trial that reported it, which is normal for this list.
        not_loaded = _read(run_dir).get("notLoadedAtFirst")
        self.not_loaded_saved = not_loaded is not None
        if self.not_loaded_saved:
            launcher.not_loaded_at_first = set(not_loaded)
        if recorded:
            log(f"{len(recorded)} trials of this run are recorded and are not run again")

    def run(self, mods: list[str], label: str, criterion: Criterion | None) -> Trial:
        if self.pending and self.pending[0].mods == mods:
            trial = self.pending.pop(0)
            self.replayed.append(trial)
            self.launcher.trials.append(trial)
            return trial
        if self.pending:
            self.log(f"the search went another way than before; the {len(self.pending)} "
                     "recorded trials left are not used")
            self.pending.clear()
            self._rewrite(self.replayed)
        trial = self.launcher.run(mods, label, criterion)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(_line(trial))
        if not self.not_loaded_saved and self.launcher.not_loaded_at_first is not None:
            update(self.run_dir, notLoadedAtFirst=sorted(self.launcher.not_loaded_at_first))
            self.not_loaded_saved = True
        return trial

    def _rewrite(self, trials: list[Trial]) -> None:
        _replace(self.path, "".join(_line(t) for t in trials))


def _line(trial: Trial) -> str:
    return json.dumps(asdict(trial), ensure_ascii=False) + "\n"


def _replace(path: Path, text: str) -> None:
    """Write through a temporary file, so a kill mid-write leaves the old file whole."""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def read_trials(path: Path) -> list[Trial]:
    trials = []
    try:
        lines = path.read_bytes().split(b"\n")
    except FileNotFoundError:
        return []
    for line in lines:
        try:
            data = json.loads(line.decode("utf-8"))
            data["errors"] = [(text, repeats) for text, repeats in data["errors"]]
            trials.append(Trial(**data))
        except (ValueError, TypeError, KeyError):
            # The last line of a run that was killed while writing it.
            continue
    return trials


def start(run_dir: Path, args, setup, changed: list[str] | None, changed_since: str | None) -> None:
    order = setup.order
    state = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "version": __version__,
        "args": saved_args(args, setup.workdir),
        "game": setup.game.version,
        "modsConfig": str(setup.config_path),
        "mods": {pid: folder_mtime(order.mods[pid].folder) for pid in order.order},
        "changed": changed,
        "changedSince": changed_since,
        "finished": False,
    }
    _write(run_dir, state)


def update(run_dir: Path, **fields) -> None:
    state = load(run_dir)
    state.update(fields)
    _write(run_dir, state)


def _write(run_dir: Path, state: dict) -> None:
    _replace(run_dir / RUN_FILE, json.dumps(state, indent=1, ensure_ascii=False))


def _read(run_dir: Path) -> dict:
    """run.json as it is, or {} when it is missing or unreadable."""
    try:
        state = json.loads((run_dir / RUN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def load(run_dir: Path) -> dict:
    path = run_dir / RUN_FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(state, dict) or not isinstance(state.get("args"), dict)
                or not isinstance(state.get("mods"), dict)
                or not {"game", "changed", "changedSince"} <= state.keys()):
            raise ValueError("parts of the run are missing")
    except FileNotFoundError:
        raise UserError(f"{run_dir} is not a bisect run rimbisect can resume (no {RUN_FILE})") from None
    except ValueError as exc:
        raise UserError(f"could not read {path}: {exc}") from None
    return state


def saved_args(args, workdir: Path) -> dict:
    saved = {}
    for key, value in vars(args).items():
        if key in ("func", "command"):
            continue
        if key in PATH_OPTIONS and value is not None:
            value = str(workdir if key == "workdir" else Path(value).expanduser().resolve())
        saved[key] = value
    return saved


def restored_args(state: dict, defaults) -> None:
    """Put the saved options of a run on top of the defaults of a fresh `bisect`."""
    for key, value in state["args"].items():
        setattr(defaults, key, Path(value) if key in PATH_OPTIONS and value is not None else value)


def check_unchanged(state: dict, setup, log: Callable[[str], None]) -> None:
    """Refuse to resume when the trials already run no longer answer for the mod list, and
    warn about mods with newer files. Some mods write logs into their own folder every time
    the game starts, so newer files alone do not mean an update."""
    # Another version may group errors differently and so decide trials differently.
    # 0.2.0 did not record its version.
    if state.get("version", "0.2.0") != __version__:
        raise StaleRun(f"rimbisect was updated from {state.get('version', '0.2.0')} to {__version__} since this "
                       "run started")
    if state["game"] != setup.game.version:
        raise StaleRun(f"the game was updated from {state['game']} to {setup.game.version} since this run "
                       "started")
    saved, order = state["mods"], setup.order
    if list(saved) != order.order:
        raise StaleRun("the active mod list changed since this run started")
    # Folder times from a copy or restore can shift by a second or two.
    updated = [pid for pid in order.order if folder_mtime(order.mods[pid].folder) > saved[pid] + 2]
    if updated:
        names = ", ".join(order.written(updated[:5]))
        setup.warnings.append(f"{len(updated)} mods have files newer than the start of this run ({names}). "
                              "Mods that write logs into their own folder do that; if one was updated "
                              "instead, the trials run before may no longer hold and a new run is safer")
        log(f"warning: {setup.warnings[-1]}")


def progress(run_dir: Path) -> str:
    started = str(_read(run_dir).get("started", "?")).replace("T", " ")[:16]
    return f"started {started}, {len(read_trials(run_dir / TRIALS_FILE))} trials done"


def latest_unfinished(runs: Path) -> Path | None:
    """The newest run, if it did not finish. A run that finished after it makes an older
    unfinished one moot."""
    if not runs.is_dir():
        return None
    for run_dir in sorted((p for p in runs.iterdir() if (p / RUN_FILE).is_file()), reverse=True):
        state = _read(run_dir)
        if state:
            return None if state.get("finished") else run_dir
    return None
