# rimbisect progress

State on 2026-09-30: version 0.2.2, the sixth review round's fixes and small guided-mode
additions (below) on top of 0.2.1's review fixes and 0.2.0's `resume`, `--save`,
standalone `rimbisect.exe` and error search by text. 0.2.1 is released:
https://github.com/Booyaka101/rimbisect/releases/tag/v0.2.1 (commit 7770802) and
https://pypi.org/project/rimbisect/0.2.1/.

## Verified

All on the owner's PC: RimWorld 1.6.4871 (Steam, `D:\SteamLibrary`), i9-14900K, NVMe.

- **Acceptance run.** A 200 mod list (the real Workshop mods padded out, built by
  `acceptance/stage.py`) with a test mod `rimbisect.acceptance.brokendef` at position 150,
  whose def has a field that does not exist:

      PYTHONPATH=src python -m rimbisect bisect --config acceptance/work/ModsConfig-200.xml \
          --workdir acceptance/work/rb --match rimbisectNoSuchField

  Found the test mod unattended on the final code (run 2026-09-30_12-34-49), exit 0,
  12 trials in 12m20s: the baseline, 10 bisect trials, and the confirm trial with the
  full list minus the culprit (199 mods). That is 11 after the baseline, as the estimate
  printed after the baseline said. Trial times 78.0, 114.4, 8.3, 41.5, 54.8, 5.1, 34.7,
  35.7, 4.6, 39.5, 2.8, 320.1 seconds. The new "culprits alone" check reused trial 11,
  which was that exact list, so it cost nothing here. The probe still closed and reported
  both pausing windows after the change to count only windows it actually removed. The
  output is in README.md. The run before the last review round (2026-09-30_11-53-13)
  took the same 12 trials in 11m47s.
- **A real bug, with resume.** The 200 mod list minus the test mod logs
  `encountered ArgumentException while patching PawnCanOpen` at startup. Hunted with
  `--match-text "while patching PawnCanOpen"` (run 2026-09-30_14-27-18). After trial 4
  the process was killed by PID, which took the game with it through the job object;
  `rimbisect resume` from the exe then replayed the four recorded trials without starting
  the game and went on live from trial 5. Exit 0 after 23 trials in 33m34s: Doors
  Expanded and Pawnmorpher, which only fail together. The confirm trial with the other
  197 mods passed. One trial crashed natively (0xC0000005) and passed when run again.
- **Mods write into their own folders.** During that run VanillaExpanded.Achievements
  (`AchievementLog.txt`), neronix17.tweaksgalore (`Version.txt`) and
  SmashPhil.VehicleFramework (`Updates/UpdateLog.xml`) wrote files into their mod
  folders at game start. The first `resume` refused the run because of it; newer files
  are now a warning, and a changed mod list or game version still refuse. The same
  writes make "changed since the last good run" include those mods, which only costs a
  trial or two since it only orders the search.
- **Save mode.** A test mod `rimbisect.acceptance.savefail` logs an error from
  `GameComponent.LoadedGame`, which only runs for a loaded save. In a 16 mod list with
  `--save Autosave-1 --match-text "only happens in a loaded save" --settle 5` (run
  2026-09-30_15-07-13): exit 0, found in 8 trials, 1m53s. The save was made with 8 mods
  on 1.6.4850 and loaded with its missing-class errors each time. Passing trials ran
  their 300 ticks within a second or two of the map being ready. `check` on the same list
  without `--save` passed, and the original save's timestamp is unchanged.
- **Save mode, 0.2.1.** The same savefail list again after the probe moved to a game
  component: found in 8 trials, passes ran about 1700 ticks with `--settle 5` because of
  the new five real seconds floor, and the save copy was deleted at the end. With dev
  mode forced off the game stayed at the main menu and the trial ended as a crash in
  12.8s instead of the 300s timeout. With the game's `DevModeDisabled` file present,
  which used to leave it broken, the save loaded.
