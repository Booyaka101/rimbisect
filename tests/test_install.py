"""Finding the game and telling 1.7 on Waylith from 1.6 on Unity."""

from pathlib import Path

import pytest

from rimbisect.errors import UserError
from rimbisect.install import Game, default_config_dir, default_player_log, find_exe, find_game


def game(root: Path, version: str = "1.6.4871 rev590") -> Game:
    return Game(root, version)


def test_both_game_executables_are_found(tmp_path):
    assert find_exe(tmp_path) is None
    (tmp_path / "RimWorld.exe").write_text("")
    assert find_exe(tmp_path) == "RimWorld.exe"
    (tmp_path / "RimWorldWin64.exe").write_text("")
    assert find_exe(tmp_path) == "RimWorldWin64.exe"  # the Unity name keeps priority
    assert game(tmp_path).exe.name == "RimWorldWin64.exe"


def test_waylith_comes_from_the_version(tmp_path):
    assert game(tmp_path, "1.6.4871 rev590").waylith is False
    assert game(tmp_path, "1.7.4976 rev1043").waylith is True
    assert game(tmp_path, "1.7.1 rev999").waylith is True  # a 1.7 hotfix
    assert game(tmp_path, "2.0.0 rev1").waylith is True
    assert game(tmp_path, "1.7.4976 rev1043").short_version == "1.7"


def test_waylith_falls_back_to_the_executable_when_the_version_is_unknown(tmp_path):
    (tmp_path / "RimWorld.exe").write_text("")
    assert game(tmp_path, "unknown").waylith is True
    unity = tmp_path / "unity"
    unity.mkdir()
    (unity / "RimWorldWin64.exe").write_text("")
    assert game(unity, "unknown").waylith is False


def test_find_game_takes_either_executable(tmp_path):
    (tmp_path / "RimWorld.exe").write_text("")
    (tmp_path / "Version.txt").write_text("1.7.4976 rev1043", encoding="utf-8")
    found = find_game(tmp_path)
    assert found.exe == tmp_path / "RimWorld.exe"
    assert found.version == "1.7.4976 rev1043" and found.waylith is True
    (tmp_path / "RimWorld.exe").unlink()
    (tmp_path / "RimWorldWin64.exe").write_text("")
    assert find_game(tmp_path).exe == tmp_path / "RimWorldWin64.exe"


def test_find_game_names_both_executables_when_it_refuses(tmp_path):
    with pytest.raises(UserError) as err:
        find_game(tmp_path)
    assert "RimWorldWin64.exe" in str(err.value) and "RimWorld.exe" in str(err.value)


def test_player_log_lives_in_logs_next_to_the_config_folder():
    log = default_player_log()
    assert log.name == "Player.log"
    assert log.parent.name == "Logs"
    assert log.parent.parent == default_config_dir().parent
