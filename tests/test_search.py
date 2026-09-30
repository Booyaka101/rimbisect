import itertools

import pytest

from fakegame import CRITERION, FakeGame, make_order
from rimbisect.modlist import CORE
from rimbisect.search import Inconclusive, Search
from rimbisect.trial import CRASH, UNRESOLVED

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


def test_alternative_dependencies_do_not_mislead_the_search():
    # m10 needs m06 or m08, m08 needs m05. Pulling in only the first active alternative
    # made {m05, m10} look like it failed on its own, because it also loaded m06. Like a
    # hard dependency, m06 comes along with m10 and is not blamed.
    found, _ = search_for(lambda mods: {"m06", "m10"} <= mods, deps={"m10": ["m06|m08"], "m08": ["m05"]})
    assert found == {"m10"}


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


def locate_all(rule, n=64, deps=DEPS, changed=None):
    order = make_order(n, deps)
    game = FakeGame(order, rule)
    search = Search(order, game, CRITERION, keep={CORE}, log=lambda _: None)
    search.record(order.order[1:], True)
    return search.locate_all(order.order[1:], changed), search, game


def test_a_second_cause_among_the_changed_mods_is_found():
    causes, _, _ = locate_all(lambda mods: "m03" in mods or "m12" in mods, n=16, deps={}, changed=["m12"])
    assert causes == [{"m03"}, {"m12"}]


def test_a_changed_alternative_dependency_does_not_frame_an_unchanged_mod():
    found, _ = search_for(lambda mods: "m12" in mods, n=16, deps={"m04": ["m12|m13"]}, changed=["m11", "m12"])
    assert found == {"m12"}


@pytest.mark.parametrize("culprit", ["m01", "m02"])
def test_mods_that_need_each_other(culprit):
    causes, _, _ = locate_all(lambda mods: culprit in mods, n=6, deps={"m01": ["m02"], "m02": ["m01"]})
    assert len(causes) == 1 and causes[0] <= {"m01", "m02"}


def test_an_error_that_does_not_show_again_is_not_blamed_on_the_mods_left():
    with pytest.raises(Inconclusive) as info:
        locate_all(lambda mods: False, n=16)
    assert "do not show the error on their own" in str(info.value)
    assert "--repeats" in info.value.advice


def test_a_culprit_that_is_also_an_alternative_is_not_loaded_again():
    # m10 needs m06 or m08. Without m06, m10 still loads on m08, and m06 must not come
    # back as its alternative, or the search finds m06 again forever.
    causes, _, game = locate_all(lambda mods: "m06" in mods, deps={"m10": ["m06|m08"]})
    assert causes == [{"m06"}]
    assert "m06" not in game.runs[-1] and {"m08", "m10"} <= set(game.runs[-1])


def test_changed_mods_left_out_stay_out_as_alternatives():
    found, game = search_for(lambda mods: "m33" in mods, deps={"m10": ["m06|m08"]}, changed=["m06"])
    assert found == {"m33"}
    assert game.trials[0].label == "without changed mods"
    assert not any("m06" in run for run in game.runs)


def test_one_cause_is_confirmed_with_one_more_trial():
    causes, _, game = locate_all(lambda mods: "m33" in mods)
    assert causes == [{"m33"}]
    assert game.trials[-1].label == "without the culprits" and "m33" not in game.runs[-1]


def test_independent_causes_are_all_found():
    causes, _, _ = locate_all(lambda mods: "m20" in mods or {"m41", "m50"} <= mods)
    assert sorted(map(sorted, causes)) == [["m20"], ["m41", "m50"]]


def test_removing_a_culprit_removes_its_dependents_before_confirming():
    causes, _, game = locate_all(lambda mods: "m05" in mods)
    assert causes == [{"m05"}]
    assert not {"m05", "m10"} & set(game.runs[-1])


@pytest.mark.parametrize("first, label", [([UNRESOLVED], "no answer, again"), ([CRASH], "crashed, again"),
                                          ([UNRESOLVED, CRASH], "crashed, again")])
def test_a_timeout_or_a_crash_is_run_again(first, label):
    seen = []

    def rule(mods):
        seen.append(frozenset(mods))
        runs = seen.count(frozenset(mods))
        if runs <= len(first) and len(mods) < 40:
            return first[runs - 1]
        return "m33" in mods

    causes, _, game = locate_all(rule)
    assert causes == [{"m33"}]
    assert any(t.label == label for t in game.trials)


def test_timing_out_twice_on_the_same_list_stops_the_search():
    with pytest.raises(Inconclusive) as info:
        locate_all(lambda mods: UNRESOLVED if len(mods) < 40 else "m33" in mods)
    assert info.value.trial.outcome == UNRESOLVED
    assert "gave no answer twice" in str(info.value)


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
