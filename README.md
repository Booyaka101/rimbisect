# rimbisect

Find the RimWorld mod, or combination of mods, behind an error, unattended.

You have 300 mods and a red error at startup, or a map that never finishes loading. The
usual fix is to disable half your list, start the game, see if the error is still there,
and repeat until you are down to one mod. That takes an evening and it is easy to get
wrong once dependencies are involved. rimbisect does the same halving for you. It
launches the game on dependency-safe parts of your mod list, reads each run's log,
closes the game and narrows down, until it can name the mod.

It never writes to your real mod list, settings or saves, and never reorders your mods.
The only thing it puts in the game folder is a small helper mod, `Mods\rimbisect-probe`,
which it deletes when the run ends.

## Install

You need Windows and RimWorld 1.6 from Steam.

The easy way is `rimbisect.exe` from the
[latest release](https://github.com/Booyaka101/rimbisect/releases/latest). It needs
nothing else installed. Double-click it and it asks what it needs to know, or run it
from a terminal with the commands below (in PowerShell, from its folder, that is
`.\rimbisect.exe bisect`). The exe isn't signed, so Windows SmartScreen may warn about it
the first time ("More info", then "Run anyway"). Smart App Control and some antivirus
programs block unsigned programs outright; the PyPI install below works around that.

With Python 3.11 or newer, install it from PyPI instead. If you don't have Python, get it
from [python.org](https://www.python.org/downloads/) and tick "Add python.exe to PATH" in
the installer. Then, in PowerShell:

```
py -m pip install rimbisect
```

If `rimbisect` is then "not recognized", Python's Scripts folder isn't on your PATH;
`py -m rimbisect` works the same everywhere. With pipx, `pipx install rimbisect` does it
in one go.

## Use it

Close RimWorld, make sure Steam is running (the game only loads Workshop mods through
it), and start a bisect:

```
rimbisect bisect
```

rimbisect finds your install through Steam and takes the mod list you have active in the
game. It first runs your full list once and shows the errors it logged, grouped and
numbered, exceptions first. Type the number of the one you want gone, or a piece of its
text, then leave it. (`--pick N` answers ahead of time, but the numbers can shift between
runs when errors come and go.) The game
opens and closes on its own for every trial; don't click in it, and don't use the PC for
games meanwhile. Expect 10 to 15 trials, each one a game start, or 20 to 30 when two
mods only fail together. A 200 mod list took 12
minutes on a fast PC; count on two to three times as long as your full list takes to load
into a map. The window title shows how far it has got, and the terminal bell rings when
a run that took more than five minutes ends. Ctrl+C stops the run at any point and closes
the game, and `rimbisect resume` goes on from there later without running the finished
trials again. It also picks up a run that died with the PC.

If you already know what the error says, skip the question. `--match-text` takes a piece
of the error as you see it in the log (`Player.log`, next to the `Config` folder
mentioned below); `--match` takes a regular expression:

```
rimbisect bisect --match-text "doesn't correspond to any field"
```

If the error only happens in your colony, give it a save. Every trial then loads a copy
of that save instead of starting a new colony:

```
rimbisect bisect --save "My colony"
```

The name is the one in the game's load menu, or a path to a `.rws` file. A save made with
mods that a trial leaves out usually still loads, with the usual errors about the missing
things. They don't confuse the search, which only looks for the error you picked. When a
trial's save doesn't load at all, that trial counts as a crash.

This is a real run on a 200 mod list with a test mod that has a broken def at position
150. It was made for testing with `--config`, which is why the paths are in a repo; yours
go to `%LOCALAPPDATA%\rimbisect\runs`.

```
rimbisect bisect --config acceptance\work\ModsConfig-200.xml --workdir acceptance\work\rb --match rimbisectNoSuchField
```

Progress goes to the terminal as it happens:

```
rimbisect 0.2.2: 200 active mods, 194 to search
run folder D:\Repos\ideas\rimbisect\acceptance\work\rb\runs\2026-09-30_12-34-49
trial   1  baseline                200 mods  FAIL         78.0s
the full list took 78s; expect about 11 more trials for a single culprit, 22 for two mods that only fail together
trial   2  bisect                  103 mods  PASS        114.4s
trial   3  bisect                   57 mods  FAIL          8.3s
trial   4  bisect                   31 mods  PASS         41.5s
trial   5  bisect                   23 mods  PASS         54.8s
trial   6  bisect                   20 mods  FAIL          5.1s
trial   7  bisect                   13 mods  PASS         34.7s
trial   8  bisect                   11 mods  PASS         35.7s
trial   9  bisect                   11 mods  FAIL          4.6s
trial  10  bisect                    9 mods  PASS         39.5s
trial  11  bisect                    7 mods  FAIL          2.8s
trial  12  without the culprits    199 mods  PASS        320.1s
```

and the report at the end:

```
rimbisect 0.2.2   found the mods that cause the error

game        D:\SteamLibrary\steamapps\common\RimWorld (1.6.4871 rev590)
mod list    D:\Repos\ideas\rimbisect\acceptance\work\ModsConfig-200.xml (200 active, 194 searched)
error       log matches /rimbisectNoSuchField/
run folder  D:\Repos\ideas\rimbisect\acceptance\work\rb\runs\2026-09-30_12-34-49

CULPRIT  rimbisect acceptance: broken def  (rimbisect.acceptance.brokendef)
  workshop     -
  folder       D:\SteamLibrary\steamapps\common\RimWorld\Mods\rimbisect-acceptance-brokendef
  modified     2026-09-30T09:23:26

Your mod list without 1 mod:
  D:\Repos\ideas\rimbisect\acceptance\work\rb\runs\2026-09-30_12-34-49\ModsConfig.fixed.xml
note: the probe closed windows that paused the game in trials 2, 12: Dialog_ModFeatures, Dialog_UpdateFeatures

TRIALS  12 runs, 12m20s total

   #  label                    mods  outcome        time
   1  baseline                  200  FAIL          78.0s
          | XML error: <rimbisectNoSuchField>1</rimbisectNoSuchField> doesn't correspond to any field in type ThingCategoryDef. Context: <ThingCategoryDef><def...
   2  bisect                    103  PASS         114.4s
   ...
```

Trial 12 is the check at the end: the full list without the culprit, which no longer
shows the error. The two windows in the note are HugsLib's update news and the feature
popup of Achtung and Camera+, which would otherwise have kept the game paused.

Six of the 200 mods (Core, the five DLCs) are always loaded, so 194 were searched. The run
folder keeps `report.txt`, `report.json`, the fixed mod list and every trial's log. Old
run folders are safe to delete.

The simplest fix is to disable the culprit in the game's mod manager. The fixed list is
your list without the culprits and without the mods that need them; to use it, close
RimWorld, back up `ModsConfig.xml` in
`%USERPROFILE%\AppData\LocalLow\Ludeon Studios\RimWorld by Ludeon Studios\Config`, and copy
`ModsConfig.fixed.xml` over it. To undo, put the backup back.

## What counts as the error

A trial fails when:

- the error you picked from the full list's log shows up again (the default). Numbers,
  hex ids and quoted names are masked before comparing, and for an exception only its
  type and the method it came from count (the first one that isn't part of .NET or
  Unity), so the same error from a different pawn still counts.
- a log line or error contains `--match-text TEXT`, or matches `--match REGEX`.
- the map takes longer than `--slower-than SECONDS` to be ready, for slow starts.
- the game crashes, exits early, gives up on the map or drops back to Core alone after a
  load error, with `--crash-is-fail`. Without
  it those trials are reported as CRASH, run again once (big lists do crash now and then
  for no repeatable reason), and counted as not showing the error if the game crashes
  again. With it
  and without `--match`, `--match-text`, `--slower-than` or `--pick`, the crash itself is
  what rimbisect hunts.

Only one of `--match`, `--match-text` and `--slower-than` can be given.

A trial passes when the map has run for `--settle` seconds of game time (20 by default)
without the error. Raise it if your error only shows up a while into the game. A PC that
runs the game too slowly to get there still stops after three times as long in real time
(at least a minute), and the report points out trials that got through less than half of
it. If the full list does not reproduce the error, rimbisect says so and stops.

A trial that gives no answer, because it hit `--timeout` or the game did not load all the
mods it was given, is run again once. If the same list gives no answer twice, rimbisect
stops with the status "inconclusive" and says which list it was. It also stops that way
when the mods it narrowed down to don't show the error when run alone, or the list a
search started from no longer shows it, which is what an error that only shows up some
of the time looks like; `--repeats 3` helps there. An inconclusive run can go on from
where it stopped with `rimbisect resume --repeats 3` (any number higher than the run
had), or with a longer `--timeout` when it stopped on a list that gave no answer.
Double-clicking `rimbisect.exe` after such a run offers the one that fits.

## How long it takes

Every trial is a full game start, so time depends on your PC and the size of the list.
On the run above (an i9-14900K, game on an NVMe SSD, RimWorld 1.6.4871) the full 199 mod
list took 5 minutes to reach a map and run it for 20 game seconds, a list of about 100
mods took under two minutes, and trials under 30 mods took 35 to 55 seconds. A failing
trial is often much quicker: the run stops the moment the error appears, and def errors
appear before the map is generated.

Trials needed after the first full run, measured with
[`acceptance/simulate.py`](https://github.com/Booyaka101/rimbisect/blob/main/acceptance/simulate.py) over every
single culprit and 300 random pairs:

| mods | one culprit | two mods that only fail together |
| ---: | --- | --- |
| 64  | 8.6 on average, at most 11 | 18.2 on average, at most 23 |
| 200 | 10.6 on average, at most 13 | 21.9 on average, at most 28 |
| 400 | 11.7 on average, at most 15 | 24.4 on average, at most 30 |

The real 200 mod run above needed 11, as the estimate after the full run said. For 400
mods, count on about 12 trials plus the full run. The first trials load half the list and
the last one nearly all of it, so they take most of the time; a single culprit in 400
mods should take somewhere around 25 to 40 minutes on a PC like this one. That is an
estimate from the 200 mod timings, not a measurement.

## How it works

Each trial runs the game with `-savedatafolder` pointing at the run folder, which holds a
copy of your `Config` folder (mod settings included), HugsLib's settings and the trial's
mod list. The folder is emptied and the copy made again before every trial, so a setting
one trial changes, or a file a mod writes there, doesn't leak into the next, and it is
deleted when the run ends. The copied `Prefs.xml`
runs the game in a 1280x720 window with the sound off and the UI scale at 1. Your own
`ModsConfig.xml` and `Prefs.xml` are only read. With `--save`, the save is copied into
the run folder and each trial loads a fresh copy of it; the game only loads a save at
startup in dev mode, so the trial's `Prefs.xml` turns that on, and the copied `Config`
leaves out the file the "disable dev mode for good" button writes. The original is never
opened for writing. If the game still sits at the main menu, the trial stops after about
ten seconds instead of waiting for `--timeout`.

For the run, rimbisect copies a small mod, `rimbisect-probe`, into the game's `Mods`
folder and deletes it afterwards, also on Ctrl+C or when you close rimbisect's window.
It loads last, writes errors and progress to a file rimbisect watches, and closes the
game once the map has settled. It has no Harmony patches and does nothing when rimbisect
did not start the game. If rimbisect is killed hard, delete `Mods\rimbisect-probe`
yourself or let the next run replace it. Only one rimbisect can run on a game at a time.

The probe runs the game at the fastest speed while it settles, so `--settle` is game
time, but it always gives a map at least five real seconds, for errors that come from
drawing rather than ticking. A mod that throws an error every frame keeps the game from
updating anything after it, the probe included; the probe notices after half a minute and
the trial counts as a crash.

RimWorld stops logging for good after 10,000 messages, which a big list can reach while
loading. The probe keeps the game's count from getting there, and switches logging back
on if it was reached before the probe loaded, so a later error is still seen.

The probe also reports which mods the game actually loaded. If a trial is missing some
(usually because Steam was closed mid-run), it counts as no answer rather than a pass.
Windows that pause a new game, like HugsLib's update news or the feature popups of
Achtung and Camera+, are closed so the map actually runs; the report says which ones.

Mods are read the way the game reads them. A mod whose `About.xml` has no packageId, or
can't be parsed, goes by the id the game makes up for it, and `rimbisect mods` warns
about it.

Every trial includes the mods that the mods under test need, so there are no "missing
dependency" errors that the full list doesn't have. Missing dependencies are never
invented; rimbisect warns about them instead, and about pairs of active mods that say
they are incompatible. A trial is your list with some mods left out, never reordered.
Mods given with `--keep` are loaded in every trial, together with what they need.

When neither half fails on its own, rimbisect narrows each half with the other one
loaded, so two or three mods that only break together come out as a set. It then leaves
each mod of the set out once, to make sure the rest don't fail without it.

Once it has a culprit, rimbisect runs the list once more without it and the mods that
need it. If the error is still there, something else causes it too, and the search goes
on through the rest. The report lists each cause on its own.

`rimbisect check` runs your full list once and, if it gets through, remembers it as the
last good state. The next bisect first tries the list without the mods whose folders
changed since then (updates, new mods). `--since DATE` does the same without a check.

For errors that come and go, `--repeats N` runs a passing list again, up to N times,
before trusting it. Lists that gave different results are listed in the report.

rimbisect only kills the game process it started, by process id. The game runs in a
Windows job object, so it also closes if rimbisect is killed or its window is closed.

## Commands

| | |
| --- | --- |
| `rimbisect bisect` | Find the mods behind an error |
| `rimbisect resume [RUN_FOLDER]` | Go on with the newest unfinished run, or the one given |
| `rimbisect check` | Run the full list once; if it gets through, save it as the last good state |
| `rimbisect mods` | Show the mod list the way rimbisect reads it, with warnings |

`bisect`, `check` and `mods` take these:

| Option | |
| --- | --- |
| `--game PATH` | RimWorld install folder. Found through Steam if omitted |
| `--config FILE` | `ModsConfig.xml` to test instead of the game's own. Only read |
| `--json` | Print JSON instead of text |

`bisect` and `check` also take these:

| Option | |
| --- | --- |
| `--workdir PATH` | Where runs and `last-good.json` go. Default `%LOCALAPPDATA%\rimbisect` |
| `--match REGEX` | Fail when an error or log line matches |
| `--match-text TEXT` | Fail when an error or log line contains TEXT, taken literally |
| `--slower-than SECONDS` | Fail when the map is not ready after this long |
| `--save NAME` | Load this save in every trial instead of starting a new colony. Only copied |
| `--settle SECONDS` | Game seconds a map has to run without the error to pass. Default 20 |
| `--timeout MINUTES` | Give up on a single trial after this long. Default 20 |

and `bisect` these:

| Option | |
| --- | --- |
| `--pick N` | Hunt error N from the full list's errors without asking |
| `--keep ID[,ID]` | Load these packageIds in every trial. Core and DLCs always are |
| `--since DATE` | First try the list without mods changed after DATE, like `2026-09-01` |
| `--ignore-last-good` | Don't try the mods changed since the last good run first |
| `--repeats N` | Run a passing list up to N times, for errors that come and go |
| `--crash-is-fail` | Count a crash, early exit or failed map as the problem |

`resume` goes on with the game, mod list and options the run started with, and takes
these:

| Option | |
| --- | --- |
| `--workdir PATH` | Where to look for runs. Default `%LOCALAPPDATA%\rimbisect` |
| `--pick N` | The error to hunt, when the run stopped before one was picked |
| `--repeats N` | Change the run's `--repeats` |
| `--timeout MINUTES` | Change the run's `--timeout` |
| `--json` | Print JSON instead of text |

`rimbisect --version` prints the version.

Exit codes: 0 when culprits were found (or `check` passed), 1 when the full list did not
reproduce the error, the base game alone fails, the search was inconclusive, or `check`
failed, 2 for usage errors such as a missing install or RimWorld already running, 130
when interrupted.

## Limitations

- Windows only, and only the Steam version has been tried. The probe is built for 1.6.
- It finds errors that happen while the game starts and during the first `--settle`
  seconds on a fresh map, or on your save with `--save`. Problems that need a raid or a
  year of play are out of reach.
- `resume` refuses a run whose game version, mod list or rimbisect version changed since
  it started. It only warns when mod files are newer, since some mods write logs into
  their own folder every time the game starts.
- Settings of mods that keep them outside `Config` (other than HugsLib) are not copied,
  so those mods run with their defaults.
- A problem that depends on load order, rather than on which mods are present, is only
  found as the set of mods involved. rimbisect does not try other orders.
- RimWorld has to be closed while it runs, and the machine is busy the whole time.

## License

MIT
