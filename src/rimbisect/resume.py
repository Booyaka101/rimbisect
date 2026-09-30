"""Keep a bisect run on disk, so a stopped run can go on where it was."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .errors import UserError
from .modlist import folder_mtime
from .signature import Criterion
from .trial import UNRESOLVED, Launcher, Trial

RUN_FILE = "run.json"
TRIALS_FILE = "trials.jsonl"
PATH_OPTIONS = ("game", "config", "workdir")


class Journal:
    """Records every finished trial in the run folder. On a resumed run it answers from
    those records, in the order they ran, until the search asks for a mod list it did not
    ask for before; from there on the trials run for real."""

    def __init__(self, launcher: Launcher, run_dir: Path, log: Callable[[str], None]):
        self.launcher = launcher
        self.path = run_dir / TRIALS_FILE
        self.log = log
        recorded = read_trials(self.path)
        # A trial that gave no answer gets another chance.
        self.pending = [t for t in recorded if t.outcome != UNRESOLVED]
        launcher.numbered = max((t.number for t in recorded), default=0)
        if self.pending:
            log(f"{len(self.pending)} trials of this run are recorded and are not run again")

    def run(self, mods: list[str], label: str, criterion: Criterion | None) -> Trial:
        if self.pending and self.pending[0].mods == mods:
            trial = self.pending.pop(0)
            if self.launcher.not_loaded_at_first is None:
                self.launcher.not_loaded_at_first = set(trial.not_loaded)
            self.launcher.trials.append(trial)
            return trial
        if self.pending:
            self.log(f"the search went another way than before; the {len(self.pending)} "
                     "recorded trials left are not used")
            self.pending.clear()
        trial = self.launcher.run(mods, label, criterion)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(trial), ensure_ascii=False) + "\n")
        return trial


def read_trials(path: Path) -> list[Trial]:
    trials = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    for line in lines:
        try:
            data = json.loads(line)
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
    (run_dir / RUN_FILE).write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


def load(run_dir: Path) -> dict:
    path = run_dir / RUN_FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state.get("args"), dict) or not isinstance(state.get("mods"), dict):
            raise ValueError("args or mods missing")
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
    if state["game"] != setup.game.version:
        raise UserError(f"the game was updated from {state['game']} to {setup.game.version} since this run "
                        "started. Start a new one with `rimbisect bisect`.")
    saved, order = state["mods"], setup.order
    if list(saved) != order.order:
        raise UserError("the active mod list changed since this run started. Start a new one with `rimbisect bisect`.")
    # Folder times from a copy or restore can shift by a second or two.
    updated = [pid for pid in order.order if folder_mtime(order.mods[pid].folder) > saved[pid] + 2]
    if updated:
        names = ", ".join(order.written(updated[:5]))
        setup.warnings.append(f"{len(updated)} mods have files newer than the start of this run ({names}). "
                              "Mods that write logs into their own folder do that; if one was updated "
                              "instead, the trials run before may no longer hold and a new run is safer")
        log(f"warning: {setup.warnings[-1]}")


def latest_unfinished(runs: Path) -> Path | None:
    if not runs.is_dir():
        return None
    for run_dir in sorted((p for p in runs.iterdir() if (p / RUN_FILE).is_file()), reverse=True):
        try:
            if not json.loads((run_dir / RUN_FILE).read_text(encoding="utf-8")).get("finished"):
                return run_dir
        except (OSError, ValueError, AttributeError):
            continue
    return None
