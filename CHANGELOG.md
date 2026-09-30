# Changelog

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
