"""The run report: report.json for tools, report.txt for people."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import __version__
from .modlist import LoadOrder, folder_mtime
from .signature import Criterion, ErrorGroup
from .trial import CRASH, FAIL, PASS, UNRESOLVED, Trial

FOUND = "found"
BASE_GAME_FAILS = "base_game_fails"
NOT_REPRODUCED = "not_reproduced"
INTERRUPTED = "interrupted"
CHECK_PASSED = "check_passed"
CHECK_FAILED = "check_failed"
INCONCLUSIVE = "inconclusive"

_SUMMARY = {
    FOUND: "found the mods that cause the error",
    BASE_GAME_FAILS: ("the error happens with only the kept mods loaded (Core, DLCs, --keep and what they "
                      "need); no removable mod causes it"),
    NOT_REPRODUCED: "the full mod list did not reproduce the error, so there is nothing to narrow down",
    INTERRUPTED: "stopped before it finished; the trials so far are below",
    INCONCLUSIVE: "the search could not go on; the warning below says why",
    CHECK_PASSED: "the full mod list reached the map and quit cleanly",
    CHECK_FAILED: "the full mod list did not reach the map and quit cleanly",
}


def _iso(timestamp: float | None) -> str | None:
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds") if timestamp else None


WORKSHOP_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id="


def describe_mod(order: LoadOrder, pid: str, game_version: str) -> dict:
    mod = order.mods[pid]
    return {
        "name": mod.name,
        "packageId": order.spelling.get(pid, pid),
        "workshopId": mod.workshop_id,
        "workshopUrl": WORKSHOP_URL + mod.workshop_id if mod.workshop_id else None,
        "supportedVersions": mod.supported_versions,
        "supportsThisVersion": game_version in mod.supported_versions if mod.supported_versions else None,
        "folder": str(mod.folder),
        "modified": _iso(folder_mtime(mod.folder)) if mod.folder.is_dir() else None,
        "neededBy": [{"name": order.mods[d].name, "packageId": order.spelling.get(d, d)}
                     for d in order.dependents([pid])],
    }


def trial_record(trial: Trial) -> dict:
    return {
        "number": trial.number,
        "label": trial.label,
        "mods": trial.mod_count,
        "outcome": trial.outcome,
        "seconds": trial.duration,
        "mapReadySeconds": trial.map_ready,
        "logResets": trial.log_resets,
        "gameTicks": trial.ticks,
        "notLoaded": trial.not_loaded,
        "closedWindows": trial.closed,
        "excerpt": trial.excerpt or None,
        "log": trial.log,
        "modList": trial.mods,
    }


def build(*, status: str, run_dir: Path, game, config_path: Path, order: LoadOrder,
          criterion: Criterion | None, trials: list[Trial], searched: int = 0,
          causes: list[set[str]] | None = None, base_fails: bool = False, flaky: list[dict] | None = None,
          changed: list[str] | None = None, changed_since: str | None = None,
          errors: list[ErrorGroup] | None = None, crash_is_fail: bool = False, settle: float | None = None,
          warnings: list[str]) -> dict:
    causes = [[pid for pid in order.order if pid in group] for group in causes or []]
    ordered = [pid for group in causes for pid in group]
    removed = [pid for pid in order.order if pid in ordered] + order.dependents(ordered)
    fixed_path = None
    if ordered:
        fixed_path = run_dir / "ModsConfig.fixed.xml"
        fixed_path.write_text(order.fixed_config(removed), encoding="utf-8")
    counts = {outcome: sum(t.outcome == outcome for t in trials) for outcome in (CRASH, UNRESOLVED)}
    notes = []
    if len(causes) > 1:
        notes.append(f"there are {len(causes)} separate causes; the error only goes away when every one is removed")
    if len(causes) == 1 and len(ordered) > 1:
        notes.append("the error needs all of these mods loaded together; removing any one of them avoids it")
    if len(causes) > 1:
        notes += [f"cause {number} needs all of its mods loaded together; removing any one of them avoids that cause"
                  for number, group in enumerate(causes, 1) if len(group) > 1]
    if base_fails and causes:
        notes.append("with these removed, the error still happens with only the kept mods loaded "
                     "(Core, DLCs, --keep and what they need)")
    hunting = status not in (CHECK_PASSED, CHECK_FAILED)
    if hunting and counts[CRASH] and not crash_is_fail:
        notes.append(f"{_count(counts[CRASH], 'trial')} crashed; crashes only count as the error "
                     "with --crash-is-fail")
    if hunting and counts[UNRESOLVED]:
        notes.append(f"{_count(counts[UNRESOLVED], 'trial')} gave no answer; the reason is under each in the list below")
    if status == CHECK_PASSED and errors:
        notes.append(f"it still logged {_count(len(errors), 'distinct error')}, listed below; "
                     "`rimbisect bisect --pick N` finds the mods behind error N")
    capped = [str(t.number) for t in trials if t.log_resets]
    if capped:
        notes.append(f"RimWorld's 10,000 message limit was hit in trial{'s' if len(capped) > 1 else ''} {', '.join(capped)}; the probe "
                     "switched logging back on, but anything logged while it was off is missing")
    # The probe waits for --settle seconds of game time, but no more than three times that
    # (and at least a minute) of real time.
    slow = [str(t.number) for t in trials
            if t.outcome == PASS and t.ticks is not None and settle and t.ticks < settle * 30]
    if slow:
        notes.append(f"the game ran slowly in trial{'s' if len(slow) > 1 else ''} {', '.join(slow)} and got "
                     "through less than half of --settle in game time; an error that takes a while to "
                     "show could have been missed there")
    closed = [str(t.number) for t in trials if t.closed]
    if closed:
        windows = sorted({w.rsplit(".", 1)[-1] for t in trials for w in t.closed})
        notes.append(f"the probe closed windows that paused the game in trial{'s' if len(closed) > 1 else ''} "
                     f"{', '.join(closed)}: {', '.join(windows)}")
    if flaky:
        notes.append("some mod lists gave different results on repeat runs; the result may be unreliable")
    return {
        "tool": f"rimbisect {__version__}",
        "status": status,
        "summary": _SUMMARY[status],
        "run": str(run_dir),
        "game": {"folder": str(game.root), "version": game.version, "shortVersion": game.short_version},
        "modsConfig": str(config_path),
        "activeMods": len(order.order),
        "searchedMods": searched,
        "criterion": criterion.describe() if criterion else None,
        "changedMods": [order.spelling.get(p, p) for p in changed] if changed else None,
        "changedSince": changed_since if changed else None,
        "culprits": [dict(describe_mod(order, pid, game.short_version), cause=number)
                     for number, group in enumerate(causes, 1) for pid in group],
        "fixedModsConfig": str(fixed_path) if fixed_path else None,
        "removedFromFixed": [order.spelling.get(p, p) for p in removed],
        "notes": notes,
        "trials": [trial_record(t) for t in trials],
        "totalSeconds": round(sum(t.duration for t in trials), 1),
        "flaky": flaky or [],
        "errorsLogged": [{"count": g.count, "headline": g.headline, "signature": g.signature}
                         for g in errors or []],
        "warnings": warnings,
    }


def _duration(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s" if hours else f"{minutes}m{secs:02d}s"


def render_text(report: dict) -> str:
    out = [f"{report['tool']}   {report['summary']}", ""]
    out.append(f"game        {report['game']['folder']} ({report['game']['version']})")
    searched = f", {report['searchedMods']} searched" if report["searchedMods"] else ""
    out.append(f"mod list    {report['modsConfig']} ({report['activeMods']} active{searched})")
    criterion = report["criterion"]
    if criterion:
        what = {"signature": criterion.get("example") or criterion["value"],
                "regex": f"log matches /{criterion['value']}/",
                "text": f'log contains "{criterion["value"]}"',
                "slower_than": f"map not ready within {criterion['value']}s",
                "crash": "the game crashes"}[criterion["kind"]]
        out.append(f"error       {what.splitlines()[0] if what else ''}")
    if report["changedMods"]:
        out.append(f"changed     {_count(len(report['changedMods']), 'mod')} {report['changedSince']}")
    out.append(f"run folder  {report['run']}")

    several = len({c["cause"] for c in report["culprits"]}) > 1
    for culprit in report["culprits"]:
        cause = f"CAUSE {culprit['cause']}  " if several else ""
        out += ["", f"{cause}CULPRIT  {culprit['name']}  ({culprit['packageId']})"]
        out.append(f"  workshop     {culprit['workshopUrl'] or '-'}")
        if culprit["supportsThisVersion"] is False:
            out.append(f"  versions     made for {', '.join(culprit['supportedVersions'])}, "
                       f"not {report['game']['shortVersion']}")
        out.append(f"  folder       {culprit['folder']}")
        out.append(f"  modified     {culprit['modified'] or '-'}")
        if culprit["neededBy"]:
            names = ", ".join(d["name"] for d in culprit["neededBy"])
            out.append(f"  needed by    {_count(len(culprit['neededBy']), 'active mod')}: {names}")
    if report["fixedModsConfig"]:
        out += ["", f"Your mod list without {_count(len(report['removedFromFixed']), 'mod')}:",
                f"  {report['fixedModsConfig']}"]
    for note in report["notes"]:
        out.append(f"note: {note}")

    trials = report["trials"]
    out += ["", f"TRIALS  {_count(len(trials), 'run')}, {_duration(report['totalSeconds'])} total", "",
            "   #  label                    mods  outcome        time"]
    shown = set()
    for t in trials:
        out.append(f"{t['number']:>4}  {t['label']:<22} {t['mods']:>6}  {t['outcome']:<10} {t['seconds']:>7.1f}s")
        if t["excerpt"] and t["outcome"] in (FAIL, CRASH, UNRESOLVED) and t["excerpt"] not in shown:
            shown.add(t["excerpt"])
            lines = t["excerpt"].splitlines()
            # A crash excerpt ends with the last lines the game logged, which say the most.
            lines = lines[:1] + lines[-5:] if t["outcome"] == CRASH and len(lines) > 6 else lines[:6]
            out += [f"          | {_clip(line, 150)}" for line in lines]
    if report["flaky"]:
        out += ["", "FLAKY"]
        for f in report["flaky"]:
            pairs = ", ".join(f"#{n} {o}" for n, o in zip(f["trials"], f["outcomes"]))
            out.append(f"  {f['mods']} mods: {pairs}")
    if report["errorsLogged"]:
        out += ["", f"ERRORS LOGGED  {len(report['errorsLogged'])} distinct"]
        out += [f"  {e['count']:>5}x  {_clip(e['headline'], 150)}" for e in report["errorsLogged"][:15]]
        if len(report["errorsLogged"]) > 15:
            out.append(f"         ... {len(report['errorsLogged']) - 15} more in report.json")
    if report["warnings"]:
        out += ["", f"WARNINGS  {len(report['warnings'])}"]
        out += [f"  - {w}" for w in report["warnings"]]
    return "\n".join(out) + "\n"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _clip(line: str, width: int) -> str:
    return line if len(line) <= width else line[: width - 3] + "..."


def write(report: dict, run_dir: Path) -> str:
    text = render_text(report)
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "report.txt").write_text(text, encoding="utf-8")
    return text
