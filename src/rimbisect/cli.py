"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import __version__, report
from .errors import UserError
from .install import EXE_NAME, Game, default_config_dir, find_game, process_running
from .modlist import PROBE_FOLDER, LoadOrder, ModsConfig, folder_mtime, read_mods_config, scan_mods
from .search import Search
from .signature import Criterion, ErrorGroup, group_errors
from .trial import CRASH, FAIL, PASS, GameLauncher, Trial, install_probe, remove_probe


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass
class Setup:
    game: Game
    config_path: Path
    config: ModsConfig
    order: LoadOrder
    workdir: Path
    warnings: list[str]


def load(args) -> Setup:
    game = find_game(args.game)
    config_path = (args.config or default_config_dir() / "ModsConfig.xml").expanduser().resolve()
    config = read_mods_config(config_path)
    warnings: list[str] = []
    mods = scan_mods(game, warnings)
    order = LoadOrder.build(config, mods, warnings)
    workdir = (args.workdir or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "rimbisect").resolve()
    return Setup(game, config_path, config, order, workdir, warnings)


def _preflight(setup: Setup) -> None:
    if process_running(EXE_NAME):
        raise UserError("RimWorld is already running. Close it first; rimbisect starts and stops the game itself.")
    if process_running("steam.exe") is False:
        setup.warnings.append("Steam was not running, so the game could not see workshop mods")
        log("warning: Steam is not running")


@contextmanager
def _session(game: Game, launcher: GameLauncher):
    """The probe is in the game's Mods folder for the duration; the copied Config goes afterwards."""
    try:
        install_probe(game)
        yield
    finally:
        remove_probe(game)
        shutil.rmtree(launcher.savedata, ignore_errors=True)
        if (game.mods_dir / PROBE_FOLDER).exists():
            log(f"warning: could not delete {game.mods_dir / PROBE_FOLDER}; delete it before you play")


def _new_run(setup: Setup, args) -> tuple[Path, GameLauncher]:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = setup.workdir / "runs" / stamp
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = setup.workdir / "runs" / f"{stamp}-{suffix}"
    run_dir.mkdir(parents=True)
    launcher = GameLauncher(setup.game, setup.config, run_dir, default_config_dir(),
                            settle=args.settle, timeout=args.timeout * 60, log=log)
    return run_dir, launcher


def _criterion(args) -> Criterion | None:
    if args.match:
        try:
            return Criterion(pattern=re.compile(args.match))
        except re.error as exc:
            raise UserError(f"--match is not a valid regular expression: {exc}") from exc
    if args.slower_than is not None:
        return Criterion(slower_than=args.slower_than)
    return None


NEED_PICK = "pick one of the errors above with --pick N, or pass --match REGEX"


def _pick(groups: list[ErrorGroup], args) -> ErrorGroup:
    shown = groups[:30]
    log("\nErrors logged by the full mod list, most frequent first:\n")
    for i, group in enumerate(shown, 1):
        log(f"  {i:>3}. {group.count:>5}x  {group.headline[:140]}")
    if len(groups) > len(shown):
        log(f"       ... and {len(groups) - len(shown)} rarer ones")
    if args.pick is not None:
        choice = args.pick
    elif sys.stdin.isatty():
        try:
            answer = input(f"\nWhich error should rimbisect hunt? [1-{len(shown)}] ").strip()
        except EOFError:
            raise UserError(NEED_PICK) from None
        choice = int(answer) if answer.isdigit() else 0
    else:
        raise UserError(NEED_PICK)
    if not 1 <= choice <= len(groups):
        raise UserError(f"pick a number between 1 and {len(groups)}")
    return groups[choice - 1]


def _keep(setup: Setup, extra: list[str]) -> set[str]:
    keep = set(setup.order.official())
    for entry in extra:
        for pid in filter(None, (p.strip().lower() for p in entry.split(","))):
            if pid in setup.order.spelling:
                keep.add(pid)
            else:
                setup.warnings.append(f"--keep {pid}: not in the active mod list, ignored")
    return keep


def _last_good_path(setup: Setup) -> Path:
    return setup.workdir / "last-good.json"


def _changed(setup: Setup, candidates: list[str], args) -> list[str] | None:
    """Mods modified since --since or since the last passing `rimbisect check`."""
    mods = setup.order.mods
    if args.since:
        try:
            since = datetime.fromisoformat(args.since).timestamp()
        except ValueError as exc:
            raise UserError(f"--since takes a date like 2026-09-01 or 2026-09-01T18:30, not {args.since!r}") from exc
        changed = [p for p in candidates if folder_mtime(mods[p].folder) > since]
        source = f"since {args.since}"
    else:
        path = _last_good_path(setup)
        if args.ignore_last_good or not path.is_file():
            return None
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))["mods"]
            if not all(isinstance(t, (int, float)) for t in saved.values()):
                raise ValueError("folder times are not numbers")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            setup.warnings.append(f"ignored {path}: {exc}")
            return None
        # Folder times from a copy or restore can shift by a second or two.
        changed = [p for p in candidates if p not in saved or folder_mtime(mods[p].folder) > saved[p] + 2]
        source = "since the last good run"
    log(f"{len(changed)} of {len(candidates)} mods changed {source}")
    if not changed or len(changed) == len(candidates):
        return None
    return changed


