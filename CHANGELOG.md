# Changelog

## 0.2.2 - 2026-09-30

A second review pass. Runs started by 0.2.1 can't be resumed with 0.2.2.

Search:

- When the error showed up once and then not again, a search that went on after the
  first cause, or after the list without the changed mods, trusted that one failure and
  could run for hundreds of trials naming innocent mods. Each search now checks the list
  it starts from again and stops as inconclusive when it passes.
- Cutting a group of culprits down no longer trusts a pass it saw earlier; it runs that
  list again.
- `resume` refuses to lower `--repeats`, and refuses to try an inconclusive run again with
  options that would stop it the same way.

Trials:

- A mod that throws an error every frame kept the probe from ending the trial, which then
  waited out the whole `--timeout`. The probe now gives up after half a minute without an
  update and the trial counts as a crash.
- Files a mod writes into the save data folder, like its own settings folder, no longer
  carry over into the next trial.
- Closing the console window mid-trial no longer records that trial as a crash.

Mods:

- `About.xml` is read the way the game reads it: text that isn't valid UTF-8, tags in the
  wrong case, a file the game can't parse, a missing packageId and a mod folder without
  `About.xml` all give the mod the id the game uses, where rimbisect used to drop it and
  warn that it wasn't installed.
- A `_steam` entry left in the mod list after its local copy was removed counts as the
  Workshop copy, as in the game.
- Two Workshop copies of the same mod are reported as installed twice.

Guided mode and progress:

- Double-clicking the exe after an inconclusive run offers to try it again from where it
  stopped, with more repeats or a longer timeout depending on why it stopped.
- The culprits are named once more at the end, since the trial list pushes them out of
  view.
- The window title shows the trial count, and the terminal bell rings when a run longer
  than five minutes ends.

## 0.2.1 - 2026-09-30

A review pass over 0.2.0. Runs started by 0.2.0 can't be resumed with 0.2.1.

Search:

- An error that shows up only some of the time could send the search down the wrong
  half and blame mods that never fail. The full list runs again once the search
  narrows, and a pass there stops the run as inconclusive instead of 190 trials later.
- A group of mods is cut down to the ones it needs, so a mod kept in by a lucky pass
  isn't named.
- When every trial either failed or crashed, the check that the game fails without any
  mods was skipped and the search named a string of innocent mods. Crashes no longer
  count as a pass for that.

Save mode:

- A save no longer fails to load when dev mode was turned off for good in the game's
  options.
- When the game stays at the main menu instead of loading the save, the trial ends after
  about ten seconds as a crash instead of waiting out the timeout.
- Game time is counted for the whole game, not the current map, so a save that opens on
  the world view or has several maps settles too. Settle is at least five real seconds.

Errors:

- Signatures take the first stack frame outside .NET and Unity, so unrelated errors
  failing in the same dictionary lookup stay apart.
- RimWorld's "Duplicate stacktrace" lines join the error they repeat.
- The errors list in the report is numbered like the one `--pick` uses.

Runs:

- `resume` takes `--repeats`, `--timeout`, `--pick` and `--json`, and resuming an
  inconclusive run with `--repeats` or `--timeout` tries again from where it stopped.
- `resume` refuses a run started by another rimbisect version, and a save run whose save
  copy is gone.
- Closing the console window stops the game and removes the probe from `Mods`.
- A second rimbisect on the same game is refused.
- A run that was killed mid-write no longer leaves a broken `run.json` or trial line.
- The save copy is deleted when a run finishes, except an inconclusive one.
- Guided mode asks for the RimWorld folder when it can't find it, strips the quotes from
  a dragged path, shows how far an unfinished run got, and starts a new run when the old
  one can't go on.

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
