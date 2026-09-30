"""Run the game once on a given mod list and classify what happened."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Protocol

from .errors import UserError
from .install import Game
from .modlist import PROBE_FOLDER, PROBE_ID, ModsConfig
from .signature import Criterion

PASS, FAIL, CRASH, UNRESOLVED = "PASS", "FAIL", "CRASH", "UNRESOLVED"
DONE_LINE = "RIMBISECT_DONE"

# Unattended trials must keep running without focus; the rest just keeps them out of the way.
PREF_OVERRIDES = {
    "runInBackground": "True",
    "fullscreen": "False",
    "screenWidth": "1280",
    "screenHeight": "720",
    "volumeMaster": "0",
}


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

    @property
    def mod_count(self) -> int:
        return sum(m != PROBE_ID for m in self.mods)

    def line(self) -> str:
        return (f"trial {self.number:>3}  {self.label:<22} {self.mod_count:>4} mods  "
                f"{self.outcome:<10} {self.duration:>6.1f}s")


class Launcher(Protocol):
    def run(self, mods: list[str], label: str, criterion: Criterion | None) -> Trial: ...


def install_probe(game: Game) -> Path:
    target = game.mods_dir / PROBE_FOLDER
    source = resources.files("rimbisect") / "probe"
    with resources.as_file(source) as src:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
    return target


def remove_probe(game: Game) -> None:
    shutil.rmtree(game.mods_dir / PROBE_FOLDER, ignore_errors=True)


def patch_prefs(path: Path) -> None:
    """Apply PREF_OVERRIDES to a copied Prefs.xml, adding the elements if missing."""
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
    for tag, value in PREF_OVERRIDES.items():
        el = root.find(tag)
        if el is None:
            el = etree.SubElement(root, tag)
        el.text = value
    tree.write(str(path), xml_declaration=True, encoding="utf-8")


def prepare_savedata(real_config: Path, savedata: Path) -> Path:
    """savedata/Config with every file from the real Config folder except ModsConfig.xml."""
    config = savedata / "Config"
    if config.exists():
        shutil.rmtree(config)
    if real_config.is_dir():
        shutil.copytree(real_config, config, ignore=lambda d, names: [n for n in names
                                                                       if Path(d) == real_config and n == "ModsConfig.xml"])
    else:
        config.mkdir(parents=True)
    patch_prefs(config / "Prefs.xml")
    return config


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


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process rimbisect started and the processes it started, nothing else."""
    if proc.poll() is not None:
        return
    listed = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                             _DESCENDANTS.format(pid=proc.pid)], capture_output=True, text=True)
    proc.kill()
    for pid in listed.stdout.split():
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

    def lines(self) -> list[str]:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                data = fh.read()
        except OSError:
            return []
        self.offset += len(data)
        data = self.partial + data
        *complete, self.partial = data.split(b"\n")
        return [line.decode("utf-8", errors="replace").rstrip("\r") for line in complete]


class GameLauncher:
    """Launches RimWorld with -quicktest on an isolated save data folder."""

    def __init__(self, game: Game, config: ModsConfig, run_dir: Path, real_config_dir: Path,
                 settle: float = 20.0, timeout: float = 1200.0, poll: float = 0.25, log=print):
        if "=" in str(run_dir.resolve()):
            raise UserError(f"the run folder {run_dir} contains '=', which RimWorld's -savedatafolder= "
                            "cannot take. Pick another with --workdir.")
        self.game = game
        self.config = config
        self.run_dir = run_dir
        self.savedata = (run_dir / "savedata").resolve()
        self.settle = settle
        self.timeout = timeout
        self.poll = poll
        self.log = log
        self.trials: list[Trial] = []
        prepare_savedata(real_config_dir, self.savedata)

    def run(self, mods: list[str], label: str, criterion: Criterion | None) -> Trial:
        trial = Trial(len(self.trials) + 1, label, mods)
        self.trials.append(trial)
        stem = self.run_dir / f"trial-{trial.number:02d}"
        log_path, events_path = stem.with_suffix(".log").resolve(), stem.with_suffix(".events.jsonl").resolve()
        for path in (log_path, events_path):
            path.unlink(missing_ok=True)
        (self.savedata / "Config" / "ModsConfig.xml").write_text(self.config.to_xml(mods), encoding="utf-8")
        trial.log = str(log_path)

        env = dict(os.environ, RIMBISECT_EVENTS=str(events_path), RIMBISECT_SETTLE=str(self.settle))
        args = [str(self.game.exe), f"-savedatafolder={self.savedata.as_posix()}", "-quicktest",
                "-logFile", str(log_path)]
        started = time.monotonic()
        proc = subprocess.Popen(args, cwd=str(self.game.root), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_tail, events_tail = _Tail(log_path), _Tail(events_path)
        recent: list[str] = []
        done = log_done = False
        gave_up: str | None = None

        def read_log() -> None:
            nonlocal recent, log_done
            for line in log_tail.lines():
                if log_done or line == DONE_LINE:
                    log_done = True
                    return
                recent = (recent + [line])[-40:]
                if criterion is not None and trial.outcome != FAIL and criterion.line_matches(line):
                    trial.outcome, trial.excerpt = FAIL, line[:300]

        try:
            while True:
                elapsed = time.monotonic() - started
                # Checked before reading, so a process that has exited has also finished writing.
                exited = proc.poll() is not None
                read_log()
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
                    elif kind == "map_ready" and trial.map_ready is None:
                        trial.map_ready = round(elapsed, 1)
                    elif kind == "done":
                        done = True
                    elif kind == "gave_up":
                        gave_up = str(event.get("text", ""))
                    elif kind == "log_reset":
                        trial.log_resets += 1
                if trial.outcome == FAIL:
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
                        read_log()
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
                    trial.excerpt = "\n".join(recent[-15:])
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
            kill_tree(proc)
        return trial


def _parse_event(raw: str) -> dict | None:
    try:
        event = json.loads(raw)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def _clip(text: str, lines: int = 8) -> str:
    kept = [line for line in text.splitlines() if line.strip()][:lines]
    return "\n".join(line[:300] for line in kept)
