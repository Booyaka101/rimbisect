"""Stands in for RimWorldWin64.exe in test_trial.py: writes a log and probe events the way
the game and the probe do, following the scenario named in FAKE_SCENARIO."""

import json
import os
import sys
import time

args = sys.argv[1:]
log = open(args[args.index("-logFile") + 1], "a", encoding="utf-8")
events = open(os.environ["RIMBISECT_EVENTS"], "a", encoding="utf-8")
with open(os.environ["FAKE_PID_FILE"], "w") as fh:
    fh.write(str(os.getpid()))


def say(line):
    log.write(line + "\n")
    log.flush()


def emit(kind, text=None):
    event = {"event": kind} if text is None else {"event": kind, "text": text}
    events.write(json.dumps(event) + "\n")
    events.flush()


scenario = os.environ["FAKE_SCENARIO"]
say("Mono path[0] = 'fake'")
running = os.environ.get("FAKE_RUNNING")
emit("started", "\n".join(running.split(",")) if running else None)
if scenario == "settings":
    savedata = next(a for a in args if a.startswith("-savedatafolder=")).split("=", 1)[1]
    with open(os.path.join(savedata, "Config", "Mod_Example_Settings.xml"), "w") as fh:
        fh.write("changed by a mod")
if scenario == "late_line":
    emit("map_ready")
    emit("done")
    time.sleep(0.5)
    say("XML error: <rimbisectNoSuchField>1</rimbisectNoSuchField> doesn't correspond to any field")
    say("RIMBISECT_DONE")
    say("XML error: after the probe finished, so it does not count: rimbisectNoSuchField")
    sys.exit(0)
if scenario in ("pass", "log_reset", "settings", "paused"):
    emit("error", "Some unrelated error")
    if scenario == "log_reset":
        emit("log_reset")
    emit("map_ready")
    say("RIMBISECT_MAP_READY")
    if scenario == "paused":
        emit("closed", "HugsLib.News.Dialog_UpdateFeatures")
    emit("done", "1200")
    say("RIMBISECT_DONE")
    sys.exit(0)
if scenario == "error":
    emit("error", "Could not resolve cross-reference to Verse.ThingDef named Widget_12 (wanter=thingDef)")
elif scenario == "logline":
    say("XML error: <rimbisectNoSuchField>1</rimbisectNoSuchField> doesn't correspond to any field")
elif scenario == "exit":
    say("Crash!!!")
    sys.exit(1)
elif scenario == "gave_up":
    emit("error", "Exception from asynchronous event: System.InvalidOperationException: boom\n  at Some.Frame ()")
    emit("gave_up", "Error generating map: An error occurred while generating the map.")
elif scenario == "slow_map":
    time.sleep(1.5)
    emit("map_ready")
time.sleep(60)