def _save_last_good(setup: Setup) -> Path:
    path = _last_good_path(setup)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "saved": datetime.now().isoformat(timespec="seconds"),
        "modsConfig": str(setup.config_path),
        "mods": {pid: folder_mtime(setup.order.mods[pid].folder) for pid in setup.order.order},
    }
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return path


def _baseline(launcher: GameLauncher, setup: Setup, criterion: Criterion | None, args) -> Trial:
    mods = setup.order.trial_list(setup.order.order)
    for attempt in range(max(1, args.repeats)):
        trial = launcher.run(mods, "baseline" if attempt == 0 else "baseline again", criterion)
        log(trial.line())
        if trial.outcome == FAIL or criterion is None:
            break
    return trial


def _hunted(baseline: Trial, criterion: Criterion | None, args) -> Criterion | None:
    """The criterion to search with, or None when the baseline did not reproduce anything."""
    if criterion is not None:
        reproduced = baseline.outcome == FAIL or (args.crash_is_fail and baseline.outcome == CRASH)
        return criterion if reproduced else None
    if args.crash_is_fail and args.pick is None:
        return Criterion() if baseline.outcome == CRASH else None
    groups = group_errors(baseline.errors)
    if not groups:
        return None
    chosen = _pick(groups, args)
    baseline.outcome, baseline.excerpt = FAIL, chosen.example
    return Criterion(signature=chosen.signature, label=chosen.headline)


def _emit(data: dict, run_dir: Path, as_json: bool) -> None:
    text = report.write(data, run_dir)
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(text, end="")


def cmd_bisect(args) -> int:
    setup = load(args)
    _preflight(setup)
    keep = _keep(setup, args.keep)
    candidates = [pid for pid in setup.order.order if pid not in keep]
    if not candidates:
        raise UserError("every active mod is kept (Core, DLCs, --keep); there is nothing to search")
    criterion = _criterion(args)
    log(f"rimbisect {__version__}: {len(setup.order.order)} active mods, {len(candidates)} to search")
    changed = _changed(setup, candidates, args)
    run_dir, launcher = _new_run(setup, args)
    log(f"run folder {run_dir}")

    fields = dict(run_dir=run_dir, game=setup.game, config_path=setup.config_path, order=setup.order,
                  searched=len(candidates), warnings=setup.warnings, trials=launcher.trials,
                  crash_is_fail=args.crash_is_fail)
    search: Search | None = None
    with _session(setup.game, launcher):
        try:
            baseline = _baseline(launcher, setup, criterion, args)
            hunted = _hunted(baseline, criterion, args)
            if hunted is None:
                what = "the error" if criterion else "a crash" if args.crash_is_fail and args.pick is None else "any error"
                setup.warnings.append(f"the full mod list did not reproduce {what} "
                                      f"(baseline outcome {baseline.outcome})")
                _emit(report.build(status=report.NOT_REPRODUCED, criterion=criterion, **fields), run_dir, args.json)
                return 1
            criterion = hunted
            steps = math.ceil(math.log2(len(candidates))) if len(candidates) > 1 else 0
            # Offsets measured with acceptance/simulate.py at 64, 200 and 400 mods.
            log(f"the full list took {baseline.duration:.0f}s; expect about {steps + 2} more trials "
                f"for a single culprit, {2 * steps + 3} for two mods that only fail together")
            search = Search(setup.order, launcher, criterion, keep, repeats=args.repeats,
                            crash_is_fail=args.crash_is_fail, log=log)
            search.record(candidates, True)
            culprits = search.locate(candidates, changed)
        except KeyboardInterrupt:
            log("interrupted; the game has been closed")
            _emit(report.build(status=report.INTERRUPTED, criterion=criterion, changed=changed,
                               flaky=search.flaky if search else None, **fields), run_dir, args.json)
            return 130
    status = report.FOUND if culprits else report.BASE_GAME_FAILS
    _emit(report.build(status=status, criterion=criterion, culprits=culprits, flaky=search.flaky,
                       changed=changed, **fields), run_dir, args.json)
    return 0 if culprits else 1


