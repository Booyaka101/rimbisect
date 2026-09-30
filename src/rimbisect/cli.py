"""Command line entry point."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import __version__, report, resume
from .errors import UserError
from .install import EXE_NAME, Game, default_config_dir, find_game, process_running
from .modlist import PROBE_FOLDER, LoadOrder, ModsConfig, folder_mtime, read_mods_config, scan_mods
from .search import Inconclusive, Search, run_answered
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
    if args.config and not config_path.is_file():
        raise UserError(f"--config {args.config}: no such file")
    config = read_mods_config(config_path)
    warnings: list[str] = []
    mods = scan_mods(game, warnings)
    order = LoadOrder.build(config, mods, warnings)
    workdir = _workdir(args)
    return Setup(game, config_path, config, order, workdir, warnings)


def _workdir(args) -> Path:
    workdir = getattr(args, "workdir", None) or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "rimbisect"
    return workdir.expanduser().resolve()


def _preflight(setup: Setup) -> None:
    if process_running(EXE_NAME):
        raise UserError("RimWorld is already running. Close it first; rimbisect starts and stops the game itself.")
    workshop = setup.game.workshop_dir
    if workshop and process_running("steam.exe") is False:
        count = sum(setup.order.mods[pid].folder.parent == workshop for pid in setup.order.order)
        if count:
            raise UserError(f"Steam is not running, and without it the game does not load your {count} "
                            "Workshop mods. Start Steam and run rimbisect again.")
    _claim(setup.game)


_claimed: set[str] = set()


def _lock_name(game: Game) -> str:
    return "Local\\rimbisect-" + hashlib.sha1(str(game.root).lower().encode("utf-8")).hexdigest()[:16]


def _claim(game: Game) -> None:
    """Hold a lock on the game until rimbisect exits: two runs on one game would delete
    each other's probe and fight over the game."""
    name = _lock_name(game)
    if os.name != "nt" or name in _claimed:
        return
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, name)
    if handle and ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        raise UserError("another rimbisect is already running on this game. Let it finish or close it first.")
    _claimed.add(name)


@contextmanager
def _on_console_close(action):
    """Run action when the console window is closed. Windows then ends the process without
    running finally blocks, and waits at most five seconds for this."""
    if os.name != "nt":
        yield
        return
    import ctypes

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint32)
    def handler(event):
        if event in (2, 5, 6):  # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT
            action()
        return False

    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleCtrlHandler(handler, True)
    try:
        yield
    finally:
        kernel32.SetConsoleCtrlHandler(handler, False)


@contextmanager
def _session(game: Game, launcher: GameLauncher):
    """The probe is in the game's Mods folder for the duration; the copied Config goes afterwards."""
    def clean_up():
        remove_probe(game)
        shutil.rmtree(launcher.savedata, ignore_errors=True)

    def closed():
        launcher.stop()
        clean_up()

    with _on_console_close(closed):
        try:
            install_probe(game)
            yield
        finally:
            clean_up()
            if (game.mods_dir / PROBE_FOLDER).exists():
                log(f"warning: could not delete {game.mods_dir / PROBE_FOLDER}; delete it before you play")


def _new_run(setup: Setup) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = setup.workdir / "runs" / stamp
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = setup.workdir / "runs" / f"{stamp}-{suffix}"
    if "=" in str(run_dir):
        raise UserError(f"the run folder {run_dir} contains '=', which RimWorld's -savedatafolder= "
                        "cannot take. Pick another with --workdir.")
    try:
        run_dir.mkdir(parents=True)
    except OSError as exc:
        raise UserError(f"could not create the run folder {run_dir}: {exc.strerror or exc}. "
                        "Pick another with --workdir.") from exc
    return run_dir


def _save_file(args) -> Path | None:
    """The save --save names, a file or a name from the game's Saves folder. The resolved
    path goes back into args, which a resumed run reads."""
    if args.save is None:
        return None
    saves = default_config_dir().parent / "Saves"
    for path in (Path(args.save).expanduser(), saves / args.save, saves / f"{args.save}.rws"):
        if path.is_file():
            args.save = str(path.resolve())
            return path.resolve()
    found = sorted(saves.glob("*.rws"), key=lambda p: -p.stat().st_mtime) if saves.is_dir() else []
    names = ", ".join(p.stem for p in found[:10]) or "none"
    raise UserError(f"--save {args.save}: no such save. The newest in {saves}: {names}")


