"""Narrow a failing mod list down to the mods the failure needs.

Every question is "does the error reproduce with these mods loaded?", answered by one
trial on the player's order filtered to the dependency closure of the set. Answers are
cached by the exact set of packageIds a trial loaded.

The search splits candidates into load-order halves. If a half fails on its own, it
recurses there. If the first half passes, the second half is assumed to fail and the
first trial inside it confirms or refutes that, which saves a trial per level for the
common single-culprit case. Never two assumptions in a row. If both halves pass alone,
the failure needs mods from both: it finds the needed part of the second half with all
of the first loaded, then the needed part of the first with only that loaded. The same
recursion handles three or more mods.

Once a cause is found, it is run alone to confirm it, and the list without it is run
once more. If that still fails, there is a second, independent cause, and the search goes
on in what is left.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .modlist import LoadOrder
from .signature import Criterion
from .trial import CRASH, FAIL, UNRESOLVED, Launcher, Trial


class _Unconfirmed(Exception):
    """An assumed-failing half turned out not to fail on its own."""


class Inconclusive(Exception):
    """The search cannot go on: a mod list gave no answer twice (a timeout, or mods the game
    did not load), or the mods it narrowed down to do not show the error on their own."""

    def __init__(self, message: str, advice: str, trial: Trial | None = None):
        super().__init__(message)
        self.advice = advice
        self.trial = trial


def run_answered(launcher: Launcher, mods: list[str], label: str, criterion: Criterion | None,
                 crash_is_fail: bool, log: Callable[[str], None]) -> Trial:
    """One trial, run again if it gave no answer, or crashed before the error could show.

    Big mod lists crash now and then for no reason that repeats; a crash that does repeat
    counts as not showing the error. Logs every trial but the one it returns.
    """
    outcomes: list[str] = []
    while True:
        trial = launcher.run(mods, label, criterion)
        outcomes.append(trial.outcome)
        if trial.outcome == UNRESOLVED:
            label = "no answer, again"
        elif trial.outcome == CRASH and not crash_is_fail and outcomes.count(CRASH) == 1:
            label = "crashed, again"
        else:
            return trial
        log(trial.line())
        if outcomes.count(UNRESOLVED) == 2:
            reason = trial.excerpt.splitlines()[0] if trial.excerpt else "no result"
            raise Inconclusive(f"trial {trial.number} gave no answer twice on the same {trial.mod_count} mods "
                               f"({reason})", f"Its mod list is in report.json and the game's log in {trial.log}; "
                               "if it timed out, a longer --timeout may get through", trial)


class Search:
    def __init__(self, order: LoadOrder, launcher: Launcher, criterion: Criterion, keep: Iterable[str],
                 repeats: int = 1, crash_is_fail: bool = False, log: Callable[[str], None] = print):
        self.order = order
        self.launcher = launcher
        self.criterion = criterion
        self.keep = set(keep)
        self.repeats = max(1, repeats)
        self.crash_is_fail = crash_is_fail
        self.log = log
        self.cache: dict[frozenset[str], bool] = {}
        self.seeded: set[frozenset[str]] = set()
        self.flaky: list[dict] = []
        self.saw_pass = False
        self.base_fails = False
        self.groups: list[set[str]] = []
        # Mods taken out of the search, which no trial may load as an alternative dependency.
        self.excluded: set[str] = set()

    def _mods(self, ids: Iterable[str]) -> list[str]:
        return self.order.trial_list(set(ids) | self.keep, self.excluded)

    def _key(self, ids: Iterable[str]) -> frozenset[str]:
        return frozenset(self._mods(ids))

    def record(self, ids: Iterable[str], failed: bool) -> None:
        """Seed the cache with a result obtained elsewhere (the baseline)."""
        self.cache[self._key(ids)] = failed
        self.seeded.add(self._key(ids))
        self.saw_pass |= not failed

    def known(self, ids: Iterable[str]) -> bool | None:
        return self.cache.get(self._key(ids))

    def fails(self, ids: Iterable[str], label: str = "bisect") -> bool:
        mods = self._mods(ids)
        key = frozenset(mods)
        if key in self.cache:
            return self.cache[key]
        outcomes: list[bool] = []
        runs: list[Trial] = []
        # A reproduced error is conclusive; a pass is only trusted after every repeat.
        while len(outcomes) < self.repeats and not any(outcomes):
            trial = self._run(mods, label)
            failed = trial.outcome == FAIL or (self.crash_is_fail and trial.outcome == CRASH)
            outcomes.append(failed)
            runs.append(trial)
        failed = any(outcomes)
        if len(set(outcomes)) > 1:
            self.flaky.append({"mods": runs[0].mod_count, "trials": [t.number for t in runs],
                               "outcomes": [t.outcome for t in runs]})
            self.log(f"           flaky: the same {runs[0].mod_count} mods passed and then failed")
        self.cache[key] = failed
        self.saw_pass |= not failed
        return failed

    def _run(self, mods: list[str], label: str) -> Trial:
        trial = run_answered(self.launcher, mods, label, self.criterion, self.crash_is_fail, self.log)
        self.log(trial.line())
        return trial

    def find(self, candidates: list[str], fixed: set[str] | None = None, verified: bool = True) -> set[str]:
        """A small subset M of candidates with fails(fixed | M).

        Requires fails(fixed | candidates) (when verified) and not fails(fixed).
        """
        fixed = set(fixed or ())
        if len(candidates) == 1:
            if not verified and not self.fails(fixed | set(candidates)):
                raise _Unconfirmed
            return self._through_dependencies(candidates[0], fixed)
        half = len(candidates) // 2
        first, second = candidates[:half], candidates[half:]
        if self.fails(fixed | set(first)):
            return self.find(first, fixed)
        second_fails = self.known(fixed | set(second))
        if second_fails is None and verified:
            try:
                return self.find(second, fixed, verified=False)
            except _Unconfirmed:
                second_fails = self.known(fixed | set(second))
        if second_fails is None:
            second_fails = self.fails(fixed | set(second))
        if second_fails:
            return self.find(second, fixed)
        if not verified:
            raise _Unconfirmed
        needed_second = self.find(second, fixed | set(first))
        needed_first = self.find(first, fixed | needed_second)
        return needed_first | needed_second

    def _through_dependencies(self, pid: str, fixed: set[str]) -> set[str]:
        """A culprit that only failed together with the dependencies it pulled in may be
        innocent: check whether those dependencies fail without it."""
        already = self.order.closure(fixed | self.keep, self.excluded)
        pulled = [d for d in self.order.order if d in self.order.closure({pid}, self.excluded) - already - {pid}]
        # Dependencies that need pid back (a cycle) cannot be tried without it.
        if pulled and pid not in self.order.closure(pulled, self.excluded) \
                and self.fails(fixed | set(pulled), "dependency check"):
            return self.find(pulled, fixed)
        return {pid}

    def locate(self, candidates: list[str], changed: list[str] | None = None) -> set[str]:
        """Culprits among candidates; an empty set means the base list alone fails."""
        found: set[str] | None = None
        if changed:
            # Dropping a changed mod drops whatever depends on it too.
            removed = set(changed) | set(self.order.dependents(changed))
            unchanged = [c for c in candidates if c not in removed]
            recent = [c for c in candidates if c in removed]
            self.excluded |= removed
            without = self.fails(unchanged, "without changed mods")
            if without:
                self.log(f"the error reproduces without the {len(recent)} changed mods; searching the rest")
                found = self.find(unchanged) if unchanged else set()
            self.excluded -= removed
            # With the changed mods allowed again, one of them can come back in as an
            # alternative dependency of an unchanged mod; then the split does not hold.
            if not without and not self.fails(unchanged, "without changed mods"):
                self.log(f"the error needs at least one of the {len(recent)} changed mods")
                found = self.find(recent, set(unchanged))
                if unchanged and not self.fails(found, "changed mods alone"):
                    found |= self.find(unchanged, found)
        if found is None:
            found = self.find(candidates) if candidates else set()
        if not self.saw_pass and self.fails(set(), "base game only"):
            return set()
        if found and self._key(found) in self.seeded:
            # Every mod is needed, which rests on the baseline alone; see that it fails again.
            del self.cache[self._key(found)]
            self.seeded.discard(self._key(found))
        if found and not self.fails(found, "culprits alone"):
            names = self.order.written([pid for pid in self.order.order if pid in found])
            raise Inconclusive(f"the mods the search narrowed down to ({', '.join(names[:5])}"
                               f"{', ...' if len(names) > 5 else ''}) do not show the error on their own",
                               "The error may not show up every time; --repeats 3 runs each passing list "
                               "up to three times")
        return found

    def locate_all(self, candidates: list[str], changed: list[str] | None = None) -> list[set[str]]:
        """Every independent cause among candidates, as one set of culprits each. When the
        kept mods alone fail, base_fails is set and the causes found before that are returned."""
        groups = self.groups
        while True:
            found = self.locate(candidates, changed)
            if not found:
                self.base_fails = True
                return groups
            groups.append(found)
            self.excluded |= found
            self.excluded |= set(self.order.dependents(self.excluded))
            candidates = [c for c in candidates if c not in self.excluded]
            if not self.fails(candidates, "without the culprits"):
                return groups
            self.log(f"the error still happens without {', '.join(sorted(found))}; searching the rest")
            changed = None
