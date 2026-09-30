import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from fakegame import CRITERION
from rimbisect.install import Game
from rimbisect.modlist import CORE, PROBE_ID, ModsConfig
from rimbisect.signature import Criterion
from rimbisect.trial import CRASH, FAIL, PASS, UNRESOLVED, GameLauncher, prepare_savedata

SRC = Path(__file__).resolve().parents[1] / "src"

FAKE = Path(__file__).with_name("fake_rimworld.py")


@dataclass(frozen=True)
class BatchGame(Game):
    @property
    def exe(self) -> Path:
        return self.root / "RimWorldWin64.bat"


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    root = tmp_path / "Rim World"
    root.mkdir()
    (root / "RimWorldWin64.bat").write_text(f'@"{sys.executable}" "{FAKE}" %*\n')
    real = tmp_path / "Config"
    real.mkdir()
    (real / "ModsConfig.xml").write_text("the player's own list")
    (real / "Prefs.xml").write_text("<PrefsData><runInBackground>False</runInBackground><devMode>True</devMode></PrefsData>")
    (real / "Mod_Example_Settings.xml").write_text("settings")
    (tmp_path / "HugsLib").mkdir()
    (tmp_path / "HugsLib" / "ModSettings.xml").write_text("hugslib settings")
    monkeypatch.setenv("FAKE_PID_FILE", str(tmp_path / "pid"))
    config = ModsConfig("1.5.4409 rev1118", [], [])
    launcher = GameLauncher(BatchGame(root, "1.6.4871 rev590"), config, tmp_path / "run", real,
                            settle=1, timeout=20, poll=0.05, log=lambda _: None)
    (tmp_path / "run").mkdir(exist_ok=True)
    return launcher


def run(launcher, monkeypatch, scenario, criterion=CRITERION):
    monkeypatch.setenv("FAKE_SCENARIO", scenario)
    return launcher.run([CORE, "author.mod", PROBE_ID], scenario, criterion)


def still_running(pid_file: Path) -> bool:
    return alive(pid_file.read_text())


def alive(pid: str) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return pid in out


def test_pass_writes_an_isolated_config(launcher, monkeypatch, tmp_path):
    trial = run(launcher, monkeypatch, "pass")
    assert trial.outcome == PASS
    assert trial.map_ready is not None
    assert trial.errors == [("Some unrelated error", 1)]
    assert trial.mod_count == 2 and trial.ticks == 1200
    config = launcher.savedata / "Config"
    mods_config = (config / "ModsConfig.xml").read_text(encoding="utf-8")
    assert "<li>author.mod</li>" in mods_config and "<version>1.6.4871 rev590</version>" in mods_config
    assert (launcher.savedata / "HugsLib" / "ModSettings.xml").read_text() == "hugslib settings"
    assert (config / "Mod_Example_Settings.xml").read_text() == "settings"
    prefs = (config / "Prefs.xml").read_text(encoding="utf-8")
    assert "<runInBackground>True</runInBackground>" in prefs and "<devMode>True</devMode>" in prefs
    assert "<uiScale>1</uiScale>" in prefs
    assert (tmp_path / "Config" / "ModsConfig.xml").read_text() == "the player's own list"
    assert "<runInBackground>False</runInBackground>" in (tmp_path / "Config" / "Prefs.xml").read_text()


@pytest.mark.parametrize("scenario, criterion, outcome, excerpt", [
    ("error", CRITERION, FAIL, "Widget_12"),
    ("logline", Criterion(pattern=re.compile("rimbisectNoSuchField")), FAIL, "XML error"),
    ("gave_up", CRITERION, CRASH, "Error generating map"),
    ("fallback", CRITERION, CRASH, "fell back to Core alone"),
    ("exit_unterminated", Criterion(pattern=re.compile("rimbisectNoSuchField")), FAIL, "no newline"),
    ("hang", Criterion(slower_than=0.5), FAIL, "map not ready after 0.5s"),
    ("slow_map", Criterion(slower_than=30), PASS, "map ready after"),
])
def test_outcome_and_the_game_is_closed(launcher, monkeypatch, tmp_path, scenario, criterion, outcome, excerpt):
    trial = run(launcher, monkeypatch, scenario, criterion)
    assert trial.outcome == outcome
    assert excerpt in trial.excerpt
    assert trial.duration < 15
    assert not still_running(tmp_path / "pid")


def test_gave_up_excerpt_names_the_dialog_and_the_error(launcher, monkeypatch):
    trial = run(launcher, monkeypatch, "gave_up")
    assert trial.excerpt.splitlines() == [
        "Error generating map: An error occurred while generating the map.",
        "Exception from asynchronous event: System.InvalidOperationException: boom",
    ]


def test_log_lines_that_arrive_after_done_still_count(launcher, monkeypatch):
    trial = run(launcher, monkeypatch, "late_line", Criterion(pattern=re.compile("rimbisectNoSuchField")))
    assert trial.outcome == FAIL
    assert "after the probe finished" not in trial.excerpt


def test_log_lines_after_the_done_line_do_not_count(launcher, monkeypatch):
    trial = run(launcher, monkeypatch, "late_line", Criterion(pattern=re.compile("after the probe finished")))
    assert trial.outcome == PASS


