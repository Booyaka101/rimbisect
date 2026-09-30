from pathlib import Path

from fakegame import make_order
from rimbisect import report
from rimbisect.install import Game
from rimbisect.signature import ErrorGroup
from rimbisect.trial import CRASH, PASS, Trial


def build(tmp_path, trials, **kwargs):
    return report.build(status=report.FOUND, run_dir=tmp_path, game=Game(Path("RimWorld"), "1.6"),
                        config_path=Path("ModsConfig.xml"), order=make_order(4), criterion=None,
                        trials=trials, causes=[{"m01"}], warnings=[], **kwargs)


def test_notes_name_the_trials_that_hit_the_message_limit(tmp_path):
    trials = [Trial(1, "baseline", [], PASS, log_resets=1), Trial(2, "bisect", [], PASS),
              Trial(3, "bisect", [], PASS, log_resets=2)]
    data = build(tmp_path, trials)
    assert any("message limit was hit in trials 1, 3" in note for note in data["notes"])
    assert [t["logResets"] for t in data["trials"]] == [1, 0, 2]
    assert "TRIALS  3 runs," in report.render_text(data)


def test_passes_with_little_game_time_are_pointed_out(tmp_path):
    trials = [Trial(1, "baseline", [], PASS, ticks=1200), Trial(2, "bisect", [], PASS, ticks=90)]
    notes = build(tmp_path, trials, settle=20)["notes"]
    assert any("ran slowly in trial 2 " in note for note in notes)


def test_windows_that_paused_the_game_are_named(tmp_path):
    trials = [Trial(1, "baseline", [], PASS, closed=["Mod.Dialog_Welcome"]), Trial(2, "bisect", [], PASS),
              Trial(3, "bisect", [], PASS, closed=["Mod.Dialog_Welcome"])]
    data = build(tmp_path, trials)
    assert any(note == "the probe closed windows that paused the game in trials 1, 3: Dialog_Welcome" for note in data["notes"])
    assert data["trials"][0]["closedWindows"] == ["Mod.Dialog_Welcome"]


def test_causes_found_before_the_kept_mods_failed_alone(tmp_path):
    data = build(tmp_path, [], base_fails=True)
    assert data["culprits"][0]["packageId"] == "m01"
    assert any("still happens with only the kept mods loaded" in note for note in data["notes"])


def test_crash_note_depends_on_crash_is_fail(tmp_path):
    trials = [Trial(1, "bisect", [], CRASH), Trial(2, "crashed, again", [], CRASH)]
    assert any(note.startswith("1 trial crashed twice") for note in build(tmp_path, trials)["notes"])
    assert not any("crashed" in note for note in build(tmp_path, trials, crash_is_fail=True)["notes"])


def test_a_crash_that_went_away_on_the_second_try_is_not_a_note(tmp_path):
    trials = [Trial(1, "bisect", [], CRASH), Trial(2, "crashed, again", [], PASS)]
    assert not any("crashed" in note for note in build(tmp_path, trials)["notes"])


def test_a_crash_shows_how_the_game_exited_and_what_it_logged_last(tmp_path):
    excerpt = "\n".join(["the game crashed (access violation, 0xC0000005)"] + [f"line {i}" for i in range(10)])
    text = report.render_text(build(tmp_path, [Trial(1, "bisect", [], CRASH, excerpt=excerpt)]))
    shown = [line.strip("| ") for line in text.splitlines() if line.startswith("          |")]
    assert shown == ["the game crashed (access violation, 0xC0000005)"] + [f"line {i}" for i in range(5, 10)]


def test_culprit_links_to_the_workshop_and_flags_an_old_version(tmp_path):
    order = make_order(4)
    order.mods["m01"].workshop_id = "2009463077"
    order.mods["m01"].supported_versions = ["1.4", "1.5"]
    data = report.build(status=report.FOUND, run_dir=tmp_path, game=Game(Path("RimWorld"), "1.6.4871 rev590"),
                        config_path=Path("ModsConfig.xml"), order=order, criterion=None, trials=[],
                        causes=[{"m01"}], warnings=[])
    text = report.render_text(data)
    assert "workshop     https://steamcommunity.com/sharedfiles/filedetails/?id=2009463077" in text
    assert "versions     made for 1.4, 1.5, not 1.6" in text


def test_a_passing_check_still_mentions_the_errors_it_logged(tmp_path):
    data = report.build(status=report.CHECK_PASSED, run_dir=tmp_path, game=Game(Path("RimWorld"), "1.6"),
                        config_path=Path("ModsConfig.xml"), order=make_order(4), criterion=None,
                        trials=[Trial(1, "check", [], PASS)], errors=[ErrorGroup("sig", "Could not find X", 3)],
                        warnings=[])
    assert any("1 distinct error" in note for note in data["notes"])
    assert "     1.     3x  Could not find X" in report.render_text(data)