- **Error every frame, 0.2.2.** A test mod `rimbisect.acceptance.framefail` sets the
  map's sky manager to null, so every frame throws before the game components update
  (16,967 "Root level exception" lines in one trial). On the 0.2.1 probe a `check` with
  `--timeout 2` ended as no answer after 120.2s. With the fix it ended as a crash in
  53.7s with the probe's reason and the errors listed. `bisect --match-text "Root level
  exception in Update"` on a 16 mod list found it in 8 trials, exit 0 (run
  2026-09-30_17-50-07), and again on the final code (2026-09-30_18-07-53) while a watcher
  listed the savedata folder every second: only `Config`, `HugsLib` and the game's own
  `Saves`, reset at the start of each trial.
- **Console closed mid-trial.** A `check` on that list, with Windows' close event sent to
  rimbisect while the game was running: rimbisect exited (0xC000013A) within the wait,
  no RimWorld process was left and the probe folder was gone from `Mods`. The same
  `check` left alone passed in 25.2s and numbered its one error.
- **Standalone exe.** PyInstaller onefile, 13.8 MB, built from a clean venv (the global
  environment left the probe out and pulled in unrelated packages). It ran `mods` and the
  resume above against the real install. Started in its own console it waits in guided
  mode; started from `cmd` with no arguments it prints usage and exits 2.
- **Paused maps.** The run before that one (2026-09-30_11-29-17, same result, 14m16s)
  had 0 game ticks in trials 2 and 12: HugsLib's update news and Brrainz's feature
  dialog (Achtung, Camera+) paused the new game, so those trials only "passed" by the
  one-minute real-time cap. The probe now closes windows that force a pause and sets the
  speed to Superfast. A check on the same 103 mod list then ran 1200 ticks and passed in
  93.8s instead of 158s, and the report named both windows.
- **Native crash.** The first attempt of that 103 mod check died in map generation with
  an access violation in ntdll (0xC0000005, in the Windows event log), on a list that
  passed before and after. So big real lists crash now and then for no repeatable reason.
  Without `--crash-is-fail`, the search used to count that as a pass; it now runs a
  crashed trial again once. The crash excerpt starts with the exit code.
- **Crash path.** A test mod `rimbisect.acceptance.mapfail` whose scenario part throws
  after map generation, in a 16 mod list:

      PYTHONPATH=src python -m rimbisect bisect --config acceptance/work/ModsConfig-mapfail-16.xml \
          --workdir acceptance/work/rb --crash-is-fail --settle 5 --ignore-last-good

  Exit 0, found it in 5 trials including the confirm trial, 1m53s. Every CRASH trial
  ended within seconds of the game's "Error while generating a map" dialog.
- **Log flood.** RimWorld stops logging after 10,000 messages. A test mod
  `rimbisect.acceptance.flood` logs 10,001 warnings in its constructor and 10,001 more
  from a static constructor later in loading, then logs `rimbisectLateFloodError`:

      PYTHONPATH=src python -m rimbisect check --config acceptance/work/ModsConfig-flood-17.xml \
          --workdir acceptance/work/rb-flood --match rimbisectLateFloodError --settle 5

  FAIL in 26.3s, exit 1. All 10,001 late lines are in the log and the late error was
  seen: the probe sets the game's counter back every 5,000 messages. The one reset in
  the report is from the constructor flood, which runs before the probe is loaded.
- **Game time.** A 12 mod `check --settle 5` passed in 34.1s with 300 ticks, and with no
  "Resolution too small" error now that the UI scale is set to 1.
- **Job object.** Twice the session running a bisect was killed mid-run; both times no
  RimWorld process was left. The probe folder was left in `Mods`, as the README says.
- After every finished run the probe folder was gone from `<game>\Mods` and no RimWorld
  process was left. The real `ModsConfig.xml` and `Prefs.xml` were never written.
- `python -m pytest`: 224 passed. `python -m pyflakes src tests acceptance/*.py` clean.
- `python -m build` makes the wheel and sdist. The wheel's `RimbisectProbe.dll` matches
  `src/` by hash, installs into a fresh venv, and `rimbisect --version` and `mods` run
  from it.
- Function-pair similarity check (difflib over every function of 6+ lines in src, tests
  and acceptance): the only pairs above 0.45 are a nested helper against the function
  around it (`cli._above_zero`'s checker, a test's `rule`), which is the same lines
  counted twice.

