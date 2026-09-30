import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from fakegame import CRITERION
from rimbisect.install import Game
from rimbisect.modlist import CORE, PROBE_ID, ModsConfig
from rimbisect.signature import Criterion
from rimbisect.trial import CRASH, FAIL, PASS, UNRESOLVED, GameLauncher

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
    monkeypatch.setenv("FAKE_PID_FILE", str(tmp_path / "pid"))
    config = ModsConfig("1.6.4871 rev590", [], [])
    launcher = GameLauncher(BatchGame(root, "1.6.4871 rev590"), config, tmp_path / "run", real,
                            settle=1, timeout=20, poll=0.05, log=lambda _: None)
    (tmp_path / "run").mkdir(exist_ok=True)
    return launcher


def run(launcher, monkeypatch, scenario, criterion=CRITERION):
    monkeypatch.setenv("FAKE_SCENARIO", scenario)
    return launcher.run([CORE, "author.mod", PROBE_ID], scenario, criterion)


def still_running(pid_file: Path) -> bool:
    pid = pid_file.read_text()
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return pid in out


def test_pass_writes_an_isolated_config(launcher, monkeypatch, tmp_path):
    trial = run(launcher, monkeypatch, "pass")
    assert trial.outcome == PASS
    assert trial.map_ready is not None
    assert trial.errors == [("Some unrelated error", 1)]
    assert trial.mod_count == 2
    config = launcher.savedata / "Config"
    assert "<li>author.mod</li>" in (config / "ModsConfig.xml").read_text(encoding="utf-8")
    assert (config / "Mod_Example_Settings.xml").read_text() == "settings"
    prefs = (config / "Prefs.xml").read_text(encoding="utf-8")
    assert "<runInBackground>True</runInBackground>" in prefs and "<devMode>True</devMode>" in prefs
    assert (tmp_path / "Config" / "ModsConfig.xml").read_text() == "the player's own list"
    assert "<runInBackground>False</runInBackground>" in (tmp_path / "Config" / "Prefs.xml").read_text()


@pytest.mark.parametrize("scenario, criterion, outcome, excerpt", [
    ("error", CRITERION, FAIL, "Widget_12"),
    ("logline", Criterion(pattern=re.compile("rimbisectNoSuchField")), FAIL, "XML error"),
    ("gave_up", CRITERION, CRASH, "Error generating map"),
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


def test_exit_without_done_is_a_crash(launcher, monkeypatch):
    trial = run(launcher, monkeypatch, "exit")
    assert trial.outcome == CRASH
    assert "Crash!!!" in trial.excerpt


def test_timeout(launcher, monkeypatch, tmp_path):
    launcher.timeout = 1
    trial = run(launcher, monkeypatch, "hang")
    assert trial.outcome == UNRESOLVED
    assert "no result after 1s" in trial.excerpt
    assert not still_running(tmp_path / "pid")


def test_trials_are_numbered_and_logs_kept(launcher, monkeypatch):
    first, second = run(launcher, monkeypatch, "pass"), run(launcher, monkeypatch, "exit")
    assert (first.number, second.number) == (1, 2)
    assert Path(first.log).name == "trial-01.log" and Path(second.log).is_file()
    assert launcher.trials == [first, second]
