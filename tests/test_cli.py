import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime

import pytest

from fakegame import FakeGame, write_mod
from rimbisect import cli, resume
from rimbisect.install import Game
from rimbisect.modlist import PROBE_FOLDER
from rimbisect.trial import CRASH, UNRESOLVED, Trial


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A 16-mod install, a player config folder, and a FakeGame in place of the real one."""
    root = tmp_path / "Rim World ü"
    root.mkdir()
    (root / "RimWorldWin64.exe").write_bytes(b"")
    (root / "Version.txt").write_text("1.6.4871 rev590")
    write_mod(root / "Data" / "Core", "Ludeon.RimWorld")
    ids = [f"m{i:02d}" for i in range(16)]
    for pid in ids:
        write_mod(root / "Mods" / pid, pid, deps=["m02"] if pid == "m09" else ())
    config_dir = tmp_path / "Config"
    config_dir.mkdir()
    (config_dir / "Prefs.xml").write_text("<PrefsData><runInBackground>False</runInBackground></PrefsData>")
    items = "".join(f"<li>{pid}</li>" for pid in ["Ludeon.RimWorld", *ids])
    (config_dir / "ModsConfig.xml").write_text(
        f"<ModsConfigData><version>1.6</version><activeMods>{items}</activeMods>"
        "<knownExpansions /></ModsConfigData>")

    state = {"rule": lambda mods: "m11" in mods, "running": set(), "probe_seen": []}

    def launcher(game, config, run_dir, real_config_dir, **kwargs):
        state["save"] = kwargs.get("save")
        state["save_text"] = state["save"] and state["save"].read_text()

        def rule(mods):
            state["probe_seen"].append((game.mods_dir / PROBE_FOLDER / "About" / "About.xml").is_file())
            return state["rule"](mods)

        fake = FakeGame(None, rule)
        fake.savedata = run_dir / "savedata"
        (fake.savedata / "Config").mkdir(parents=True)
        return fake

    monkeypatch.setattr(cli, "GameLauncher", launcher)
    monkeypatch.setattr(cli, "default_config_dir", lambda: config_dir)
    monkeypatch.setattr(cli, "process_running", lambda image: image in state["running"] or image == "steam.exe")
    state["game"] = ["--game", str(root)]
    state["args"] = [*state["game"], "--workdir", str(tmp_path / "work")]
    state["root"] = root
    state["work"] = tmp_path / "work"
    return state


def reports(env):
    run = sorted((env["work"] / "runs").iterdir())[-1]
    return json.loads((run / "report.json").read_text(encoding="utf-8")), run


def test_bisect_with_match_finds_the_culprit(env, capsys):
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0
    out = capsys.readouterr().out
    assert "CULPRIT  Mod m11  (m11)" in out
    data, run = reports(env)
    assert data["status"] == "found"
    assert [c["packageId"] for c in data["culprits"]] == ["m11"]
    assert "<li>m11</li>" not in (run / "ModsConfig.fixed.xml").read_text()
    assert (run / "report.txt").read_text(encoding="utf-8") == out
    assert env["probe_seen"] and all(env["probe_seen"])
    assert not (env["root"] / "Mods" / PROBE_FOLDER).exists()
    assert not (run / "savedata").exists()
    assert "TRIALS  " in out and " runs, " in out


def test_culprit_with_dependents_is_removed_with_them(env, capsys):
    env["rule"] = lambda mods: "m02" in mods
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["culprits"][0]["neededBy"] == [{"name": "Mod m09", "packageId": "m09"}]
    assert data["removedFromFixed"] == ["m02", "m09"]


def test_interaction_is_reported_as_a_combination(env, capsys):
    env["rule"] = lambda mods: {"m04", "m13"} <= mods
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0
    data, _ = reports(env)
    assert [c["packageId"] for c in data["culprits"]] == ["m04", "m13"]
    assert any("all of these mods" in note for note in data["notes"])


def test_separate_causes_are_reported_as_such(env, capsys):
    env["rule"] = lambda mods: "m04" in mods or "m13" in mods
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0
    out = capsys.readouterr().out
    data, run = reports(env)
    assert sorted((c["cause"], c["packageId"]) for c in data["culprits"]) in ([(1, "m04"), (2, "m13")],
                                                                              [(1, "m13"), (2, "m04")])
    assert "CAUSE 1  CULPRIT" in out and "CAUSE 2  CULPRIT" in out
    assert any("2 separate causes" in note for note in data["notes"])
    fixed = (run / "ModsConfig.fixed.xml").read_text()
    assert "<li>m04</li>" not in fixed and "<li>m13</li>" not in fixed


def test_keep_takes_what_the_kept_mods_need(env, capsys):
    env["rule"] = lambda mods: "m02" in mods
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference", "--keep", "m09"]) == 1
    assert "14 to search" in capsys.readouterr().err
    data, _ = reports(env)
    assert data["status"] == "base_game_fails" and "--keep and what they need" in data["summary"]


def test_a_list_that_times_out_twice_ends_the_search(env, capsys):
    env["rule"] = lambda mods: UNRESOLVED if "m05" in mods and len(mods) < 12 else "m11" in mods
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 1
    data, _ = reports(env)
    assert data["status"] == "inconclusive"
    assert any("gave no answer twice" in w for w in data["warnings"])


@pytest.mark.parametrize("outcome, label", [(UNRESOLVED, "no answer, again"), (CRASH, "crashed, again")])
def test_a_baseline_without_an_answer_is_run_again(env, capsys, outcome, label):
    first = [outcome]
    env["rule"] = lambda mods: first.pop() if first else "m11" in mods
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0
    data, _ = reports(env)
    assert [t["label"] for t in data["trials"][:3]] == ["baseline", label, "without changed mods"] or \
        [t["label"] for t in data["trials"][:2]] == ["baseline", label]
    assert [c["packageId"] for c in data["culprits"]] == ["m11"]


def test_a_baseline_crash_counts_at_once_under_crash_is_fail(env, capsys):
    env["rule"] = lambda mods: CRASH if "m11" in mods else False
    args = ["bisect", *env["args"], "--match", "nothing like this", "--crash-is-fail", "--repeats", "3"]
    assert cli.main(args) == 0
    data, _ = reports(env)
    assert [t["label"] for t in data["trials"]].count("baseline again") == 0
    assert [c["packageId"] for c in data["culprits"]] == ["m11"]


def test_signature_mode_with_pick(env, capsys):
    assert cli.main(["bisect", *env["args"], "--pick", "1"]) == 0
    data, _ = reports(env)
    assert data["criterion"]["kind"] == "signature"
    assert data["trials"][0]["outcome"] == "FAIL"
    assert [c["packageId"] for c in data["culprits"]] == ["m11"]


def test_signature_mode_shows_the_baseline_as_failing_once_picked(env, capsys):
    assert cli.main(["bisect", *env["args"], "--pick", "1"]) == 0
    baseline = [line for line in capsys.readouterr().err.splitlines() if "baseline" in line]
    assert len(baseline) == 1 and " FAIL " in baseline[0]


def test_asks_again_after_a_bad_answer(env, capsys, monkeypatch):
    answers = iter(["x", "", "99", "1"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert cli.main(["bisect", *env["args"]]) == 0
    data, _ = reports(env)
    assert [c["packageId"] for c in data["culprits"]] == ["m11"]


def test_pick_out_of_range(env, capsys):
    assert cli.main(["bisect", *env["args"], "--pick", "2"]) == 2
    assert "between 1 and 1" in capsys.readouterr().err


def test_match_text_is_taken_literally(env, capsys):
    assert cli.main(["bisect", *env["args"], "--match-text", "Widget_12 (wanter=thingDef)"]) == 0
    data, run = reports(env)
    assert [c["packageId"] for c in data["culprits"]] == ["m11"]
    assert data["criterion"] == {"kind": "text", "value": "Widget_12 (wanter=thingDef)"}
    assert 'error       log contains "Widget_12 (wanter=thingDef)"' in (run / "report.txt").read_text(encoding="utf-8")


def test_workshop_mods_need_steam(env, capsys, monkeypatch):
    monkeypatch.setattr(Game, "workshop_dir", property(lambda self: self.root / "Mods"))
    monkeypatch.setattr(cli, "process_running", lambda image: False)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 2
    assert "Steam is not running" in capsys.readouterr().err
    assert not (env["work"] / "runs").exists()


def test_local_mods_do_not_need_steam(env, capsys, monkeypatch):
    monkeypatch.setattr(cli, "process_running", lambda image: False)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0


def test_signature_mode_needs_pick_when_not_interactive(env, capsys):
    assert cli.main(["bisect", *env["args"]]) == 2
    err = capsys.readouterr().err
    assert "1.     1x  Could not resolve cross-reference" in err
    assert "--pick N" in err


def test_nothing_to_find(env, capsys):
    env["rule"] = lambda mods: False
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 1
    data, _ = reports(env)
    assert data["status"] == "not_reproduced"
    assert len(data["trials"]) == 1
    assert cli.main(["bisect", *env["args"], "--pick", "1"]) == 1


def test_error_in_the_base_game(env, capsys):
    env["rule"] = lambda mods: True
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 1
    data, _ = reports(env)
    assert data["status"] == "base_game_fails"
    assert data["fixedModsConfig"] is None


def test_check_saves_last_good_and_bisect_tries_changed_mods_first(env, capsys):
    env["rule"] = lambda mods: False
    assert cli.main(["check", *env["args"]]) == 0
    assert (env["work"] / "last-good.json").is_file()
    later = time.time() + 60
    changed = env["root"] / "Mods" / "m07" / "About" / "About.xml"
    os.utime(changed, (later, later))
    env["rule"] = lambda mods: "m07" in mods
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0
    data, _ = reports(env)
    assert data["changedMods"] == ["m07"]
    assert [t["label"] for t in data["trials"]] == ["baseline", "without changed mods", "changed mods alone"]


def test_check_fails_on_match(env, capsys):
    assert cli.main(["check", *env["args"], "--match", "cross-reference"]) == 1
    data, _ = reports(env)
    assert data["status"] == "check_failed"
    assert not (env["work"] / "last-good.json").exists()


def test_since_date(env, capsys):
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference", "--since", "31/12/2025"]) == 2
    err = capsys.readouterr().err
    assert "--since takes a date" in err and "rimbisect 0" not in err
    assert not (env["work"] / "runs").exists()
    later = time.time() + 60
    os.utime(env["root"] / "Mods" / "m11" / "About" / "About.xml", (later, later))
    since = datetime.fromtimestamp(later - 30).isoformat(timespec="seconds")
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference", "--since", since]) == 0
    data, run = reports(env)
    assert data["changedMods"] == ["m11"]
    assert f"changed     1 mod since {since}" in (run / "report.txt").read_text(encoding="utf-8")


def test_broken_last_good_is_ignored_with_a_warning(env, capsys):
    env["work"].mkdir()
    (env["work"] / "last-good.json").write_text('{"mods": {"m07": null}}')
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0
    data, _ = reports(env)
    assert data["changedMods"] is None
    assert any("last-good.json" in w for w in data["warnings"])


def test_interrupt_before_any_trial(env, capsys, monkeypatch):
    def interrupted(game):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "install_probe", interrupted)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 130
    assert "interrupted" in capsys.readouterr().err


def test_interrupt_writes_a_partial_report(env, capsys, monkeypatch):
    calls = {"n": 0}

    def rule(mods):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        return "m11" in mods

    env["rule"] = rule
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 130
    data, _ = reports(env)
    assert data["status"] == "interrupted"
    assert len(data["trials"]) == 2
    env["rule"] = lambda mods: (_ for _ in ()).throw(KeyboardInterrupt)
    assert cli.main(["check", *env["args"]]) == 130
    data, _ = reports(env)
    assert data["status"] == "interrupted" and not data["trials"]
    assert not (env["root"] / "Mods" / PROBE_FOLDER).exists()


def test_user_errors(env, tmp_path, capsys):
    env["running"].add("RimWorldWin64.exe")
    assert cli.main(["bisect", *env["args"], "--match", "x"]) == 2
    assert "already running" in capsys.readouterr().err
    env["running"].clear()
    assert cli.main(["bisect", *env["args"], "--match", "(unclosed"]) == 2
    assert "--match-text" in capsys.readouterr().err
    assert not (env["work"] / "runs").exists()
    assert cli.main(["mods", "--game", str(tmp_path)]) == 2
    assert "does not contain RimWorldWin64.exe" in capsys.readouterr().err
    assert cli.main(["mods", *env["game"], "--config", str(tmp_path / "nope.xml")]) == 2
    assert "nope.xml: no such file" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["bisect", *env["args"], "--match", "x", "--slower-than", "30"])
    for extra, message in [(["--match", "x", "--pick", "1"], "leave it out with --match"),
                           (["--match-text", ""], "need some text"),
                           (["--keep", "m03,nosuchmod"], "--keep nosuchmod: not in the active mod list")]:
        assert cli.main(["bisect", *env["args"], *extra]) == 2
        assert message in capsys.readouterr().err
    assert not (env["work"] / "runs").exists()
    (tmp_path / "a file").write_text("")
    for workdir, message in [(tmp_path / "a file", "could not create the run folder"), (tmp_path / "a=b", "'='")]:
        assert cli.main(["check", *env["game"], "--workdir", str(workdir)]) == 2
        assert message in capsys.readouterr().err
    assert not (tmp_path / "a=b" / "runs").exists()


@pytest.mark.parametrize("option, value", [("--settle", "-1"), ("--settle", "nan"), ("--timeout", "0"),
                                           ("--timeout", "inf"), ("--slower-than", "-5"), ("--repeats", "0"),
                                           ("--repeats", "1.5"), ("--pick", "-3")])
def test_numbers_have_to_make_sense(env, capsys, option, value):
    with pytest.raises(SystemExit):
        cli.main(["bisect", *env["args"], option, value])
    assert option in capsys.readouterr().err


def test_mods_command(env, capsys):
    assert cli.main(["mods", *env["game"]]) == 0
    out = capsys.readouterr().out
    assert "17 active mods" in out
    assert out.splitlines()[3].split() == ["#", "packageId", "source", "workshop", "name"]
    assert cli.main(["mods", *env["game"], "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mods"][10]["dependencies"] == ["m02"]


def test_crash_is_fail_hunts_the_crash(env, capsys):
    env["rule"] = lambda mods: CRASH if "m06" in mods else False
    assert cli.main(["bisect", *env["args"], "--crash-is-fail"]) == 0
    data, _ = reports(env)
    assert data["criterion"]["kind"] == "crash"
    assert [c["packageId"] for c in data["culprits"]] == ["m06"]
    assert not any("crashed" in note for note in data["notes"])


def test_crash_is_fail_without_a_crash_does_not_ask_for_an_error(env, capsys):
    assert cli.main(["bisect", *env["args"], "--crash-is-fail"]) == 1
    data, _ = reports(env)
    assert data["status"] == "not_reproduced"
    assert "did not reproduce a crash" in data["warnings"][-1]


def test_crashes_do_not_count_without_crash_is_fail(env, capsys):
    # Lists with m06 but without m11 crash; those trials must count as passing.
    env["rule"] = lambda mods: "m11" in mods or ("m06" in mods and CRASH)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0
    data, _ = reports(env)
    assert [c["packageId"] for c in data["culprits"]] == ["m11"]
    assert any("crashed twice in a row" in note for note in data["notes"])


def test_closed_stdin_on_a_terminal_asks_for_pick(env, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(EOFError))
    assert cli.main(["bisect", *env["args"]]) == 2
    assert "--pick N" in capsys.readouterr().err


def _interrupt_after(count, rule):
    live = {"n": 0}

    def run(mods):
        live["n"] += 1
        if live["n"] > count:
            raise KeyboardInterrupt
        return rule(mods)
    return run, live


def test_resume_goes_on_without_running_the_finished_trials_again(env, capsys):
    rule = env["rule"]
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0
    whole, _ = reports(env)
    env["rule"], live = _interrupt_after(3, rule)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 130
    assert "rimbisect resume" in capsys.readouterr().err
    stopped, run = reports(env)
    env["rule"], live = _interrupt_after(99, rule)
    assert cli.main(["resume", "--workdir", str(env["work"])]) == 0
    assert "3 trials of this run are recorded" in capsys.readouterr().err
    data, resumed = reports(env)
    assert resumed == run and data["status"] == "found"
    assert [c["packageId"] for c in data["culprits"]] == ["m11"]
    assert live["n"] == len(whole["trials"]) - 3
    assert [t["number"] for t in data["trials"]] == list(range(1, len(whole["trials"]) + 1))
    assert cli.main(["resume", "--workdir", str(env["work"])]) == 2
    assert "no unfinished run" in capsys.readouterr().err
    assert cli.main(["resume", str(run)]) == 2
    assert "is finished" in capsys.readouterr().err


def test_resume_keeps_the_picked_error(env, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "1")
    env["rule"], _ = _interrupt_after(2, env["rule"])
    assert cli.main(["bisect", *env["args"]]) == 130
    env["rule"] = lambda mods: "m11" in mods
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["resume", "--workdir", str(env["work"])]) == 0
    data, _ = reports(env)
    assert data["criterion"]["kind"] == "signature" and data["trials"][0]["outcome"] == "FAIL"


def test_an_inconclusive_run_is_tried_again_only_with_other_options(env, capsys):
    env["rule"] = lambda mods: UNRESOLVED if "m05" in mods and len(mods) < 12 else "m11" in mods
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 1
    _, run = reports(env)
    assert cli.main(["resume", "--workdir", str(env["work"])]) == 2
    assert "no unfinished run" in capsys.readouterr().err
    assert cli.main(["resume", str(run)]) == 2
    assert "Resume it with --repeats or --timeout" in capsys.readouterr().err
    env["rule"] = lambda mods: "m11" in mods
    assert cli.main(["resume", str(run), "--timeout", "40"]) == 0
    data, _ = reports(env)
    assert [c["packageId"] for c in data["culprits"]] == ["m11"]
    numbers = [t["number"] for t in data["trials"]]
    assert numbers == sorted(set(numbers))
    assert json.loads((run / "run.json").read_text(encoding="utf-8"))["args"]["timeout"] == 40


def _shown_once():
    shown = []

    def rule(mods):
        # Only the first list with m11 shows the error.
        shown.append("m11" in mods)
        return shown.count(True) == 1 and shown[-1]
    return rule


def test_an_inconclusive_run_is_only_tried_again_in_a_way_that_can_change_the_answer(env, capsys):
    env["rule"] = _shown_once()
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference", "--repeats", "2"]) == 1
    _, run = reports(env)
    capsys.readouterr()
    assert cli.main(["resume", str(run), "--timeout", "40"]) == 2
    assert "--repeats higher than 2" in capsys.readouterr().err
    assert cli.main(["resume", str(run), "--repeats", "1"]) == 2
    assert "each passing list up to 2 times" in capsys.readouterr().err
    env["rule"] = lambda mods: "m11" in mods
    assert cli.main(["resume", str(run), "--repeats", "4"]) == 0


def test_resume_takes_the_pick_a_stopped_run_is_waiting_for(env, capsys):
    assert cli.main(["bisect", *env["args"]]) == 2
    assert cli.main(["resume", "--workdir", str(env["work"]), "--pick", "1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "found"
    env["rule"], _ = _interrupt_after(2, env["rule"])
    assert cli.main(["bisect", *env["args"], "--pick", "1"]) == 130
    assert cli.main(["resume", "--workdir", str(env["work"]), "--pick", "2"]) == 2
    assert "already hunts error 1" in capsys.readouterr().err


def test_resume_goes_by_what_the_first_trial_could_not_load(env, capsys, monkeypatch):
    original = FakeGame.run

    def unloading(self, mods, label, criterion):
        """m15 never loads, which a crash before the game started does not tell; a trial
        missing more than the first answered one did gives no answer."""
        trial = original(self, mods, label, criterion)
        trial.not_loaded = ["m15"] if "m15" in mods and trial.outcome != CRASH else []
        if self.not_loaded_at_first is None and trial.outcome != CRASH:
            self.not_loaded_at_first = set(trial.not_loaded)
        elif self.not_loaded_at_first is not None and set(trial.not_loaded) - self.not_loaded_at_first:
            trial.outcome = UNRESOLVED
        return trial

    monkeypatch.setattr(FakeGame, "run", unloading)
    first = [CRASH]
    rule = env["rule"] = lambda mods: first.pop() if first else "m11" in mods
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0
    whole, _ = reports(env)
    first.append(CRASH)
    env["rule"], _ = _interrupt_after(3, rule)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 130
    env["rule"] = rule
    assert cli.main(["resume", "--workdir", str(env["work"])]) == 0
    data, _ = reports(env)
    assert [(t["label"], t["outcome"]) for t in data["trials"]] == \
        [(t["label"], t["outcome"]) for t in whole["trials"]]


def test_a_cut_off_last_trial_line_is_dropped_and_later_trials_are_kept(env, capsys):
    rule = env["rule"]
    env["rule"], _ = _interrupt_after(3, rule)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 130
    _, run = reports(env)
    trials = run / "trials.jsonl"
    with open(trials, "ab") as fh:
        fh.write('{"number": 4, "label": "bisect \u00fc'.encode()[:-1])
    env["rule"], _ = _interrupt_after(2, rule)
    assert cli.main(["resume", "--workdir", str(env["work"])]) == 130
    assert [t.number for t in resume.read_trials(trials)] == [1, 2, 3, 4, 5]


def test_trials_the_search_no_longer_asks_for_are_dropped(env, capsys):
    rule = env["rule"]
    env["rule"], _ = _interrupt_after(4, rule)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 130
    _, run = reports(env)
    about = env["root"] / "Mods" / "m09" / "About" / "About.xml"
    before = about.stat().st_mtime
    about.write_text(about.read_text().replace("<li><packageId>m02</packageId></li>", ""), encoding="utf-8")
    os.utime(about, (before, before))
    os.utime(about.parent, (before, before))
    env["rule"], live = _interrupt_after(1, rule)
    assert cli.main(["resume", "--workdir", str(env["work"])]) == 130
    assert "the search went another way" in capsys.readouterr().err
    kept = resume.read_trials(run / "trials.jsonl")
    env["rule"], live = _interrupt_after(99, rule)
    assert cli.main(["resume", "--workdir", str(env["work"])]) == 0
    assert "went another way" not in capsys.readouterr().err
    data, _ = reports(env)
    assert [t["number"] for t in data["trials"]][:len(kept)] == [t.number for t in kept]


def test_only_the_newest_run_is_offered(env, capsys):
    rule = env["rule"]
    env["rule"], _ = _interrupt_after(2, rule)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 130
    env["rule"] = rule
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0
    assert resume.latest_unfinished(env["work"] / "runs") is None
    env["rule"], _ = _interrupt_after(2, rule)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 130
    _, run = reports(env)
    (run.parent / "9999-12-31_00-00-00").mkdir()
    (run.parent / "9999-12-31_00-00-00" / "run.json").write_text("[]")
    assert resume.latest_unfinished(env["work"] / "runs") == run
    assert "2 trials done" in resume.progress(run)


def test_a_run_started_by_another_rimbisect_is_not_resumed(env, capsys):
    env["rule"], _ = _interrupt_after(2, env["rule"])
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 130
    _, run = reports(env)
    state = json.loads((run / "run.json").read_text(encoding="utf-8"))
    state["version"] = "0.1.0"
    (run / "run.json").write_text(json.dumps(state), encoding="utf-8")
    assert cli.main(["resume", "--workdir", str(env["work"])]) == 2
    assert f"rimbisect was updated from 0.1.0 to {cli.__version__} since this run started" in capsys.readouterr().err


def test_resume_warns_about_newer_files_and_refuses_another_mod_list(env, capsys):
    rule = env["rule"]
    env["rule"], _ = _interrupt_after(2, rule)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 130
    later = time.time() + 60
    os.utime(env["root"] / "Mods" / "m04", (later, later))
    env["rule"] = rule
    assert cli.main(["resume", "--workdir", str(env["work"])]) == 0
    assert "1 mods have files newer than the start of this run (m04)" in reports(env)[0]["warnings"][0]
    env["rule"], _ = _interrupt_after(2, rule)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 130
    config = env["work"].parent / "Config" / "ModsConfig.xml"
    config.write_text(config.read_text().replace("<li>m03</li>", ""))
    env["running"].add("RimWorldWin64.exe")
    assert cli.main(["resume", "--workdir", str(env["work"])]) == 2
    assert "the active mod list changed" in capsys.readouterr().err


def test_pick_lists_exceptions_first_and_finds_errors_by_text(capsys, monkeypatch):
    names = [a + b for a in "ABCD" for b in "abcdefghij"]
    errors = [(f"Could not load reference to Verse.ThingDef named TM_{name}", 50 - i) for i, name in enumerate(names)]
    errors.append(("encountered ArgumentException while patching PawnCanOpen\n"
                   "  at HarmonyLib.PatchClassProcessor.Patch ()", 1))
    answers = iter(["Could not load", "nothing like it", "TM_Dh", "1"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    args = argparse.Namespace(crash_is_fail=False, pick=None)
    baseline = Trial(1, "baseline", [], errors=errors)
    criterion = cli._hunted(baseline, None, args)
    err = capsys.readouterr().err
    assert "     1.     1x  encountered ArgumentException" in err
    assert "40 errors contain that" in err and "no error contains that" in err
    assert criterion.label == "Could not load reference to Verse.ThingDef named TM_Dh" and args.pick == 39
    assert baseline.outcome == "FAIL"


def test_save_is_found_by_name_and_copied_into_the_run(env, capsys):
    saves = env["work"].parent / "Saves"
    saves.mkdir()
    (saves / "Tribe of Estian.rws").write_text("the tribe")
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference", "--save", "Tribe"]) == 2
    err = capsys.readouterr().err
    assert "--save Tribe: no such save. The newest in" in err and err.rstrip().endswith(": Tribe of Estian")
    assert not (env["work"] / "runs").exists()
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference", "--save", "Tribe of Estian"]) == 0
    data, run = reports(env)
    assert data["save"] == str(saves / "Tribe of Estian.rws")
    assert env["save"] == run / "save.rws" and env["save_text"] == "the tribe"
    assert not env["save"].exists()
    assert f"save        {saves}" in capsys.readouterr().out


@pytest.fixture
def clicked(env, tmp_path, monkeypatch):
    """The exe double-clicked: no arguments, defaults for everything, answers from the list."""
    found = cli.find_game
    monkeypatch.setattr(cli, "find_game", lambda path: found(path or env["root"]))
    monkeypatch.setattr(cli, "_double_clicked", lambda: True)
    monkeypatch.setattr("sys.argv", ["rimbisect.exe"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    env["work"] = tmp_path / "rimbisect"
    env["answers"] = []
    env["prompts"] = []

    def answer(prompt):
        env["prompts"].append(prompt)
        return env["answers"].pop(0)

    monkeypatch.setattr("builtins.input", answer)
    return env


def test_double_click_runs_a_bisect_and_waits_before_closing(clicked, capsys):
    clicked["answers"] = ["Nope", "", "1", ""]
    assert cli.main() == 0
    data, _ = reports(clicked)
    assert [c["packageId"] for c in data["culprits"]] == ["m11"]
    err = capsys.readouterr().err
    assert "no such save" in err
    assert err.rstrip().endswith("rimbisect found Mod m11. To play without the error, turn it off in the "
                                 "game's Mods screen.")
    assert clicked["prompts"][-1].endswith("Press Enter to close this window.")


def test_a_long_run_shows_progress_in_the_title_and_rings_when_done(env, capsys, monkeypatch):
    titles = []
    monkeypatch.setattr(cli, "_title", titles.append)
    monkeypatch.setattr(cli, "LONG_RUN", 0)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0
    assert titles[0] == "rimbisect: 1 of about 8 to 15 trials"
    assert titles[-1].startswith(f"rimbisect: {len(reports(env)[0]['trials'])} of about")
    assert capsys.readouterr().err.endswith("\a")


def test_double_click_offers_to_resume(clicked, capsys):
    clicked["rule"], _ = _interrupt_after(2, clicked["rule"])
    clicked["answers"] = ["", "1", ""]
    assert cli.main() == 130
    clicked["rule"] = lambda mods: "m11" in mods
    clicked["answers"] = ["", ""]
    assert cli.main() == 0
    assert "unfinished run (started " in clicked["prompts"][-2] and "2 trials done" in clicked["prompts"][-2]
    data, _ = reports(clicked)
    assert data["status"] == "found" and len(list((clicked["work"] / "runs").iterdir())) == 1


def test_double_click_keeps_a_crash_on_screen(clicked, capsys, monkeypatch):
    monkeypatch.setattr(cli, "cmd_bisect", lambda args: 1 / 0)
    clicked["answers"] = ["", ""]
    assert cli.main() == 1
    assert "ZeroDivisionError" in capsys.readouterr().err and clicked["answers"] == []


def test_a_run_keeps_its_copy_of_the_save_only_while_it_can_go_on(env, capsys, monkeypatch):
    saves = env["work"].parent / "Saves"
    saves.mkdir()
    (saves / "Colony.rws").write_text("the colony")
    args = [*env["args"], "--match", "cross-reference", "--save", "Colony"]
    assert cli.main(["check", *args]) == 1
    _, run = reports(env)
    assert env["save_text"] == "the colony" and not (run / "save.rws").exists()
    env["rule"], _ = _interrupt_after(2, env["rule"])
    assert cli.main(["bisect", *args]) == 130
    _, run = reports(env)
    (run / "save.rws").unlink()
    assert cli.main(["resume", "--workdir", str(env["work"])]) == 2
    assert "the copy of the save" in capsys.readouterr().err

    def denied(src, dst):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(cli.shutil, "copyfile", denied)
    assert cli.main(["bisect", *args]) == 2
    assert "could not copy" in capsys.readouterr().err
    _, newest = max((p.name, p) for p in (env["work"] / "runs").iterdir())
    assert not (newest / "run.json").exists()


def test_double_click_starts_over_when_the_mod_list_changed(clicked, capsys):
    clicked["rule"], _ = _interrupt_after(2, clicked["rule"])
    clicked["answers"] = ["", "1", ""]
    assert cli.main() == 130
    config = clicked["work"].parent / "Config" / "ModsConfig.xml"
    config.write_text(config.read_text().replace("<li>m03</li>", ""))
    clicked["rule"] = lambda mods: "m11" in mods
    clicked["answers"] = ["", "", "1", ""]
    assert cli.main() == 0
    err = capsys.readouterr().err
    assert "the active mod list changed since this run started, so a new run starts" in err
    assert "rimbisect found Mod m11. To play" in err
    assert len(list((clicked["work"] / "runs").iterdir())) == 2


def test_double_click_asks_where_the_game_is(clicked, capsys, monkeypatch):
    found = cli.find_game

    def find_game(path):
        if path is None:
            raise cli.UserError("could not find RimWorld through Steam")
        return found(path)
    monkeypatch.setattr(cli, "find_game", find_game)
    clicked["answers"] = [str(clicked["root"].parent / "nope"), f'"{clicked["root"]}"', "", "1", ""]
    assert cli.main() == 0
    assert "does not contain RimWorldWin64.exe" in capsys.readouterr().err
    assert json.loads(next((clicked["work"] / "runs").iterdir()).joinpath("run.json").read_text(
        encoding="utf-8"))["args"]["game"] == str(clicked["root"])


@pytest.mark.parametrize("flaky, offer, option", [
    (False, "giving each trial up to 40 minutes", ("timeout", 40)),
    (True, "running each passing list up to 3 times", ("repeats", 3)),
])
def test_double_click_offers_to_try_a_run_without_an_answer_again(clicked, flaky, offer, option):
    clicked["rule"] = _shown_once() if flaky else \
        lambda mods: UNRESOLVED if "m05" in mods and len(mods) < 12 else "m11" in mods
    clicked["answers"] = ["", "1", ""]
    assert cli.main() == 1
    clicked["rule"] = lambda mods: "m11" in mods
    clicked["answers"] = ["", ""]
    assert cli.main() == 0
    assert "stopped without an answer" in clicked["prompts"][-2] and offer in clicked["prompts"][-2]
    data, run = reports(clicked)
    assert data["status"] == "found" and len(list((clicked["work"] / "runs").iterdir())) == 1
    assert json.loads((run / "run.json").read_text(encoding="utf-8"))["args"][option[0]] == option[1]


def test_double_click_without_input_stops(clicked, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(EOFError))
    assert cli.main() == 2
    assert "no answer to read" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "nt", reason="a Windows mutex")
def test_one_run_at_a_time_per_game(env, capsys):
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    other = kernel32.CreateMutexW(None, False, cli._lock_name(cli.find_game(env["root"])))
    try:
        assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 2
        assert "another rimbisect is already running on this game" in capsys.readouterr().err
    finally:
        kernel32.CloseHandle(other)
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 0


def test_closing_the_window_takes_the_probe_out(env, capsys, monkeypatch):
    closers = []

    @contextmanager
    def on_close(action):
        closers.append(action)
        yield
    monkeypatch.setattr(cli, "_on_console_close", on_close)
    left = []

    def closed_during_a_trial(mods):
        closers[-1]()
        left.append((env["root"] / "Mods" / PROBE_FOLDER).exists())
        raise KeyboardInterrupt
    env["rule"] = closed_during_a_trial
    assert cli.main(["bisect", *env["args"], "--match", "cross-reference"]) == 130
    assert left == [False]