## Review passes

Four rounds of independent review, each followed by fixes with tests. Every test added
in the last round was checked to fail on the code before its fix.

Probe and game side:

- The probe went blind after RimWorld's 10,000 message cap. It now keeps the counter
  from getting there, and switches logging back on if it was reached earlier.
- Nothing checked that the game loaded the mods it was given (Steam closed mid-run makes
  it skip Workshop mods silently). The probe reports the loaded list; a trial missing
  mods the full list had is no answer, and so is a FAIL from such a list.
- Settle was real time, so a slow PC tested less game. It is now game ticks with a
  real-time cap, and passes with little game time are flagged in the report.
- The events file was reopened for every line; it is one writer now.
- Forcing 1280x720 made a "Resolution too small" error for players with a big UI scale.
  The copied Prefs set the UI scale to 1.
- Config and HugsLib settings are copied fresh for each trial, so a setting one trial
  writes doesn't reach the next. A copy that fails is no answer instead of a traceback.
- The game runs in a job object, so it can't outlive rimbisect.
- Windows that pause a new game are closed (found live in this pass, see above).

Search and matching:

- `locate_all` looped forever when a culprit was also an alternative dependency of
  another mod, and alternative dependencies could name an innocent mod. Mods taken out of
  the search are never loaded as an alternative again.
- Independent causes were reported as one. A confirm trial without the culprit now runs,
  and the search goes on if the error is still there.
- `--keep` mods' dependencies were still searched; they are loaded with the kept mods.
- Error signatures depended on which other mods patched the method, and lost the method
  name for generic types. Harmony's renamed frames are mapped back, and `[T]` is dropped.
- A crash while hunting some other error counted as a pass; it is now run again once.

CLI and docs: Ctrl+C, an empty `Prefs.xml` and a bad `last-good.json` gave tracebacks;
`--match` and `--since` were checked after the run folder was made; a line continuation
in `cli.py` was mangled. All fixed. README options split by command, exit codes include
inconclusive, the limitations list mod settings kept outside `Config`.

Last round (search, trial loop and CLI, three reviewers):

- The search could blame mods that don't fail alone: an error that shows up only some of
  the time narrows to whatever was left. The narrowed set is now run on its own (reusing
  a matching trial unless it is the baseline), and the run stops as inconclusive with
  advice to use `--repeats` if it passes.
- With `--since`, a second cause among the changed mods was missed, and a changed mod
  pulled in as an alternative dependency could frame an unchanged one. Mods that need
  each other (a dependency cycle) sent the search into infinite recursion.
- The baseline gave up on the first trial without an answer; it now retries like the
  search does, through one shared function.
- An error logged before the probe reported the loaded mods could end a trial as FAIL on
  a list the game had not fully loaded. The game falling back to Core alone after a load
  error was not seen at all; it is a crash now. The last log line was lost when the game
  exited without a newline. The probe's own log lines could match `--match`.
- Signatures included the pawn's name for "Exception ticking Kaylee" errors; with a stack
  frame it is now the exception type and the method.
- CLI: an interrupted or inconclusive run dropped the causes found so far; `check` had no
  report on Ctrl+C; zero, negative or NaN numbers, an empty `--match-text`, a `--pick`
  with a match, an unknown `--keep` id, a missing `--config` and a workdir that is a file
  gave tracebacks or odd runs. `--workdir` was accepted by `mods`, which ignores it.
  The report shows the `--since` date.

Fifth round, for 0.2.1 (search, trial loop and probe, CLI and docs, three reviewers).
Every one of the 12 tests added or changed was checked to fail on the 0.2.0 code.

- Flaky errors: a reviewer's scenario where the error never shows again after the
  baseline ran 189 trials before giving up; the full list is now run again at the first
  interaction split and the run stops as inconclusive after 9. A flaky first pass that
  left an innocent mod in a pair is caught by leaving each member out in turn. A list
  that only crashed named 17 bogus causes; a crash no longer counts as a pass there.