def _copy_save(save: Path | None, run_dir: Path) -> None:
    """Into the run folder, where a resumed run finds it again."""
    if save is not None:
        try:
            shutil.copyfile(save, run_dir / SAVE_COPY)
        except OSError as exc:
            raise UserError(f"could not copy {save} to the run folder: {exc}") from exc


def _launcher(setup: Setup, args, run_dir: Path) -> GameLauncher:
    return GameLauncher(setup.game, setup.config, run_dir, default_config_dir(),
                        settle=args.settle, timeout=args.timeout * 60, log=log,
                        save=run_dir / SAVE_COPY if args.save else None)


def _finish(run_dir: Path, status: str) -> None:
    """An inconclusive run can be tried again with other options, so it keeps its save."""
    resume.update(run_dir, finished=True, status=status)
    if status != report.INCONCLUSIVE:
        (run_dir / SAVE_COPY).unlink(missing_ok=True)


def _criterion(args) -> Criterion | None:
    if args.match_text == "" or args.match == "":
        raise UserError("--match and --match-text need some text; an empty one matches every line")
    if args.match_text is not None:
        return Criterion(pattern=re.compile(re.escape(args.match_text)), label=args.match_text)
    if args.match is not None:
        try:
            return Criterion(pattern=re.compile(args.match))
        except re.error as exc:
            raise UserError(f"--match is not a valid regular expression: {exc}; "
                            "use --match-text to match the text as it is") from exc
    if args.slower_than is not None:
        return Criterion(slower_than=args.slower_than)
    return None


SAVE_COPY = "save.rws"
NEED_PICK = "pick one of the errors above with --pick N, or pass --match-text with a piece of it"


def _show(numbered: list[tuple[int, ErrorGroup]]) -> None:
    for i, group in numbered[:30]:
        log(f"  {i:>4}. {group.count:>5}x  {group.headline[:140]}")
    if len(numbered) > 30:
        log(f"        ... and {len(numbered) - 30} more; type a piece of an error's text to find it")


def _pick(groups: list[ErrorGroup], args) -> ErrorGroup:
    """groups in the order shown: exceptions first, the rest most frequent first."""
    log("\nErrors logged by the full mod list, exceptions first:\n")
    _show(list(enumerate(groups, 1)))
    if args.pick is not None:
        if not 1 <= args.pick <= len(groups):
            raise UserError(f"--pick {args.pick}: pick a number between 1 and {len(groups)}")
        return groups[args.pick - 1]
    if not sys.stdin.isatty():
        raise UserError(NEED_PICK)
    while True:
        try:
            answer = input("\nWhich error should rimbisect hunt? A number, or a piece of its text: ").strip()
        except EOFError:
            raise UserError(NEED_PICK) from None
        if answer.isdigit() and 1 <= int(answer) <= len(groups):
            return groups[int(answer) - 1]
        found = [(i, g) for i, g in enumerate(groups, 1) if answer and answer.lower() in g.example.lower()]
        if len(found) == 1:
            log(f"  {found[0][0]:>4}. {found[0][1].headline[:140]}")
            return found[0][1]
        if found:
            log(f"\n{len(found)} errors contain that:\n")
            _show(found)
        elif answer:
            log("no error contains that")


def _keep(setup: Setup, extra: list[str]) -> set[str]:
    keep = set(setup.order.official())
    for entry in extra:
        for pid in filter(None, (p.strip().lower() for p in entry.split(","))):
            if pid not in setup.order.spelling:
                raise UserError(f"--keep {pid}: not in the active mod list. `rimbisect mods` lists the packageIds.")
            keep.add(pid)
    return keep


def _last_good_path(setup: Setup) -> Path:
    return setup.workdir / "last-good.json"


def _since(args) -> float | None:
    if args.since is None:
        return None
    try:
        return datetime.fromisoformat(args.since).timestamp()
    except ValueError as exc:
        raise UserError(f"--since takes a date like 2026-09-01 or 2026-09-01T18:30, not {args.since!r}") from exc


