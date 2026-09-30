# rimbisect

Find the RimWorld mod, or combination of mods, behind an error. Unattended.

You have 300 mods and a red error at startup, or a map that never finishes loading. The
usual fix is to disable half your list, start the game, see if the error is still there,
and repeat until you are down to one mod. That takes an evening and it is easy to get
wrong once dependencies are involved. rimbisect does the same halving for you. It
launches the game on dependency-safe parts of your mod list, reads each run's log,
closes the game and narrows down, until it can name the mod.

It never writes to your real mod list, settings or saves. It never reorders your mods.

## Install

```
pipx install rimbisect
```

or `pip install rimbisect`. Needs Windows, Python 3.11 or newer, and RimWorld 1.6 from
Steam.

## Use it

Close RimWorld, keep Steam running, and start a bisect:

```
rimbisect bisect
```

rimbisect finds your install through Steam and takes the mod list you have active in the
game. It first runs your full list once and shows the errors it logged, grouped and
numbered. Type the number of the one you want gone, then leave it. The game opens and
closes on its own for every trial; don't click in it.

If you already know what the error says, skip the question:

```
rimbisect bisect --match "doesn't correspond to any field"
```

This is a real run on a 200 mod list with a test mod that has a broken def at position
150. Progress goes to the terminal as it happens:

```
rimbisect 0.1.0: 200 active mods, 194 to search
run folder D:\Repos\ideas\rimbisect\acceptance\work\rb\runs\2026-09-30_09-25-24
trial   1  baseline                200 mods  FAIL         98.7s
trial   2  bisect                  103 mods  PASS        116.3s
trial   3  bisect                   57 mods  FAIL          8.3s
trial   4  bisect                   31 mods  PASS         38.8s
trial   5  bisect                   23 mods  PASS         61.8s
trial   6  bisect                   20 mods  FAIL          5.1s
trial   7  bisect                   13 mods  PASS         35.2s
trial   8  bisect                   11 mods  PASS         33.7s
trial   9  bisect                   11 mods  FAIL          4.8s
trial  10  bisect                    9 mods  PASS         38.7s
trial  11  bisect                    7 mods  FAIL          2.8s
```

and the report at the end:

```
rimbisect 0.1.0   found the mods that cause the error

game        D:\SteamLibrary\steamapps\common\RimWorld (1.6.4871 rev590)
mod list    D:\Repos\ideas\rimbisect\acceptance\work\ModsConfig-200.xml (200 active, 194 searched)
error       log matches /rimbisectNoSuchField/
run folder  D:\Repos\ideas\rimbisect\acceptance\work\rb\runs\2026-09-30_09-25-24

CULPRIT  rimbisect acceptance: broken def  (rimbisect.acceptance.brokendef)
  workshop id  -
  folder       D:\SteamLibrary\steamapps\common\RimWorld\Mods\rimbisect-acceptance-brokendef
  modified     2026-09-30T09:23:26

Your mod list without 1 mod:
  D:\Repos\ideas\rimbisect\acceptance\work\rb\runs\2026-09-30_09-25-24\ModsConfig.fixed.xml

TRIALS  11 run, 7m24s total

   #  label                    mods  outcome        time
   1  baseline                  200  FAIL          98.7s
          | XML error: <rimbisectNoSuchField>1</rimbisectNoSuchField> doesn't correspond to any field in type ThingCategoryDef. Context: <ThingCategoryDef><def...
   2  bisect                    103  PASS         116.3s
   ...
```

Six of the 200 mods (Core, the five DLCs) are always loaded, so 194 were searched. The run
folder keeps `report.txt`, `report.json`, the fixed mod list and every trial's log.

To use the fixed list, copy `ModsConfig.fixed.xml` over the `ModsConfig.xml` in your
config folder (back that up first), or just disable the culprit in the game.

## What counts as the error

A trial fails when:

- the error you picked from the full list's log shows up again (the default). Numbers,
  hex ids and quoted names are masked before comparing, so the same error from a
  different pawn still counts.
- a log line or error matches `--match REGEX`.
- the map takes longer than `--slower-than SECONDS` to be ready, for slow starts.
- the game crashes, exits early or gives up on the map, with `--crash-is-fail`. Without
  it those trials are reported as CRASH and treated as passing.

A trial passes when the map has been running for `--settle` seconds (20 by default)
without the error. Raise it if your error only shows up a while into the game. If the
full list does not reproduce the error, rimbisect says so and stops.

## How long it takes

