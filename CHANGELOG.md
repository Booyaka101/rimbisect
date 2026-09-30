# Changelog

All notable changes to rimbisect are recorded here. This project follows
[Semantic Versioning](https://semver.org/).

## 0.1.0 - 2026-09-30

First release.

- `rimbisect bisect` runs the game on dependency-safe subsets of your mod list, in your
  load order, and narrows down the mod or combination of mods behind an error. It hunts
  an error you pick from the full list's log, a regex (`--match`), a slow start
  (`--slower-than`) or a crash (`--crash-is-fail`).
- Interactions are found too: when neither half fails alone it finds the part of each
  half the error needs, so two or three mods that only break together come out as a set.
- Mods changed since the last good run (`rimbisect check`) or since `--since DATE` are
  tried first.
- Every trial runs on a copy of your Config folder with `-savedatafolder`, so your real
  mod list, settings and saves are never written. A small probe mod, installed for the
  run and removed afterwards, reports errors, map-ready and done, and quits the game.
  When the game gives up on a trial (map generation threw, or loading failed and it fell
  back to Core alone) the probe says so and the trial ends as a crash right away, instead
  of waiting for the timeout or passing on a vanilla game.
- `report.txt`, `report.json` and a `ModsConfig.fixed.xml` without the culprits (and the
  mods that need them) in every run folder.
- `rimbisect mods` shows the mod list the way rimbisect reads it, with warnings for
  missing dependencies, duplicate packageIds and folders without About.xml.
