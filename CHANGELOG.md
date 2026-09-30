# Changelog

All notable changes to rimbisect are recorded here. This project follows
[Semantic Versioning](https://semver.org/).

## 0.1.0 - 2026-09-30

First release.

- `rimbisect bisect` runs the game on dependency-safe subsets of your mod list, in your
  load order, and narrows down the mod or combination of mods behind an error. It hunts
  an error you pick from the full list's log, a piece of text (`--match-text`), a regex
  (`--match`), a slow start (`--slower-than`) or a crash (`--crash-is-fail`).
- Interactions are found too: when neither half fails alone it finds the part of each
  half the error needs, so two or three mods that only break together come out as a set.
- After a culprit is found the list is run once more without it. If the error is still
  there the search goes on, and every separate cause is reported on its own.
- Errors are compared with numbers, ids and quoted names masked, and with Harmony's
  renamed stack frames mapped back to the original method.
- Mods changed since the last good run (`rimbisect check`) or since `--since DATE` are
  tried first. `--keep` mods load in every trial along with what they need.
- Every trial runs on a fresh copy of your Config folder and HugsLib's settings with
  `-savedatafolder`, so your real mod list, settings and saves are never written and no
  trial sees what an earlier one changed. The copy is deleted when the run ends.
- A small probe mod, installed for the run and removed afterwards, reports errors, the
  mods the game actually loaded, map-ready and done, and quits the game after `--settle`
  seconds of game time. Windows that pause a new game (HugsLib's news, mod feature
  popups) are closed so that game time actually passes.
- When the game gives up on a trial (map generation threw, or loading failed and it fell
  back to Core alone) the trial ends as a crash right away, instead of waiting for the
  timeout or passing on a vanilla game.
- RimWorld stops logging after 10,000 messages. The probe keeps the count from getting
  there and switches logging back on if it was reached before the probe loaded.
- A trial that times out or does not get all its mods loaded is run again once; twice on
  the same list stops the run as inconclusive instead of guessing. A trial that crashes
  while hunting some other error is run again once too before it counts as a pass.
- The game runs in a Windows job object, so it closes with rimbisect even if rimbisect
  is killed. Only processes rimbisect started are ever stopped.
- `report.txt`, `report.json` and a `ModsConfig.fixed.xml` without the culprits (and the
  mods that need them) in every run folder. Culprits come with their Workshop link and a
  warning when they don't list your game version.
- `rimbisect mods` shows the mod list the way rimbisect reads it, with warnings for
  missing dependencies, incompatible pairs, duplicate packageIds and folders without
  About.xml.
