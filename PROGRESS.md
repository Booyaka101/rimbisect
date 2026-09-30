# rimbisect progress

State on 2026-09-30: version 0.1.0 is built, tested on the real game and committed locally
on `main`. Nothing is pushed or published.

## Verified

All on the owner's PC: RimWorld 1.6.4871 (Steam, `D:\SteamLibrary`), i9-14900K, NVMe.

- **Acceptance run.** A 200 mod list (the real Workshop mods padded out, built by
  `acceptance/stage.py`) with a test mod `rimbisect.acceptance.brokendef` at position 150,
  whose def has a field that does not exist:

      PYTHONPATH=src python -m rimbisect bisect --config acceptance/work/ModsConfig-200.xml \
          --workdir acceptance/work/rb --match rimbisectNoSuchField

  Exit 0, found the test mod unattended in 11 trials (baseline plus 10), 7m24s. Trial
  times 98.7, 116.3, 8.3, 38.8, 61.8, 5.1, 35.2, 33.7, 4.8, 38.7, 2.8 seconds. The output
  is in README.md.
- **Crash path.** A test mod `rimbisect.acceptance.mapfail` whose scenario part throws
  after map generation, in a 16 mod list:

      PYTHONPATH=src python -m rimbisect bisect --config acceptance/work/ModsConfig-mapfail-16.xml \
          --workdir acceptance/work/rb --crash-is-fail --settle 5 --timeout 6

  Exit 0, found it in 4 trials, 1m26s. Every CRASH trial ended within seconds of the
  game's "Error while generating a map" dialog, which the probe detects.
- **Log flood.** RimWorld stops logging after 10,000 messages. A test mod
  `rimbisect.acceptance.flood` logs 10,001 warnings in its constructor, staged ahead of
  brokendef in a 17 mod list (`ModsConfig-flood-17.xml`). With the first probe build,
  `rimbisect check --match rimbisectNoSuchField` passed (exit 0, 268s): the broken def's
  XML error was never logged. With the fixed probe the same check failed on it after 8.1s
  (exit 1), and the report noted the reset.
- **Without `--crash-is-fail`** the same list exits 1 with "did not reproduce any error",
  and no traceback when stdin is closed.
- **`rimbisect check`** from the wheel installed into a clean venv, on the owner's own
  8 mod list: PASS in 20.7s, `last-good.json` saved, exit 0.
- After every run the probe folder was gone from `<game>\Mods` and no RimWorld process
  was left. The real `ModsConfig.xml` was never written.
- `python -m pytest`: 127 passed.
- `python -m build` makes the wheel and sdist. The wheel includes the probe's
  `About.xml` and `RimbisectProbe.dll` (hash checked against `src/`), installs into a
  fresh venv, and `rimbisect --version`, `mods` and `check` run from it.
- Function-pair similarity check (difflib over every function of 6+ lines in src, tests
  and acceptance): no pair above 0.40.

## Review pass

An independent review of the code found six problems. All are fixed, with tests:

- High: the probe went blind after RimWorld's 10,000 message cap, so a trial could pass
  with the error in it. The probe now turns logging back on (verified above).
- Ctrl+C before the first trial, an empty `Prefs.xml` and a malformed `last-good.json`
  gave tracebacks. `main` now returns 130 on Ctrl+C, an empty Prefs is replaced, a bad
  last-good file is ignored with a warning. `--match` and `--since` are checked before
  the run folder is made, so a typo no longer costs a baseline or leaves a Config copy.
- `taskkill /T` could kill an unrelated process whose parent id was reused by the game.
  rimbisect now kills the game and only the children created after it.
- A log line written just before the probe's done event could be missed. The log is
  now read up to the probe's done line before a trial passes.
- The probe read the game's message queue without a lock at startup; now guarded.
- Not changed: the reviewer flagged `Translate()` on the timer thread. It only runs when
  an error dialog is already open, and that dialog's title was translated on the main
  thread, so the language data is loaded by then.

## Not verified

- The "RecoveredFromErrors" dialog (the game falling back to Core alone after a load
  error) is detected by the same code as the map dialog, but was never triggered live.
  Taken from the decompiled `PlayDataLoader`.
- The 400 mod timing in the README is an estimate from the 200 mod run.
- Non-Steam installs, other RimWorld versions than 1.6.4871, Windows 10.
- Pairs of mods that only fail together were tested with the fake game and the simulator
  only, not with two real test mods.
- The README progress block is from a run before the "expect about N more trials" line
  was added, so that line is missing there. Rerun the acceptance to refresh it.

## Next steps for the owner

1. Create the GitHub repo `Booyaka101/rimbisect` (the URLs in `pyproject.toml` point
   there), push `main`, and add a CI workflow for `python -m pytest` on `windows-latest`
   if you want one. The tests need Windows (`tasklist`, `.bat` fake game).
2. `python -m build` and `twine upload dist/*` from the phone's build path. The dist in
   `dist/` predates the last commit; rebuild it first.
3. First distribution step, below.

To run the acceptance again: `python acceptance/stage.py install --size 200 --position 150`
(or `--mod mapfail`, `--mod flood`) installs the test mod and writes the list to
`acceptance/work`, and `python acceptance/stage.py remove` removes the test mods.
The test mod sources are in `acceptance/brokendef`, `acceptance/mapfail-src` and
`acceptance/flood-src`, the probe's
in `probe-src` (`dotnet build -c Release`, then copy the DLL to
`src/rimbisect/probe/Assemblies`).

## First distribution step

A reply in the next r/RimWorld "PC Help/Bug (Mod)" thread about an error at startup,
where someone is being told to halve their mod list by hand. These come up every few days
(1wm0ale "Looping Dev errors" and 1w53nc2 both got "do a binary search" answers this
month, and 1wdnotm is a crash before the main menu). The sub allows tools that add to the
discussion. A reply in a live thread fits better than a standalone post, and GitHub's
fluffy-mods/ModManager#149 (the one bisect feature request found) has had no activity
since 2021.

Draft, in the register of those threads. Only reply where the error shows up at startup
or on a fresh map, since that is all rimbisect can reproduce:

> If you don't want to do the halving by hand, I made a small command line tool that does
> it: https://github.com/Booyaka101/rimbisect
>
> It starts the game with dev quicktest on half your list, checks the log for the red
> error you pick, closes the game and keeps halving until it's down to the mod, or the two
> mods that only break together. Dependencies stay loaded so you don't get fake missing
> dependency errors, and it runs on a copy of your config so your mod list and saves
> aren't touched. 200 mods took about 7 minutes on my PC.
>
> It only catches stuff that happens at startup or in the first bit of a new map though,
> so if the error needs your save it won't help. Windows only for now.

Least sure of: "I made" (it was built with Claude; say so if anyone asks), and whether
the 7 minute figure oversells it for someone on a slower PC with 400 mods.

## Missing features

Built in this pass: the copied Config folder (15 MB on this PC) is deleted when a run
ends, so run folders keep only logs and reports. The report notes trials that hit the
message limit.

Not built:

- Resume an interrupted bisect from its run folder. The trial results are all in
  `report.json`, so the search could replay them.
- `--suspects ID,...` to search only some mods (the inverse of `--keep`).
- A notification when a long run finishes.
- Load order problems: rimbisect never reorders, so an order-only bug comes out as a set.
- Errors that need a save or play time. Would need save loading, out of scope for v1.
- Linux and macOS, GOG and other non-Steam installs (`--game` may work, untested).
- Writing the fixed list into the game's config. Left out on purpose: rimbisect never
  writes the real config.
