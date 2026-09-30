"""Trials the search needs for N mods, measured on the FakeGame from the test suite.

    python acceptance/simulate.py 400

Counts search trials only, including the one that checks nothing else fails; a real run
adds the baseline.
"""

from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from fakegame import CRITERION, FakeGame, make_order  # noqa: E402
from rimbisect.modlist import CORE  # noqa: E402
from rimbisect.search import Search  # noqa: E402


def trials(n: int, culprits: set[str], deps: dict) -> int:
    order = make_order(n, deps)
    game = FakeGame(order, lambda mods: culprits <= mods)
    search = Search(order, game, CRITERION, keep={CORE}, log=lambda _: None)
    candidates = order.order[1:]
    search.record(candidates, True)
    causes = search.locate_all(candidates)
    assert causes == [culprits], (causes, culprits)
    return len(game.runs)


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    rng = random.Random(294100)
    ids = [f"m{i:02d}" for i in range(n)]
    # Roughly the shape of a real list: a few libraries early on that many mods need.
    deps = {pid: [ids[rng.randrange(0, 5)]] for pid in ids[5:] if rng.random() < 0.3}
    libraries = {d for ds in deps.values() for d in ds}
    plain = [pid for pid in ids if pid not in libraries]
    singles = [trials(n, {pid}, deps) for pid in plain]
    pairs = [trials(n, set(rng.sample(plain, 2)), deps) for _ in range(300)]
    for label, counts in (("one culprit", singles), ("two mods together", pairs)):
        print(f"{n} mods, {label}: mean {statistics.mean(counts):.1f}, "
              f"median {statistics.median(counts):.0f}, max {max(counts)} trials ({len(counts)} cases)")


if __name__ == "__main__":
    main()