def _changed(setup: Setup, candidates: list[str], args) -> tuple[list[str] | None, str | None]:
    """Mods modified since --since or since the last passing `rimbisect check`, and which of the two."""
    mods = setup.order.mods
    since = _since(args)
    if since is not None:
        changed = [p for p in candidates if folder_mtime(mods[p].folder) > since]
        source = f"since {args.since}"
    else:
        path = _last_good_path(setup)
        if args.ignore_last_good or not path.is_file():
            return None, None
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))["mods"]
            if not all(isinstance(t, (int, float)) for t in saved.values()):
                raise ValueError("folder times are not numbers")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            setup.warnings.append(f"ignored {path}: {exc}")
            return None, None
        # Folder times from a copy or restore can shift by a second or two.
        changed = [p for p in candidates if p not in saved or folder_mtime(mods[p].folder) > saved[p] + 2]
        source = "since the last good run"
    log(f"{len(changed)} of {len(candidates)} mods changed {source}")
    if not changed or len(changed) == len(candidates):
        return None, None
    return changed, source


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
    """The full list; with --repeats, up to that many times until it shows the error."""
    mods = setup.order.trial_list(setup.order.order)
    trial = run_answered(launcher, mods, "baseline", criterion, args.crash_is_fail, log)
    if criterion is None:
        return trial
    log(trial.line())
    for _ in range(args.repeats - 1):
        if _reproduced(trial, args):
            break
        trial = run_answered(launcher, mods, "baseline again", criterion, args.crash_is_fail, log)
        log(trial.line())
    return trial


def _warn_not_loaded(trial: Trial, setup: Setup) -> None:
    if trial.not_loaded:
        names = ", ".join(setup.order.written(trial.not_loaded[:10]))
        setup.warnings.append(f"the game did not load {len(trial.not_loaded)} of the active mods even with the "
                              f"full list ({names}), so they are not tested")


def _reproduced(trial: Trial, args) -> bool:
    return trial.outcome == FAIL or (args.crash_is_fail and trial.outcome == CRASH)


def _hunted(baseline: Trial, criterion: Criterion | None, args) -> Criterion | None:
    """The criterion to search with, or None when the baseline did not reproduce anything."""
    if criterion is not None:
        return criterion if _reproduced(baseline, args) else None
    if args.crash_is_fail and args.pick is None:
        return Criterion() if baseline.outcome == CRASH else None
    groups = group_errors(baseline.errors)
    if not groups:
        return None
    chosen = _pick(groups, args)
    args.pick = groups.index(chosen) + 1
    baseline.outcome, baseline.excerpt = FAIL, chosen.example
    return Criterion(signature=chosen.signature, label=chosen.headline)


def _emit(data: dict, run_dir: Path, as_json: bool) -> None:
    text = report.write(data, run_dir)
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(text, end="")
        log(f"\nThe report is saved in {run_dir / 'report.txt'}")