Every trial is a full game start, so time depends on your PC and the size of the list.
On the run above (an i9-14900K, game on an NVMe SSD, RimWorld 1.6.4871) a list of about
100 mods took just under 2 minutes to get through a settled map, and trials under 30
mods took 30 to 60 seconds. A failing trial is often much quicker: the run stops the
moment the error appears, and def errors appear before the map is generated.

Trials needed after the first full run, measured with `acceptance/simulate.py` over every
single culprit and 300 random pairs:

| mods | one culprit | two mods that only fail together |
| ---: | --- | --- |
| 64  | 7.6 on average, at most 10 | 15.2 on average, at most 20 |
| 200 | 9.6 on average, at most 12 | 18.9 on average, at most 25 |
| 400 | 10.7 on average, at most 14 | 21.4 on average, at most 27 |

The real 200 mod run above needed 10. For 400 mods, count on about 11 trials plus the
full run. The first trials load half the list and take longest, so a single culprit in
400 mods should take somewhere around 15 to 25 minutes on a PC like this one. That is an
estimate from the 200 mod timings, not a measurement.

## How it works

- **Isolation.** Each trial runs the game with `-savedatafolder` pointing at
  the run folder, which holds a copy of your `Config` folder (mod settings included) and
  the trial's mod list. Your `ModsConfig.xml`, `Prefs.xml` and saves are only read.
- **A probe mod.** rimbisect copies a small mod, `rimbisect-probe`, into the game's
  `Mods` folder for the run and deletes it afterwards, also on Ctrl+C. It loads last,
  writes errors and progress to a file rimbisect watches, and closes the game once the
  map has settled. It has no Harmony patches and does nothing when rimbisect did not
  start the game. If rimbisect is killed hard, delete `Mods\rimbisect-probe` yourself.
- **Dependencies are respected.** Every trial includes the mods that the mods under test
  need, so there are no "missing dependency" errors that the full list doesn't have.
  Missing dependencies are never invented; rimbisect warns about them instead.
- **Load order is kept.** A trial is your list with some mods left out, never reordered.
- **Interactions.** When neither half fails on its own, it narrows each half with the
  other one loaded. Two or three mods that only break together come out as a set.
- **Changed mods first.** `rimbisect check` runs your full list once and, if it gets
  through, remembers it as the last good state. The next bisect first tries the list
  without the mods whose folders changed since then (updates, new mods). `--since DATE`
  does the same without a check.
- **Flaky errors.** With `--repeats N`, a list that passes is run again, up to N times,
  before it is trusted. Lists that gave different results are listed in the report.

rimbisect only kills the game process it started, by process id.

## Commands

| | |
| --- | --- |
| `rimbisect bisect` | Find the mods behind an error |
| `rimbisect check` | Run the full list once; if it gets through, save it as the last good state |
| `rimbisect mods` | Show the mod list the way rimbisect reads it, with warnings |

| Option | |
| --- | --- |
| `--game PATH` | RimWorld install folder. Found through Steam if omitted |
| `--config FILE` | `ModsConfig.xml` to test instead of the game's own. Only read |
| `--workdir PATH` | Where runs and `last-good.json` go. Default `%LOCALAPPDATA%\rimbisect` |
| `--json` | Print the report as JSON |
| `--match REGEX` | Fail when an error or log line matches |
| `--slower-than SECONDS` | Fail when the map is not ready after this long |
| `--settle SECONDS` | How long a map has to run without the error to pass. Default 20 |
| `--timeout MINUTES` | Give up on a single trial after this long. Default 20 |
| `--pick N` | Hunt error N from the full list's errors without asking |
| `--keep ID[,ID]` | Load these packageIds in every trial. Core and DLCs always are |
| `--since DATE` | First try the list without mods changed after DATE, like `2026-09-01` |
| `--ignore-last-good` | Don't try the mods changed since the last good run first |
| `--repeats N` | Run a passing list up to N times, for errors that come and go |
| `--crash-is-fail` | Count a crash, early exit or failed map as the problem |

Exit codes: 0 when culprits were found (or `check` passed), 1 when the full list did not
reproduce the error, the base game alone fails, or `check` failed, 2 for usage errors
such as a missing install or RimWorld already running, 130 when interrupted.

## Limitations

- Windows only, and only the Steam version has been tried. The probe is built for 1.6.
- It finds errors that happen while the game starts and during the first `--settle`
  seconds on a fresh map. Problems that need your save, a raid or a year of play are out
  of reach.
- A problem that depends on load order, rather than on which mods are present, is only
  found as the set of mods involved. rimbisect does not try other orders.
- RimWorld has to be closed while it runs, and the machine is busy the whole time.

## License

MIT