def cmd_check(args) -> int:
    setup = load(args)
    _preflight(setup)
    criterion = _criterion(args)
    run_dir, launcher = _new_run(setup, args)
    log(f"rimbisect {__version__}: one trial with all {len(setup.order.order)} active mods")
    with _session(setup.game, launcher):
        try:
            trial = launcher.run(setup.order.trial_list(setup.order.order), "check", criterion)
        except KeyboardInterrupt:
            log("interrupted; the game has been closed")
            return 130
    log(trial.line())
    passed = trial.outcome == PASS
    if passed:
        log(f"saved this mod list as the last good one: {_save_last_good(setup)}")
    status = report.CHECK_PASSED if passed else report.CHECK_FAILED
    _emit(report.build(status=status, run_dir=run_dir, game=setup.game, config_path=setup.config_path,
                       order=setup.order, criterion=criterion, trials=launcher.trials,
                       errors=group_errors(trial.errors), warnings=setup.warnings), run_dir, args.json)
    return 0 if passed else 1


def cmd_mods(args) -> int:
    setup = load(args)
    order = setup.order
    rows = []
    for pid in order.order:
        mod = order.mods[pid]
        if mod.official:
            source = "official"
        else:
            source = "workshop" if setup.game.workshop_dir and mod.folder.parent == setup.game.workshop_dir else "local"
        rows.append({"packageId": order.spelling[pid], "name": mod.name, "source": source,
                     "workshopId": mod.workshop_id, "folder": str(mod.folder),
                     "dependencies": [d.package_id for d in mod.dependencies]})
    if args.json:
        print(json.dumps({"game": {"folder": str(setup.game.root), "version": setup.game.version},
                          "modsConfig": str(setup.config_path), "mods": rows, "warnings": setup.warnings},
                         indent=2, ensure_ascii=False))
        return 0
    print(f"{setup.game.root} ({setup.game.version})")
    print(f"{setup.config_path}: {len(rows)} active mods\n")
    for i, row in enumerate(rows, 1):
        print(f"{i:>4}  {row['packageId']:<45} {row['source']:<9} {row['workshopId'] or '-':<11} {row['name']}")
    if setup.warnings:
        print(f"\n{len(setup.warnings)} warning(s):")
        for w in setup.warnings:
            print(f"  - {w}")
    return 0


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--game", type=Path, help="RimWorld install folder (default: found through Steam)")
    common.add_argument("--config", type=Path,
                        help="ModsConfig.xml to test (default: the game's own). Only read, never written.")
    common.add_argument("--workdir", type=Path,
                        help=r"where runs and last-good.json go (default: %%LOCALAPPDATA%%\rimbisect)")
    common.add_argument("--json", action="store_true", help="print the report as JSON")

    trial_opts = argparse.ArgumentParser(add_help=False)
    what = trial_opts.add_mutually_exclusive_group()
    what.add_argument("--match", metavar="REGEX", help="a trial fails when an error or log line matches REGEX")
    what.add_argument("--slower-than", type=float, metavar="SECONDS",
                      help="a trial fails when the map takes longer than SECONDS to be ready")
    trial_opts.add_argument("--settle", type=float, default=20, metavar="SECONDS",
                            help="how long to keep the map running before quitting (default 20)")
    trial_opts.add_argument("--timeout", type=float, default=20, metavar="MINUTES",
                            help="give up on a trial after this long (default 20)")

    parser = argparse.ArgumentParser(
        prog="rimbisect", description="Find the RimWorld mod, or combination of mods, behind an error.")
    parser.add_argument("--version", action="version", version=f"rimbisect {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    bisect = sub.add_parser("bisect", parents=[common, trial_opts],
                            help="run the game on halves of your mod list until the culprit is found")
    bisect.add_argument("--pick", type=int, metavar="N", help="hunt error N from the baseline's list without asking")
    bisect.add_argument("--keep", action="append", default=[], metavar="ID[,ID]",
                        help="packageIds to load in every trial (Core and DLCs always are)")
    bisect.add_argument("--since", metavar="DATE", help="first try the list without mods changed after DATE")
    bisect.add_argument("--ignore-last-good", action="store_true",
                        help="do not try the mods changed since the last good run first")
    bisect.add_argument("--repeats", type=int, default=1, metavar="N",
                        help="run a passing list up to N times before trusting it, for errors that come and go")
    bisect.add_argument("--crash-is-fail", action="store_true",
                        help="count a crash or unexpected exit as reproducing the problem")
    bisect.set_defaults(func=cmd_bisect)

    check = sub.add_parser("check", parents=[common, trial_opts],
                           help="run the full list once and, if it gets through, save it as the last good one")
    check.set_defaults(func=cmd_check)

    mods = sub.add_parser("mods", parents=[common], help="show the active mod list as rimbisect reads it")
    mods.set_defaults(func=cmd_mods)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Redirected output would otherwise use the ANSI code page and choke on mod names.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except UserError as exc:
        print(f"rimbisect: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("rimbisect: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
