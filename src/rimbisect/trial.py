"""Run the game once on a given mod list and classify what happened."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path
from typing import Protocol

from .errors import UserError
from .install import Game
from .modlist import PROBE_FOLDER, PROBE_ID, ModsConfig
from .signature import Criterion

PASS, FAIL, CRASH, UNRESOLVED = "PASS", "FAIL", "CRASH", "UNRESOLVED"
DONE_LINE = "RIMBISECT_DONE"
PROBE_LINE = "RIMBISECT_"
# When loading throws, the game drops every mod but Core and loads again, without the probe.
FALLBACK_LINES = ("Caught exception while loading play data", "Could not recover from errors loading play data")

# Unattended trials must keep running without focus; the rest just keeps them out of the way.
PREF_OVERRIDES = {
    "runInBackground": "True",
    "fullscreen": "False",
    "screenWidth": "1280",
    "screenHeight": "720",
    "volumeMaster": "0",
    # Above 1 the game logs "Resolution too small" at 1280x720, which is not the player's error.
    "uiScale": "1",
}

# At launch the game loads the save named "autostart", but only in dev mode.
SAVE_PREFS = {"devMode": "True", "pauseOnLoad": "False"}
AUTOSTART = "autostart.rws"

# Mod settings that live next to Config rather than in it.
SETTINGS_FOLDERS = ("HugsLib",)


@dataclass
class Trial:
    number: int
    label: str
    mods: list[str]
    outcome: str = UNRESOLVED
    duration: float = 0.0
    map_ready: float | None = None
    excerpt: str = ""
    log: str | None = None
    errors: list[tuple[str, int]] = field(default_factory=list)
    log_resets: int = 0
    ticks: int | None = None  # game ticks the map ran before the probe quit
    not_loaded: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)  # windows the probe closed to unpause the game

    @property
    def mod_count(self) -> int:
        return sum(m != PROBE_ID for m in self.mods)

    def line(self) -> str:
        return (f"trial {self.number:>3}  {self.label:<22} {self.mod_count:>4} mods  "
                f"{self.outcome:<10} {self.duration:>6.1f}s")


class Launcher(Protocol):
    trials: list[Trial]
    numbered: int  # trial numbers used so far
    not_loaded_at_first: set[str] | None

    def run(self, mods: list[str], label: str, criterion: Criterion | None) -> Trial: ...


def install_probe(game: Game) -> Path:
    target = game.mods_dir / PROBE_FOLDER
    source = resources.files("rimbisect") / "probe"
    try:
        with resources.as_file(source) as src:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target)
    except OSError as exc:
        raise UserError(f"could not copy the probe mod to {target}: {exc}. Check that RimWorld is closed "
                        "and that you can write to its Mods folder.") from exc
    return target


def remove_probe(game: Game) -> None:
    shutil.rmtree(game.mods_dir / PROBE_FOLDER, ignore_errors=True)


def patch_prefs(path: Path, extra: dict[str, str] | None = None) -> None:
    """Apply PREF_OVERRIDES and extra to a copied Prefs.xml, adding the elements if missing."""
    from lxml import etree

    root = None
    if path.is_file():
        try:
            tree = etree.parse(str(path), etree.XMLParser(recover=True))
            root = tree.getroot()
        except etree.XMLSyntaxError:
            pass
    if root is None:
        root = etree.Element("PrefsData")
        tree = etree.ElementTree(root)
    for tag, value in {**PREF_OVERRIDES, **(extra or {})}.items():
        el = root.find(tag)
        if el is None:
            el = etree.SubElement(root, tag)
        el.text = value
    tree.write(str(path), xml_declaration=True, encoding="utf-8")


def prepare_savedata(real_config: Path, savedata: Path, save: Path | None = None) -> Path:
    """savedata/Config with every file from the real Config folder except ModsConfig.xml,
    plus the SETTINGS_FOLDERS beside it. Done before every trial, so nothing a mod writes
    during one trial carries over into the next. With a save, it is the one the game loads."""
    config = savedata / "Config"
    for name in ("Config", *SETTINGS_FOLDERS):
        shutil.rmtree(savedata / name, ignore_errors=True)
    if real_config.is_dir():
        shutil.copytree(real_config, config, dirs_exist_ok=True,
                        ignore=lambda d, names: [n for n in names if Path(d) == real_config and n == "ModsConfig.xml"])
    else:
        config.mkdir(parents=True)
    for name in SETTINGS_FOLDERS:
        if (real_config.parent / name).is_dir():
            shutil.copytree(real_config.parent / name, savedata / name, dirs_exist_ok=True)
    patch_prefs(config / "Prefs.xml", SAVE_PREFS if save else None)
    if save:
        (savedata / "Saves").mkdir(exist_ok=True)
        shutil.copyfile(save, savedata / "Saves" / AUTOSTART)
    return config


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


class Job:
    """A Windows job object holding the game and whatever it starts. Windows ends them all
    when the job is closed, including when rimbisect itself is killed or its window closed."""

    def __init__(self, handle: int):
        self.handle = handle

    @classmethod
    def holding(cls, pid: int) -> Job | None:
        """A job with the process in it, or None if Windows would not allow it."""
        if os.name != "nt":
            return None
        k32 = _kernel32()
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process = k32.OpenProcess(0x0101, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        ok = bool(process) and k32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) \
            and k32.AssignProcessToJobObject(job, process)
        if process:
            k32.CloseHandle(process)
        if not ok:
            k32.CloseHandle(job)
            return None
        return cls(job)

    def close(self) -> None:
        if self.handle:
            _kernel32().CloseHandle(self.handle)
            self.handle = None


# taskkill /T also takes processes whose parent merely had the same id before it was
# reused, so children only count if they started after their parent.
_DESCENDANTS = """
$all = Get-CimInstance Win32_Process
$queue = @($all | Where-Object ProcessId -eq {pid})
while ($queue) {{
    $parent, $queue = $queue
    $kids = @($all | Where-Object {{ $_.ParentProcessId -eq $parent.ProcessId -and $_.CreationDate -ge $parent.CreationDate }})
    $kids.ProcessId
    $queue = @($queue) + $kids
}}
"""


def kill_tree(proc: subprocess.Popen, job: Job | None = None) -> None:
    """Kill the process rimbisect started and the processes it started, nothing else."""
    if job is not None:
        job.close()
    elif proc.poll() is None:
        try:
            listed = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                     _DESCENDANTS.format(pid=proc.pid)], capture_output=True, text=True,
                                    timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired):
            listed = ""
        proc.kill()
        for pid in listed.split():
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (OSError, ValueError):
                pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


class _Tail:
    """New complete lines appended to a file since the last read."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.partial = b""

    def lines(self, final: bool = False) -> list[str]:
        """With final, the file is complete and an unterminated last line counts too."""
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                data = fh.read()
        except OSError:
            data = b""
        self.offset += len(data)
        data = self.partial + data
        *complete, self.partial = data.split(b"\n")
        if final and self.partial:
            complete.append(self.partial)
            self.partial = b""
        return [line.decode("utf-8", errors="replace").rstrip("\r") for line in complete]


