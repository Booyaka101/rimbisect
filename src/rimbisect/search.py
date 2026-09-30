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
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .modlist import LoadOrder
from .signature import Criterion
from .trial import CRASH, FAIL, Launcher, Trial


class _Unconfirmed(Exception):
    """An assumed-failing half turned out not to fail on its own."""


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
        self.flaky: list[dict] = []
        self.saw_pass = False

    def _key(self, ids: Iterable[str]) -> frozenset[str]:
        return frozenset(self.order.trial_list(set(ids) | self.keep))

    def record(self, ids: Iterable[str], failed: bool) -> None:
        """Seed the cache with a result obtained elsewhere (the baseline)."""
        self.cache[self._key(ids)] = failed
        self.saw_pass |= not failed

    def known(self, ids: Iterable[str]) -> bool | None:
        return self.cache.get(self._key(ids))

    def fails(self, ids: Iterable[str], label: str = "bisect") -> bool:
        mods = self.order.trial_list(set(ids) | self.keep)
        key = frozenset(mods)
        if key in self.cache:
            return self.cache[key]
        outcomes: list[bool] = []
        runs: list[Trial] = []
        # A reproduced error is conclusive; a pass is only trusted after every repeat.
        while len(outcomes) < self.repeats and not any(outcomes):
            trial = self.launcher.run(mods, label, self.criterion)
            failed = trial.outcome == FAIL or (self.crash_is_fail and trial.outcome == CRASH)
            outcomes.append(failed)
            runs.append(trial)
            self.log(trial.line())
        failed = any(outcomes)
        if len(set(outcomes)) > 1:
            self.flaky.append({"mods": runs[0].mod_count, "trials": [t.number for t in runs],
                               "outcomes": [t.outcome for t in runs]})
            self.log(f"           flaky: the same {runs[0].mod_count} mods passed and then failed")
        self.cache[key] = failed
        self.saw_pass |= not failed
        return failed

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
        already = self.order.closure(fixed | self.keep)
        pulled = [d for d in self.order.order if d in self.order.closure({pid}) - already - {pid}]
        if pulled and self.fails(fixed | set(pulled), "dependency check"):
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
            if self.fails(unchanged, "without changed mods"):
                self.log(f"the error reproduces without the {len(recent)} changed mods; searching the rest")
                candidates = unchanged
            else:
                self.log(f"the error needs at least one of the {len(recent)} changed mods")
                found = self.find(recent, set(unchanged))
                if not self.fails(found, "changed mods alone"):
                    found |= self.find(unchanged, found)
        if found is None:
            if not candidates:
                return set()
            found = self.find(candidates)
        if not self.saw_pass and self.fails(set(), "base game only"):
            return set()
        return found