def cmd_bisect(args, resumed: tuple[Path, dict] | None = None) -> int:
    setup = load(args)
    if resumed:
        resume.check_unchanged(resumed[1], setup, log)
    _preflight(setup)
    keep = _keep(setup, args.keep)
    kept = setup.order.closure(keep)
    candidates = [pid for pid in setup.order.order if pid not in kept]
    if not candidates:
        raise UserError("every active mod is kept (Core, DLCs, --keep and what they need); there is nothing to search")
    criterion = _criterion(args)
    if args.pick is not None and criterion is not None:
        raise UserError("--pick chooses from the errors of the full list; leave it out with --match, "
                        "--match-text or --slower-than")
    _since(args)
    save = None if resumed else _save_file(args)
    log(f"rimbisect {__version__}: {len(setup.order.order)} active mods, {len(candidates)} to search")
    if resumed is None:
        changed, changed_since = _changed(setup, candidates, args)
        run_dir = _new_run(setup)
        _copy_save(save, run_dir)
        resume.start(run_dir, args, setup, changed, changed_since)
    else:
        run_dir, state = resumed
        changed, changed_since = state["changed"], state["changedSince"]
        if args.save and not (run_dir / SAVE_COPY).is_file():
            raise resume.StaleRun(f"the copy of the save in {run_dir} is gone")
    log(f"run folder {run_dir}")
    launcher = _launcher(setup, args, run_dir)
    journal = resume.Journal(launcher, run_dir, log)

    fields = dict(run_dir=run_dir, game=setup.game, config_path=setup.config_path, order=setup.order,
                  searched=len(candidates), warnings=setup.warnings, trials=launcher.trials,
                  crash_is_fail=args.crash_is_fail, settle=args.settle, changed_since=changed_since,
                  save=args.save)
    search: Search | None = None
    with _session(setup.game, launcher):
        try:
            baseline = _baseline(journal, setup, criterion, args)
            _warn_not_loaded(baseline, setup)
            hunted = _hunted(baseline, criterion, args)
            resume.update(run_dir, args=resume.saved_args(args, setup.workdir))
            if criterion is None:
                # Only now is it known whether the picked error counts as a failure.
                log(baseline.line())
            if hunted is None:
                what = "the error" if criterion else "a crash" if args.crash_is_fail and args.pick is None else "any error"
                setup.warnings.append(f"the full mod list did not reproduce {what} (baseline outcome {baseline.outcome})")
                _emit(report.build(status=report.NOT_REPRODUCED, criterion=criterion, **fields), run_dir, args.json)
                _finish(run_dir, report.NOT_REPRODUCED)
                return 1
            criterion = hunted
            steps = math.ceil(math.log2(len(candidates))) if len(candidates) > 1 else 0
            # Offsets measured with acceptance/simulate.py at 64, 200 and 400 mods.
            log(f"the full list took {baseline.duration:.0f}s; expect about {steps + 3} more trials "
                f"for a single culprit, {2 * steps + 6} for two mods that only fail together")
            search = Search(setup.order, journal, criterion, keep, repeats=args.repeats,
                            crash_is_fail=args.crash_is_fail, log=log)
            search.record(candidates, True)
            causes = search.locate_all(candidates, changed)
        except KeyboardInterrupt:
            log("interrupted; the game has been closed. `rimbisect resume` goes on from here.")
            _emit(report.build(status=report.INTERRUPTED, criterion=criterion, changed=changed,
                               causes=search.groups if search else None,
                               flaky=search.flaky if search else None, **fields), run_dir, args.json)
            return 130
        except Inconclusive as exc:
            log(f"stopping: {exc}")
            setup.warnings.append(f"{exc}. {exc.advice}")
            _emit(report.build(status=report.INCONCLUSIVE, criterion=criterion, changed=changed,
                               causes=search.groups if search else None,
                               flaky=search.flaky if search else None, **fields), run_dir, args.json)
            _finish(run_dir, report.INCONCLUSIVE)
            return 1
    status = report.FOUND if causes else report.BASE_GAME_FAILS
    _emit(report.build(status=status, criterion=criterion, causes=causes, base_fails=search.base_fails,
                       flaky=search.flaky, changed=changed, **fields), run_dir, args.json)
    _finish(run_dir, status)
    return 0 if causes else 1


def cmd_check(args) -> int:
    setup = load(args)
    _preflight(setup)
    criterion = _criterion(args)
    save = _save_file(args)
    run_dir = _new_run(setup)
    _copy_save(save, run_dir)
    launcher = _launcher(setup, args, run_dir)
    log(f"rimbisect {__version__}: one trial with all {len(setup.order.order)} active mods")
    fields = dict(run_dir=run_dir, game=setup.game, config_path=setup.config_path, order=setup.order,
                  criterion=criterion, trials=launcher.trials, settle=args.settle, save=args.save,
                  warnings=setup.warnings)
    with _session(setup.game, launcher):
        try:
            trial = launcher.run(setup.order.trial_list(setup.order.order), "check", criterion)
        except KeyboardInterrupt:
            log("interrupted; the game has been closed")
            _emit(report.build(status=report.INTERRUPTED, **fields), run_dir, args.json)
            return 130
        finally:
            (run_dir / SAVE_COPY).unlink(missing_ok=True)
    log(trial.line())
    _warn_not_loaded(trial, setup)
    passed = trial.outcome == PASS
    if passed:
        log(f"saved this mod list as the last good one: {_save_last_good(setup)}")
    status = report.CHECK_PASSED if passed else report.CHECK_FAILED
    _emit(report.build(status=status, errors=group_errors(trial.errors), **fields), run_dir, args.json)
    return 0 if passed else 1


def cmd_resume(args) -> int:
    runs = _workdir(args) / "runs"
    run_dir = args.run.expanduser().resolve() if args.run else resume.latest_unfinished(runs)
    if run_dir is None:
        raise UserError(f"there is no unfinished run in {runs} to resume")
    try:
        return _resume(run_dir, args)
    except resume.StaleRun as exc:
        raise UserError(f"{exc}, so it cannot go on. Start a new one with `rimbisect bisect`.") from None


