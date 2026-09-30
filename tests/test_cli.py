import json
import os
import time

import pytest

from fakegame import FakeGame, write_mod
from rimbisect import cli
from rimbisect.modlist import PROBE_FOLDER
from rimbisect.trial import CRASH


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
        def rule(mods):
            state["probe_seen"].append((game.mods_dir / PROBE_FOLDER / "About" / "About.xml").is_file())
            return state["rule"](mods)

        return FakeGame(None, rule)

    monkeypatch.setattr(cli, "GameLauncher", launcher)
    monkeypatch.setattr(cli, "default_config_dir", lambda: config_dir)
    monkeypatch.setattr(cli, "process_running", lambda image: image in state["running"] or image == "steam.exe")
    state["args"] = ["--game", str(root), "--workdir", str(tmp_path / "work")]
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


def test_signature_mode_with_pick(env, capsys):
    assert cli.main(["bisect", *env["args"], "--pick", "1"]) == 0
    data, _ = reports(env)
    assert data["criterion"]["kind"] == "signature"
    assert data["trials"][0]["outcome"] == "FAIL"
    assert [c["packageId"] for c in data["culprits"]] == ["m11"]


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
    assert "--since takes a date" in capsys.readouterr().err


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
    assert not (env["root"] / "Mods" / PROBE_FOLDER).exists()


def test_user_errors(env, tmp_path, capsys):
    env["running"].add("RimWorldWin64.exe")
    assert cli.main(["bisect", *env["args"], "--match", "x"]) == 2
    assert "already running" in capsys.readouterr().err
    env["running"].clear()
    assert cli.main(["bisect", *env["args"], "--match", "(unclosed"]) == 2
    assert "not a valid regular expression" in capsys.readouterr().err
    assert cli.main(["mods", "--game", str(tmp_path)]) == 2
    assert "does not contain RimWorldWin64.exe" in capsys.readouterr().err
    assert cli.main(["mods", *env["args"], "--config", str(tmp_path / "nope.xml")]) == 2
    assert "no ModsConfig.xml" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["bisect", *env["args"], "--match", "x", "--slower-than", "30"])


def test_mods_command(env, capsys):
    assert cli.main(["mods", *env["args"]]) == 0
    out = capsys.readouterr().out
    assert "17 active mods" in out
    assert cli.main(["mods", *env["args"], "--json"]) == 0
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
    assert any("crashed; crashes only count as the error with --crash-is-fail" in note for note in data["notes"])


def test_closed_stdin_on_a_terminal_asks_for_pick(env, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(EOFError))
    assert cli.main(["bisect", *env["args"]]) == 2
    assert "--pick N" in capsys.readouterr().err
