import itertools

import pytest

from fakegame import CRITERION, FakeGame, make_order
from rimbisect.modlist import CORE
from rimbisect.search import Search

DEPS = {"m10": ["m05"]}


def search_for(rule, n=64, deps=DEPS, changed=None, **kwargs):
    order = make_order(n, deps)
    game = FakeGame(order, rule)
    search = Search(order, game, CRITERION, keep={CORE}, log=lambda _: None, **kwargs)
    candidates = [pid for pid in order.order if pid != CORE]
    search.record(candidates, True)  # the baseline
    return search.locate(candidates, changed), game


def test_single_culprit_m33_in_seven_trials():
    found, game = search_for(lambda mods: "m33" in mods)
    assert found == {"m33"}
    assert len(game.runs) <= 7


@pytest.mark.parametrize("culprit", [f"m{i:02d}" for i in range(64) if i not in (5, 10)])
def test_every_single_culprit_is_found(culprit):
    found, game = search_for(lambda mods: culprit in mods)
    assert found == {culprit}
    assert len(game.runs) <= 9


def test_pair_m12_m40_in_twenty_trials():
    found, game = search_for(lambda mods: {"m12", "m40"} <= mods)
    assert found == {"m12", "m40"}
    assert len(game.runs) <= 20


def test_every_pair_is_found():
    worst = 0
    for a, b in itertools.combinations(range(0, 64, 3), 2):
        pair = {f"m{a:02d}", f"m{b:02d}"}
        if pair & {"m05", "m10"}:
            continue
        found, game = search_for(lambda mods, pair=pair: pair <= mods)
        assert found == pair
        worst = max(worst, len(game.runs))
    assert worst <= 22


def test_three_way_interaction():
    trio = {"m03", "m30", "m60"}
    found, _ = search_for(lambda mods: trio <= mods)
    assert found == trio


def test_dependency_is_blamed_not_its_dependent():
    # m10 depends on m05; if m05 is the culprit, any half holding m10 also fails.
    found, _ = search_for(lambda mods: "m05" in mods)
    assert found == {"m05"}


def test_dependent_culprit_keeps_its_dependency_loaded():
    found, game = search_for(lambda mods: "m10" in mods)
    assert found == {"m10"}
    assert all("m05" in run for run in game.runs if "m10" in run)


def test_base_game_alone_failing_blames_no_mod():
    found, game = search_for(lambda mods: True)
    assert found == set()
    assert game.runs[-1] == [CORE, "rimbisect.probe"]


def test_changed_mods_are_tried_first():
    found, game = search_for(lambda mods: "m50" in mods, changed=["m20", "m50", "m51"])
    assert found == {"m50"}
    assert len(game.runs) <= 4


def test_changed_mod_interacting_with_an_unchanged_one():
    found, _ = search_for(lambda mods: {"m07", "m50"} <= mods, changed=["m50", "m51"])
    assert found == {"m07", "m50"}


def test_changed_mod_not_involved():
    found, _ = search_for(lambda mods: "m33" in mods, changed=["m50"])
    assert found == {"m33"}


def test_removing_a_changed_dependency_drops_its_dependents():
    found, _ = search_for(lambda mods: "m10" in mods, changed=["m05"])
    assert found == {"m10"}


def test_results_are_cached_by_mod_set():
    found, game = search_for(lambda mods: "m33" in mods)
    keys = [frozenset(run) for run in game.runs]
    assert len(keys) == len(set(keys))


def test_flaky_trials_are_reported():
    calls = {"n": 0}

    def rule(mods):
        if "m06" not in mods:
            return False
        calls["n"] += 1
        return calls["n"] % 2 == 0  # m06 fails every other time

    order = make_order(8)
    search = Search(order, FakeGame(order, rule), CRITERION, keep={CORE}, repeats=3, log=lambda _: None)
    search.record(order.order[1:], True)
    assert search.locate(order.order[1:]) == {"m06"}
    assert search.flaky