class GameLauncher:
    """Launches RimWorld on an isolated save data folder, into a new -quicktest colony or a save."""

    def __init__(self, game: Game, config: ModsConfig, run_dir: Path, real_config_dir: Path,
                 settle: float = 20.0, timeout: float = 1200.0, poll: float = 0.25, log=print,
                 save: Path | None = None):
        self.game = game
        self.config = replace(config, version=game.version)
        self.run_dir = run_dir
        self.real_config_dir = real_config_dir
        self.savedata = (run_dir / "savedata").resolve()
        self.settle = settle
        self.timeout = timeout
        self.poll = poll
        self.log = log
        self.save = save
        self.trials: list[Trial] = []
        self.numbered = 0
        self.not_loaded_at_first: set[str] | None = None

    def run(self, mods: list[str], label: str, criterion: Criterion | None) -> Trial:
        self.numbered += 1
        trial = Trial(self.numbered, label, mods)
        self.trials.append(trial)
        stem = self.run_dir / f"trial-{trial.number:02d}"
        log_path, events_path = stem.with_suffix(".log").resolve(), stem.with_suffix(".events.jsonl").resolve()
        for path in (log_path, events_path):
            path.unlink(missing_ok=True)
        trial.log = str(log_path)
        try:
            prepare_savedata(self.real_config_dir, self.savedata, self.save)
            (self.savedata / "Config" / "ModsConfig.xml").write_text(self.config.to_xml(mods), encoding="utf-8")
        except OSError as exc:
            trial.excerpt = f"could not refresh the copied Config folder: {exc}"
            return trial

        env = dict(os.environ, RIMBISECT_EVENTS=str(events_path), RIMBISECT_SETTLE=str(self.settle))
        args = [str(self.game.exe), f"-savedatafolder={self.savedata.as_posix()}",
                *([] if self.save else ["-quicktest"]), "-logFile", str(log_path)]
        started = time.monotonic()
        proc = subprocess.Popen(args, cwd=str(self.game.root), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        job = Job.holding(proc.pid)
        log_tail, events_tail = _Tail(log_path), _Tail(events_path)
        recent: list[str] = []
        done = log_done = loaded = False
        unloaded: str | None = None
        gave_up: str | None = None

        def read_log(final: bool) -> None:
            nonlocal recent, log_done, gave_up
            for line in log_tail.lines(final):
                if log_done or line == DONE_LINE:
                    log_done = True
                    return
                if line.strip("= \t"):  # Mono's crash banner is mostly rules and blank lines.
                    recent = (recent + [line])[-40:]
                if line.startswith(PROBE_LINE):
                    continue
                if line.startswith(FALLBACK_LINES) and gave_up is None:
                    gave_up = "the game could not load this mod list and fell back to Core alone"
                if criterion is not None and trial.outcome != FAIL and criterion.line_matches(line):
                    trial.outcome, trial.excerpt = FAIL, line[:300]

        try:
            while True:
                elapsed = time.monotonic() - started
                # Checked before reading, so a process that has exited has also finished writing.
                exited = proc.poll() is not None
                read_log(exited)
                for raw in events_tail.lines():
                    event = _parse_event(raw)
                    if event is None:
                        continue
                    kind = event.get("event")
                    if kind == "error":
                        text = str(event.get("text", ""))
                        trial.errors.append((text, int(event.get("repeats", 1))))
                        if criterion is not None and trial.outcome != FAIL and criterion.error_matches(text):
                            trial.outcome, trial.excerpt = FAIL, _clip(text)
                    elif kind == "started":
                        loaded = True
                        if "text" in event:
                            unloaded = self._check_loaded(trial, str(event["text"]).split("\n"))
                    elif kind == "map_ready" and trial.map_ready is None:
                        trial.map_ready = round(elapsed, 1)
                    elif kind == "done":
                        done = True
                        trial.ticks = int(event["text"]) if str(event.get("text", "")).isdigit() else None
                    elif kind == "gave_up":
                        gave_up = str(event.get("text", ""))
                    elif kind == "log_reset":
                        trial.log_resets += 1
                    elif kind == "closed" and "text" in event:
                        trial.closed.append(str(event["text"]))
                if unloaded:
                    # A FAIL from a list the game only partly loaded says nothing about the list.
                    trial.outcome, trial.excerpt = UNRESOLVED, unloaded
                    break
                # Mod constructors and assembly loading log before the probe reports what
                # the game loaded, and a FAIL only counts from a list it loaded in full.
                if trial.outcome == FAIL and (loaded or exited):
                    break
                if criterion is not None and criterion.slower_than is not None:
                    if trial.map_ready is None and elapsed > criterion.slower_than:
                        trial.outcome = FAIL
                        trial.excerpt = f"map not ready after {criterion.slower_than:g}s"
                        break
                    if trial.map_ready is not None:
                        trial.outcome = FAIL if trial.map_ready > criterion.slower_than else PASS
                        trial.excerpt = f"map ready after {trial.map_ready:g}s"
                        break
                if done:
                    # The done event can overtake log lines written just before it.
                    deadline = time.monotonic() + 30
                    while not log_done and time.monotonic() < deadline:
                        exited = proc.poll() is not None
                        read_log(exited)
                        if exited:
                            break
                        time.sleep(self.poll)
                    if trial.outcome != FAIL:
                        trial.outcome = PASS
                    break
                if gave_up is not None:
                    trial.outcome = CRASH
                    trial.excerpt = "\n".join([gave_up] + [text.splitlines()[0] for text, _ in trial.errors[-3:] if text])
                    break
                if exited:
                    trial.outcome = CRASH
                    trial.excerpt = "\n".join([_exit_line(proc.returncode)] + recent[-10:])
                    break
                if elapsed > self.timeout:
                    trial.outcome = UNRESOLVED
                    trial.excerpt = f"no result after {self.timeout:g}s\n" + "\n".join(recent[-10:])
                    break
                time.sleep(self.poll)
        finally:
            trial.duration = round(time.monotonic() - started, 1)
            if done:
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
            kill_tree(proc, job)
        return trial

    def _check_loaded(self, trial: Trial, running: list[str]) -> str | None:
        """Record the mods the game was given but did not load. Whatever the first trial
        could not load is taken as normal; when this trial is missing more, the game did not
        test the list it was asked to, and the reason is returned."""
        trial.not_loaded = [m for m in trial.mods if m not in set(running) and m != PROBE_ID]
        if self.not_loaded_at_first is None:
            self.not_loaded_at_first = set(trial.not_loaded)
            return None
        missing = [m for m in trial.not_loaded if m not in self.not_loaded_at_first]
        if not missing:
            return None
        return (f"the game did not load {len(missing)} of the mods it was given "
                f"({', '.join(missing[:5])}); is Steam still running?")


def _parse_event(raw: str) -> dict | None:
    try:
        event = json.loads(raw)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def _exit_line(code: int) -> str:
    code &= 0xFFFFFFFF
    if code == 0xC0000005:
        return "the game crashed (access violation, 0xC0000005)"
    return f"the game exited early with code {code:#010x}" if code >= 0x80000000 else f"the game exited early with code {code}"


def _clip(text: str, lines: int = 8) -> str:
    kept = [line for line in text.splitlines() if line.strip()][:lines]
    return "\n".join(line[:300] for line in kept)