def test_log_resets_are_counted(launcher, monkeypatch):
    trial = run(launcher, monkeypatch, "log_reset")
    assert trial.outcome == PASS and trial.log_resets == 1


def test_windows_the_probe_closed_are_recorded(launcher, monkeypatch):
    # The probe logs the window's name too, which is not the game's error.
    trial = run(launcher, monkeypatch, "paused", Criterion(pattern=re.compile("HugsLib")))
    assert trial.outcome == PASS and trial.closed == ["HugsLib.News.Dialog_UpdateFeatures"]


def test_empty_prefs_is_replaced(launcher, tmp_path):
    (tmp_path / "Config" / "Prefs.xml").write_text("")
    prepare_savedata(tmp_path / "Config", launcher.savedata)
    assert "<runInBackground>True</runInBackground>" in (launcher.savedata / "Config" / "Prefs.xml").read_text(encoding="utf-8")


def test_exit_without_done_is_a_crash(launcher, monkeypatch):
    trial = run(launcher, monkeypatch, "exit")
    assert trial.outcome == CRASH
    assert trial.excerpt.splitlines()[0] == "the game exited early with code 1"
    assert trial.excerpt.splitlines()[-2:] == ["Crash!!!", "	Managed Stacktrace:"]


def test_timeout(launcher, monkeypatch, tmp_path):
    launcher.timeout = 1
    trial = run(launcher, monkeypatch, "hang")
    assert trial.outcome == UNRESOLVED
    assert "no result after 1s" in trial.excerpt
    assert not still_running(tmp_path / "pid")


def test_a_save_is_loaded_instead_of_a_new_colony(launcher, monkeypatch, tmp_path):
    (tmp_path / "Config" / "Prefs.xml").write_text("<PrefsData><devMode>False</devMode></PrefsData>")
    (tmp_path / "Config" / "DevModeDisabled").write_text("")
    launcher.save = tmp_path / "Colony.rws"
    launcher.save.write_text("the colony")
    trial = run(launcher, monkeypatch, "save", None)
    assert trial.outcome == PASS
    assert trial.errors == [("loaded the colony, pauseOnLoad True, told 1", 1)]
    assert "<devMode>False</devMode>" in (tmp_path / "Config" / "Prefs.xml").read_text()
    assert (tmp_path / "Config" / "DevModeDisabled").exists()


def test_settings_a_mod_changed_are_put_back_for_the_next_trial(launcher, monkeypatch):
    run(launcher, monkeypatch, "settings")
    settings = launcher.savedata / "Config" / "Mod_Example_Settings.xml"
    assert settings.read_text() == "changed by a mod"
    run(launcher, monkeypatch, "pass")
    assert settings.read_text() == "settings"


def test_mods_the_game_did_not_load(launcher, monkeypatch):
    monkeypatch.setenv("FAKE_RUNNING", f"{CORE},{PROBE_ID}")
    first = run(launcher, monkeypatch, "pass")
    assert first.outcome == PASS and first.not_loaded == ["author.mod"]
    assert run(launcher, monkeypatch, "pass").outcome == PASS  # the same mod missing again is normal
    monkeypatch.setenv("FAKE_RUNNING", PROBE_ID)
    third = run(launcher, monkeypatch, "pass")
    assert third.outcome == UNRESOLVED
    assert "did not load 1 of the mods it was given (ludeon.rimworld)" in third.excerpt


@pytest.mark.parametrize("scenario, criterion", [
    ("error", CRITERION),
    ("late_start", Criterion(pattern=re.compile("rimbisectNoSuchField"))),  # logged before the probe reports
])
def test_an_error_from_a_list_the_game_did_not_load_is_no_answer(launcher, monkeypatch, scenario, criterion):
    monkeypatch.setenv("FAKE_RUNNING", f"{CORE},author.mod,{PROBE_ID}")
    assert run(launcher, monkeypatch, "pass").outcome == PASS
    monkeypatch.setenv("FAKE_RUNNING", PROBE_ID)
    trial = run(launcher, monkeypatch, scenario, criterion)
    assert trial.outcome == UNRESOLVED and "did not load" in trial.excerpt


def test_a_config_copy_that_cannot_be_refreshed_is_no_answer(launcher, monkeypatch):
    def locked(*_):
        raise PermissionError("Prefs.xml is in use")
    monkeypatch.setattr("rimbisect.trial.prepare_savedata", locked)
    trial = run(launcher, monkeypatch, "pass")
    assert trial.outcome == UNRESOLVED and "Prefs.xml is in use" in trial.excerpt


def test_the_game_does_not_outlive_rimbisect():
    script = (f"import subprocess, sys, os; sys.path.insert(0, {str(SRC)!r}); from rimbisect.trial import Job; "
              "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              "job = Job.holding(p.pid); print(p.pid if job else 'no job', flush=True); os._exit(0)")
    pid = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30).stdout.strip()
    assert pid.isdigit(), pid
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not alive(pid)


def test_trials_are_numbered_and_logs_kept(launcher, monkeypatch):
    first, second = run(launcher, monkeypatch, "pass"), run(launcher, monkeypatch, "exit")
    assert (first.number, second.number) == (1, 2)
    assert Path(first.log).name == "trial-01.log" and Path(second.log).is_file()
    assert launcher.trials == [first, second]