def _resume(run_dir: Path, args) -> int:
    """args carries the options a resumed run may change."""
    state = resume.load(run_dir)
    retry = args.repeats is not None or args.timeout is not None
    if state.get("finished"):
        if state.get("status") != report.INCONCLUSIVE:
            raise UserError(f"the run in {run_dir} is finished; its report is in {run_dir / 'report.txt'}")
        if not retry:
            raise UserError(f"the run in {run_dir} stopped without an answer; {run_dir / 'report.txt'} says why. "
                            "Resume it with --repeats or --timeout to try again from there.")
    saved = _parser().parse_args(["bisect"])
    resume.restored_args(state, saved)
    if args.pick is not None and saved.pick not in (None, args.pick):
        raise UserError(f"this run already hunts error {saved.pick} of the full list; "
                        "start a new run to hunt another")
    for key in ("pick", "repeats", "timeout"):
        if getattr(args, key) is not None:
            setattr(saved, key, getattr(args, key))
    saved.json = saved.json or args.json
    log(f"resuming the run started {state.get('started', '?')}")
    if state.get("finished"):
        resume.update(run_dir, finished=False, status=None)
    return cmd_bisect(saved, (run_dir, state))


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
    print(f"{'#':>4}  {'packageId':<45} {'source':<9} {'workshop':<11} name")
    for i, row in enumerate(rows, 1):
        print(f"{i:>4}  {row['packageId']:<45} {row['source']:<9} {row['workshopId'] or '-':<11} {row['name']}")
    if setup.warnings:
        print(f"\n{len(setup.warnings)} warning(s):")
        for w in setup.warnings:
            print(f"  - {w}")
    return 0