- Save mode: `Config/DevModeDisabled` (the "disable dev mode for good" button) breaks
  the game at startup once dev mode is forced on; it is deleted from the copy. A save
  that doesn't load left the game at the main menu until the timeout. Game time was
  counted on the current map only.
- Signatures: framework frames (`System.`, `UnityEngine.`, `Mono.`) and RimWorld's
  `[Ref X] Duplicate stacktrace` lines split one error into several or merged unrelated
  ones.
- CLI: closing the console window left the probe in `Mods`; two runs on one game fought
  over it; `resume` could not change `--repeats` or `--timeout`, nor retry an
  inconclusive run; a killed write could corrupt `run.json`; the save copy was never
  deleted; a run from another rimbisect version was resumed.
- Docs: the exe needs `.\` in PowerShell, SmartScreen and antivirus, the pair estimates
  in the README were low (`acceptance/simulate.py` now gives 18.2/23, 21.9/28 and
  24.4/30 mean/max trials for 64, 200 and 400 mods).

Sixth round, for 0.2.2 (search and resume, trial lifecycle, mod parsing, and one reviewer
looking for enhancements). Every one of the 18 tests added or changed was checked to fail
on the 0.2.1 code.

- Flaky errors again: the search after the first cause, and the one on the list without
  the changed mods, started from a failure nobody had checked. A reviewer's scripts ran
  about 650 trials and named bogus causes; they now stop as inconclusive after 10 to 19.
  Leaving one culprit out reused a flaky pass; in the reviewer's seeded simulation wrong
  answers went from 47 of 300 to 18 of 300. `acceptance/simulate.py` numbers unchanged.
- `resume` let `--repeats` go down, and retried an inconclusive run with options that
  could only stop it the same way.
- A mod throwing every frame (Root_Play wraps the whole frame in one try/catch) kept the
  probe's game component from updating, so the trial waited out `--timeout`. Found live
  with a new test mod, see Verified.
- Only `Config` and `HugsLib` were reset between trials; a finished run's savedata had
  CameraPlus, DefaultSettingsBackup and Xenotypes folders carried along. The whole folder
  is emptied now.
- Closing the console mid-trial could journal the killed trial as a crash, which
  `--crash-is-fail` would count as the error.
- `About.xml`: lxml read files the game reads differently (bytes that aren't UTF-8, a
  bare `&`, `<PackageId>`, no packageId, no `About.xml` at all), so the mod was dropped
  with a false "not installed" warning. Ported from `Verse.ModMetaData` and
  `DirectXmlLoader`: File.ReadAllText's decoding, case-insensitive field tags, defaults on
  a parse error, and the made-up id (StableStringHash, ConvertToASCII). A leftover
  `_steam` entry and two Workshop copies of one mod also follow the game now.
- Enhancements built: guided mode offers to retry an inconclusive run with the option
  that can change the answer, names the culprits again at the end, and the window title
  shows the trial count; a run over five minutes rings the bell.

Left as they are, on purpose:

- No cap on the number of causes. Each group must fail on its own, and lists with many
  real causes (missing textures from several mods) exist.
- Texture warnings for different files are not merged into one error.
- Inferring a trial's result from the ones around it to save a run. Too easy to get
  wrong for a few minutes saved.
- `DebugSettings.pauseOnError` is left alone; it is off unless a player turns it on.
- A flake that lands on the leave-one-out check itself can still leave a pair. Only
  `--repeats` catches that.
- From the enhancement review: writing the fixed list into the game's `ModLists` folder
  as an `.rml` (breaks the promise that rimbisect never writes game files, and the
  import could not be checked live), `--suspects`, time estimates in minutes (trial
  times vary tenfold within one run) and toast notifications.

Not changed: `Translate()` on the probe's timer thread. It only runs when an error
dialog is already open, and that dialog's title was translated on the main thread.

## Not verified

- The game falling back to Core alone after a load error (its log lines and the
  "RecoveredFromErrors" dialog) was tested with the fake game only.
- The pawn-name signature change was tested with made-up log lines, not a live error.
- The window title and the bell were not seen live; the run above had its output
  redirected, which turns both off.
- The `About.xml` cases were checked against the decompiled game, not by loading such
  mods. A scan of all 243 installed mods gives the same ids before and after the change.
- Guided mode (double-click) was tested live only up to its first question; the rest is
  covered by tests with a faked console.
- The exe on another PC, and what SmartScreen does with it.
- `--save` with a save made on the same big list it is bisecting.
- The 400 mod timing in the README is an estimate from the 200 mod run.
- The trial counts from `acceptance/simulate.py` assume a game that fails exactly when
  the culprits are loaded. Real lists with flaky errors take more.
- If the probe fails to write one event, it drops its writer and opens a new one on the
  next event without disposing the old stream. Harmless for a process that exits soon.
- Non-Steam installs, other RimWorld versions than 1.6.4871, Windows 10.

## Next steps for the owner

1. First distribution step, below.
2. Later releases: bump the version, wait for CI on the exact commit, then
   `python -m build`, `twine upload dist/*`, tag, download the `exe` artifact of that
   commit's CI run (`gh run download <id> -n exe`) and `gh release create` with the
   wheel, sdist and exe.

To run the acceptance again: `python acceptance/stage.py install --size 200 --position 150`
(or `--mod mapfail`, `--mod flood`, `--mod savefail`, `--mod framefail`) installs the test mod and writes the list to
`acceptance/work`, and `python acceptance/stage.py remove` removes the test mods. The
game's own mod list has only 8 mods now, so add `--source acceptance/work/ModsConfig-200.xml`
to reuse the saved 200 mod list. The
test mod sources are in `acceptance/brokendef`, `acceptance/mapfail-src`,
`acceptance/flood-src`, `acceptance/savefail-src` and `acceptance/framefail-src`, the probe's in `probe-src` (`dotnet build -c Release` writes the
DLL straight to `src/rimbisect/probe/Assemblies`). `acceptance/simulate.py` measures
trial counts against a simulated game.

## First distribution step

Draft updated for 0.2.0 below the 0.1.0 one. Not posted.

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
> aren't touched. 200 mods took about 12 minutes on my PC.
>
> It only catches stuff that happens at startup or in the first bit of a new map though,
> so if the error needs your save it won't help. Windows only for now.

Least sure of: "I made" (it was built with Claude; say so if anyone asks), and whether
the 12 minute figure oversells it for someone on a slower PC with 400 mods.

0.2.0 draft. With the exe and `--save` it now fits threads about errors in an existing
colony too:

> If you don't want to halve your list by hand, I made a tool that does it for you:
> https://github.com/Booyaka101/rimbisect
>
> There's an exe on the releases page, no Python needed. Double click it, pick the red
> error from the list it shows, and it keeps restarting the game on smaller parts of your
> list until it's down to the mod, or the two mods that only break together. It keeps
> dependencies loaded and runs on a copy of your config, so your mod list and saves
> aren't touched. If the error only happens in your colony you can give it a save and
> every run loads a copy of that.
>
> On my 200 mod list it found a Doors Expanded + Pawnmorpher patch conflict on its own,
> took about half an hour. Windows only, and the exe isn't signed so SmartScreen will
> probably moan the first time.

Least sure of: the Doors Expanded + Pawnmorpher line. It is a real find on this list,
but on today's versions only; check it still happens before naming two mods in public,
or drop the names. Same "I made" caveat as above.

## Missing features

Built for 0.2.0: `resume`, `--save`, the exe with a guided double-click mode, exceptions
first and search by text when picking the error, Superfast settle.

Built in the review passes: the copied Config folder is deleted when a run ends; the
report notes message-limit hits, slow trials, closed windows and mods the game did not
load; `--match-text`; crash exit codes in the report; `--version`; the causes found so far
in an interrupted or inconclusive report; a header row in `mods`; the `--since` date in
the report.

Not built:

- `--suspects ID,...` to search only some mods (the inverse of `--keep`).
- Load order problems: rimbisect never reorders, so an order-only bug comes out as a set.
- Errors that need play time, a raid or an event. `--save` covers errors that come
  from the save itself.
- Linux and macOS, GOG and other non-Steam installs (`--game` may work, untested).
- Writing the fixed list into the game's config. Left out on purpose: rimbisect never
  writes the real config.
