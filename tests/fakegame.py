"""A stand-in for the game: a rule decides whether a mod list reproduces the error."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rimbisect.about import Dependency, Mod
from rimbisect.modlist import CORE, PROBE_ID, LoadOrder, ModsConfig
from rimbisect.signature import Criterion, signature_of
from rimbisect.trial import CRASH, FAIL, PASS, UNRESOLVED, Trial

ERROR = "Could not resolve cross-reference to Verse.ThingDef named Widget_12 (wanter=thingDef)"
CRITERION = Criterion(signature=signature_of(ERROR))


def write_mod(folder: Path, package_id: str, deps=()) -> Path:
    """A mod folder with a minimal About/About.xml."""
    (folder / "About").mkdir(parents=True)
    dep_xml = "".join(f"<li><packageId>{d}</packageId></li>" for d in deps)
    (folder / "About" / "About.xml").write_text(
        f"<ModMetaData><name>Mod {package_id}</name><packageId>{package_id}</packageId>"
        f"<modDependencies>{dep_xml}</modDependencies></ModMetaData>", encoding="utf-8")
    return folder


def make_order(n: int = 64, deps: dict[str, list[str]] | None = None, dlcs=()) -> LoadOrder:
    deps = deps or {}
    ids = [f"m{i:02d}" for i in range(n)]
    mods = {CORE: Mod(CORE, "Core", Path("Data/Core"), official=True)}
    for dlc in dlcs:
        mods[dlc] = Mod(dlc, dlc, Path("Data") / dlc, official=True)
    for pid in ids:
        # "a|b" is a dependency on a with b as an alternative.
        mods[pid] = Mod(pid, f"Mod {pid}", Path("Mods") / pid,
                        dependencies=[Dependency(*_split(d)) for d in deps.get(pid, [])])
    config = ModsConfig("1.6.4871 rev590", [CORE, *dlcs, *ids], list(dlcs))
    return LoadOrder.build(config, mods, [])


def _split(dep: str) -> tuple[str, tuple[str, ...]]:
    first, *rest = dep.split("|")
    return first, tuple(rest)


class FakeGame:
    """Logs ERROR whenever rule(loaded mods) is true, and crashes or times out when it
    returns CRASH or UNRESOLVED.
    With an order, it also checks that every trial list is dependency-safe and in the
    player's order."""

    def __init__(self, order: LoadOrder | None, rule: Callable[[set[str]], bool | str]):
        self.order = order
        self.rule = rule
        self.runs: list[list[str]] = []
        self.trials: list[Trial] = []
        self.numbered = 0
        self.not_loaded_at_first: set[str] | None = None

    def run(self, mods, label, criterion) -> Trial:
        assert mods[-1] == PROBE_ID, "the probe must load last"
        loaded = set(mods)
        if self.order is not None:
            for pid in mods[:-1]:
                for dep in self.order.mods[pid].dependencies:
                    assert {dep.package_id, *dep.alternatives} & loaded, f"{pid} loaded without {dep.package_id}"
            positions = [self.order.order.index(pid) for pid in mods[:-1]]
            assert positions == sorted(positions), "trial lists must keep the player's order"
        self.runs.append(list(mods))
        hit = self.rule(loaded)
        errors = [(ERROR, 1)] if hit and hit != UNRESOLVED else []
        failed = criterion is not None and any(criterion.error_matches(text) for text, _ in errors)
        outcome = hit if hit in (CRASH, UNRESOLVED) else FAIL if failed else PASS
        self.numbered += 1
        trial = Trial(self.numbered, label, list(mods), outcome, 1.0, errors=errors)
        self.trials.append(trial)
        return trial

    def stop(self) -> None:
        pass
