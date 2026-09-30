# Changelog

## 0.2.0 - 2026-09-30

- `rimbisect.exe`, a single file on the release page that needs no Python. Double-click
  it and it asks what it needs; from a terminal it takes the same commands as before.
- `rimbisect resume` goes on with a run that was stopped or crashed. Trials that already
  gave an answer are not run again. A double-click offers to resume an unfinished run.
- `--save NAME` loads a copy of one of your saves instead of starting a new colony, for
  errors that only show up in your game.
- When picking the error, exceptions are listed first, and typing a piece of an error's
  text finds it among all of them.
- The probe runs the settle time at the game's fastest speed, so passing trials are
  shorter.
- A native crash's excerpt in the report no longer shows Mono's empty banner lines.

## 0.1.0 - 2026-09-30

First release.

- `rimbisect bisect` finds the mod, or the set of mods that only fail together, behind
  an error you pick from the log, a piece of text, a regex, a slow start or a crash.
- Trials keep dependencies loaded and your load order intact, and run on a throwaway
  copy of your Config folder, so your mod list, settings and saves are never written.
- A confirm run without the culprit finds separate causes of the same error.
- `rimbisect check` saves a known good list; the next bisect tries recently changed
  mods first.
- `rimbisect mods` shows the mod list with missing dependencies and incompatibilities.

See the README for how it works and what it can't do.