def _above_zero(kind):
    def parse(text: str):
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a {'whole ' if kind is int else ''}number: {text!r}") from None
        if not (math.isfinite(value) and value > 0):
            raise argparse.ArgumentTypeError(f"has to be more than 0, not {text}")
        return value
    return parse


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--game", type=Path, metavar="PATH",
                        help="RimWorld install folder (default: found through Steam)")
    common.add_argument("--config", type=Path, metavar="FILE",
                        help="ModsConfig.xml to test (default: the game's own). Only read, never written.")
    common.add_argument("--json", action="store_true", help="print JSON instead of text")

    trial_opts = argparse.ArgumentParser(add_help=False)
    trial_opts.add_argument("--workdir", type=Path, metavar="PATH",
                            help=r"where runs and last-good.json go (default: %%LOCALAPPDATA%%\rimbisect)")
    what = trial_opts.add_mutually_exclusive_group()
    what.add_argument("--match", metavar="REGEX", help="a trial fails when an error or log line matches REGEX")
    what.add_argument("--match-text", metavar="TEXT",
                      help="a trial fails when an error or log line contains TEXT, taken literally")
    what.add_argument("--slower-than", type=_above_zero(float), metavar="SECONDS",
                      help="a trial fails when the map takes longer than SECONDS to be ready")
    trial_opts.add_argument("--save", metavar="NAME",
                            help="load this save instead of starting a new colony: a name from your Saves "
                                 "folder or a .rws file. Only copied, never written.")
    trial_opts.add_argument("--settle", type=_above_zero(float), default=20, metavar="SECONDS",
                            help="game seconds to keep the map running before quitting (default 20)")
    trial_opts.add_argument("--timeout", type=_above_zero(float), default=20, metavar="MINUTES",
                            help="give up on a trial after this many minutes (default 20)")

    parser = argparse.ArgumentParser(
        prog="rimbisect", description="Find the RimWorld mod, or combination of mods, behind an error.")
    parser.add_argument("--version", action="version", version=f"rimbisect {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    what = "run the game on halves of your mod list until the culprit is found"
    bisect = sub.add_parser("bisect", parents=[common, trial_opts], help=what, description=what)
    bisect.add_argument("--pick", type=_above_zero(int), metavar="N",
                        help="hunt error N from the full list's errors without asking")
    bisect.add_argument("--keep", action="append", default=[], metavar="ID[,ID]",
                        help="packageIds to load in every trial (Core and DLCs always are)")
    bisect.add_argument("--since", metavar="DATE",
                        help="first try the list without mods changed after DATE, like 2026-09-01 or 2026-09-01T18:30")
    bisect.add_argument("--ignore-last-good", action="store_true",
                        help="do not try the mods changed since the last good run first")
    bisect.add_argument("--repeats", type=_above_zero(int), default=1, metavar="N",
                        help="run a passing list up to N times before trusting it, for errors that come and go")
    bisect.add_argument("--crash-is-fail", action="store_true",
                        help="count a crash, an early exit or a map that fails to generate as the error")
    bisect.set_defaults(func=cmd_bisect)

    what = "run the full list once and, if it gets through, save it as the last good one"
    check = sub.add_parser("check", parents=[common, trial_opts], help=what, description=what)
    check.set_defaults(func=cmd_check)

    what = "go on with a bisect run that was stopped, without running its finished trials again"
    resumed = sub.add_parser("resume", help=what, description=what)
    resumed.add_argument("run", nargs="?", type=Path, metavar="RUN_FOLDER",
                         help="the run's folder (default: the latest unfinished run)")
    resumed.add_argument("--workdir", type=Path, metavar="PATH",
                         help=r"where to look for runs (default: %%LOCALAPPDATA%%\rimbisect)")
    resumed.add_argument("--pick", type=_above_zero(int), metavar="N",
                         help="the error to hunt, when the run stopped before one was picked")
    resumed.add_argument("--repeats", type=_above_zero(int), metavar="N", help="change the run's --repeats")
    resumed.add_argument("--timeout", type=_above_zero(float), metavar="MINUTES", help="change the run's --timeout")
    resumed.add_argument("--json", action="store_true", help="print JSON instead of text")
    resumed.set_defaults(func=cmd_resume)

    what = "show the active mod list as rimbisect reads it, with warnings"
    mods = sub.add_parser("mods", parents=[common], help=what, description=what)
    mods.set_defaults(func=cmd_mods)
    return parser


def _double_clicked() -> bool:
    """The standalone exe started from Explorer: the only programs in its console window
    are its own two processes (PyInstaller's launcher and the one it unpacks)."""
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return False
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    kernel32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    # Started from Explorer with input from somewhere else, there is nobody to answer.
    typing = kernel32.GetConsoleMode(kernel32.GetStdHandle(-10), ctypes.byref(ctypes.c_uint32()))
    return bool(typing) and kernel32.GetConsoleProcessList((ctypes.c_uint32 * 4)(), 4) <= 2


def _ask(prompt: str) -> str:
    """The answer, without the quotes Explorer adds to a path dragged into the window."""
    try:
        return input(prompt).strip().strip('"')
    except EOFError:
        raise UserError("no answer to read") from None


def cmd_guided(args) -> int:
    """What a double-click runs: resume an unfinished run, or bisect with the defaults."""
    log(f"rimbisect {__version__} finds the mod behind a RimWorld error. It starts the game on smaller\n"
        "and smaller parts of your mod list, which takes from a few minutes to an hour. You can use\n"
        "the computer meanwhile, but leave the game windows it opens alone. Ctrl+C stops it, and\n"
        "opening rimbisect again goes on from there.\n")
    parser = _parser()
    unfinished = resume.latest_unfinished(_workdir(args) / "runs")
    if unfinished and _ask(f"There is an unfinished run ({resume.progress(unfinished)}). "
                           "Go on with it? [Y/n] ").lower() != "n":
        try:
            return _guided_end(_resume(unfinished, parser.parse_args(["resume"])))
        except resume.StaleRun as exc:
            log(f"{exc}, so a new run starts.\n")
    bisect = parser.parse_args(["bisect"])
    while True:
        try:
            find_game(bisect.game)
            break
        except UserError as exc:
            log(str(exc))
            bisect.game = Path(_ask("Paste the RimWorld folder, the one with RimWorldWin64.exe in it: "))
    _preflight(load(bisect))
    while True:
        bisect.save = _ask("Load a save instead of starting a new colony? Type its name, or press Enter: ") or None
        try:
            _save_file(bisect)
            break
        except UserError as exc:
            log(str(exc))
    return _guided_end(cmd_bisect(bisect))


def _guided_end(code: int) -> int:
    if code == 0:
        log("To play without the error, turn the culprits off in the game's Mods screen.")
    return code


def _run(args) -> int:
    try:
        return args.func(args)
    except UserError as exc:
        print(f"rimbisect: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("rimbisect: interrupted", file=sys.stderr)
        return 130


def main(argv: list[str] | None = None) -> int:
    # Redirected output would otherwise use the ANSI code page and choke on mod names.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if argv is None and len(sys.argv) == 1 and _double_clicked():
        try:
            code = _run(argparse.Namespace(func=cmd_guided))
        except Exception:
            # Otherwise the window closes before anyone can read the traceback.
            traceback.print_exc()
            code = 1
        try:
            input("\nPress Enter to close this window.")
        except EOFError:
            pass
        return code
    return _run(_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
