"""Stage the acceptance test: a mod list of N mods with a broken test mod at position P.

    python acceptance/stage.py install --source <ModsConfig.xml> --size 200 --position 150
    rimbisect bisect --config acceptance/work/ModsConfig-brokendef-200.xml --match rimbisectNoSuchField
    python acceptance/stage.py remove

install copies the test mod into <game>/Mods and writes the trimmed mod list to
acceptance/work. The source mod list is only read. The test mods:

    brokendef  a def with a field that does not exist, logged as an XML error
    mapfail    throws while the starting map is generated (bisect with --crash-is-fail)
    flood      logs 10,001 warnings while mods load, which switches the game's logging off,
               then 10,001 more at startup followed by the error rimbisectLateFloodError.
               Stage it into a brokendef list (--source) to check both errors are still seen.
    savefail   logs an error when a save is loaded, never in a new colony (bisect with --save)
    framefail  makes every frame throw once the map is up, before the probe's update runs
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rimbisect.install import default_config_dir, find_game  # noqa: E402
from rimbisect.modlist import CORE, LoadOrder, read_mods_config, scan_mods  # noqa: E402

HERE = Path(__file__).resolve().parent
TEST_MODS = ("brokendef", "mapfail", "flood", "savefail", "framefail")


def install(args) -> None:
    game = find_game(args.game)
    source = args.source or default_config_dir() / "ModsConfig.xml"
    config = read_mods_config(source)
    warnings: list[str] = []
    order = LoadOrder.build(config, scan_mods(game, warnings), warnings)
    kept = order.order[: args.size - 1]
    if len(kept) < args.size - 1:
        sys.exit(f"{source} has only {len(order.order)} installed active mods")
    if args.position <= kept.index(CORE) + 1:
        sys.exit("the test mod has to load after Core")
    test_id = f"rimbisect.acceptance.{args.mod}"
    active = order.written(kept)
    active.insert(args.position - 1, test_id)
    work = HERE / "work"
    work.mkdir(exist_ok=True)
    out = work / f"ModsConfig-{args.mod}-{args.size}.xml"
    out.write_text(config.to_xml(active), encoding="utf-8")
    target = game.mods_dir / f"rimbisect-acceptance-{args.mod}"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(HERE / args.mod, target)
    print(f"wrote {out} ({len(active)} mods, {test_id} at position {args.position})")
    print(f"installed the test mod at {target}")


def remove(args) -> None:
    mods_dir = find_game(args.game).mods_dir
    for name in TEST_MODS:
        target = mods_dir / f"rimbisect-acceptance-{name}"
        if target.exists():
            shutil.rmtree(target)
            print(f"removed {target}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "remove"])
    parser.add_argument("--mod", choices=TEST_MODS, default="brokendef")
    parser.add_argument("--game", type=Path)
    parser.add_argument("--source", type=Path, help="mod list to take the mods from")
    parser.add_argument("--size", type=int, default=200)
    parser.add_argument("--position", type=int, default=150)
    args = parser.parse_args()
    install(args) if args.action == "install" else remove(args)


if __name__ == "__main__":
    main()
