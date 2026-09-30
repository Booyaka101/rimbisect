from pathlib import Path

import pytest

from fakegame import make_order, write_mod
from rimbisect.errors import UserError
from rimbisect.install import Game, library_with_app, parse_vdf
from rimbisect.modlist import CORE, PROBE_ID, LoadOrder, ModsConfig, read_mods_config, scan_mods

VDF = r'''
"libraryfolders"
{
	"0"
	{
		"path"		"C:\\Program Files (x86)\\Steam"
		"apps"
		{
			"228980"		"123"
		}
	}
	"1"
	{
		"path"		"D:\\SteamLibrary"
		"apps"
		{
			"294100"		"456"
		}
	}
}
'''


def test_vdf_library_with_rimworld_comes_first():
    assert parse_vdf(VDF)["libraryfolders"]["1"]["path"] == r"D:\SteamLibrary"
    assert library_with_app(VDF) == [Path(r"D:\SteamLibrary"), Path(r"C:\Program Files (x86)\Steam")]


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "Steam Library ö" / "steamapps" / "common" / "RimWorld"
    write_mod(root / "Data" / "Core", "Ludeon.RimWorld")
    write_mod(root / "Data" / "Biotech", "Ludeon.RimWorld.Biotech")
    write_mod(root / "Mods" / "Local Copy", "Author.Shared")
    write_mod(root / "Mods" / "Second Copy", "author.shared")
    (root / "Mods" / "No About" / "About").mkdir(parents=True)
    write_mod(root / "Mods" / "Needs Lib", "author.needslib", deps=["author.lib"])
    write_mod(root / "Mods" / "rimbisect-probe", "rimbisect.probe")
    workshop = tmp_path / "Steam Library ö" / "steamapps" / "workshop" / "content" / "294100"
    write_mod(workshop / "1111111111", "author.shared")
    write_mod(workshop / "2222222222", "author.lib")
    return Game(root, "1.6.4871 rev590")


def test_scan_follows_the_games_duplicate_rules(game):
    warnings = []
    mods = scan_mods(game, warnings)
    assert mods["author.shared"].folder.name == "Local Copy"
    assert mods["author.shared_steam"].workshop_id == "1111111111"
    assert mods["author.lib"].workshop_id == "2222222222"
    assert mods[CORE].official
    assert PROBE_ID not in mods
    joined = "\n".join(warnings)
    assert "installed twice" in joined
    assert "both a local mod" in joined
    assert "No About" in joined


def write_config(path: Path, active, expansions=()) -> Path:
    li = lambda values: "".join(f"<li>{v}</li>" for v in values)  # noqa: E731
    path.write_text(f"<ModsConfigData><version>1.6.4871 rev590</version><activeMods>{li(active)}</activeMods>"
                    f"<knownExpansions>{li(expansions)}</knownExpansions></ModsConfigData>", encoding="utf-8")
    return path


def test_load_order_keeps_spelling_and_skips_missing(game, tmp_path):
    config = read_mods_config(write_config(tmp_path / "ModsConfig.xml",
                                           ["Ludeon.RimWorld", "Ludeon.RimWorld.Biotech", "Author.Shared",
                                            "author.needslib", "not.installed"], ["ludeon.rimworld.biotech"]))
    warnings = []
    order = LoadOrder.build(config, scan_mods(game, []), warnings)
    assert order.order == [CORE, "ludeon.rimworld.biotech", "author.shared", "author.needslib"]
    assert order.official() == [CORE, "ludeon.rimworld.biotech"]
    assert any("not.installed" in w for w in warnings)
    assert any("author.lib" in w and "not in the active list" in w for w in warnings)
    # A dependency that is not active is never invented.
    assert order.trial_list(["author.needslib"]) == ["author.needslib", PROBE_ID]
    xml = order.fixed_config(["author.shared"])
    assert "<li>Author.Shared</li>" not in xml and "<li>Ludeon.RimWorld</li>" in xml
    assert "<li>ludeon.rimworld.biotech</li>" in xml  # knownExpansions untouched


def test_config_errors(tmp_path):
    with pytest.raises(UserError, match="no ModsConfig.xml"):
        read_mods_config(tmp_path / "missing.xml")
    bad = tmp_path / "bad.xml"
    bad.write_text("<Prefs><x>1</x></Prefs>")
    with pytest.raises(UserError, match="activeMods"):
        read_mods_config(bad)
    config = read_mods_config(write_config(tmp_path / "no-core.xml", ["author.shared"]))
    with pytest.raises(UserError, match="Core"):
        LoadOrder.build(config, {}, [])


def test_closure_and_dependents():
    order = make_order(8, {"m03": ["m01"], "m05": ["m03"], "m06": ["m01"]})
    assert order.closure({"m05"}) == {"m05", "m03", "m01"}
    assert order.dependents(["m01"]) == ["m03", "m05", "m06"]
    assert order.dependents(["m03"]) == ["m05"]
    assert order.trial_list({"m05", CORE}) == [CORE, "m01", "m03", "m05", PROBE_ID]


def test_alternative_dependency_satisfied_by_either():
    from rimbisect.about import Dependency

    order = make_order(4)
    order.mods["m03"].dependencies = [Dependency("m00", ("m01",))]
    # Both active alternatives come along, so the closure of a union is the union of closures.
    assert order.closure({"m03"}) == {"m03", "m00", "m01"}
    assert order.dependents(["m00"]) == []
    assert order.dependents(["m00", "m01"]) == ["m03"]


def test_declared_incompatibilities_are_warned_about():
    order = make_order(3)
    order.mods["m02"].incompatible_with = ["m00", "not.active"]
    warnings = []
    LoadOrder.build(order.config, order.mods, warnings)
    assert warnings == ["Mod m02 (m02) says it is incompatible with Mod m00 (m00), and both are active"]


def test_steam_copy_satisfies_a_dependency_like_in_the_game():
    from rimbisect.about import Dependency, Mod

    order = make_order(3)
    mods = dict(order.mods)
    mods["lib_steam"] = Mod("lib_steam", "Lib", Path("workshop/1"), workshop_id="1")
    mods["lib"] = Mod("lib", "Lib", Path("Mods/lib"))
    mods["m02"].dependencies = [Dependency("lib")]
    warnings = []
    config = ModsConfig("1.6", [CORE, "m00", "m01", "lib_steam", "m02"], [])
    order = LoadOrder.build(config, mods, warnings)
    assert not any("needs" in w for w in warnings)
    assert order.closure({"m02"}) == {"m02", "lib_steam"}
    assert order.dependents(["lib_steam"]) == ["m02"]
