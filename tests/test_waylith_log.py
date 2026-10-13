"""The 1.7 log format, read from Player.log files captured from real 1.7.4976 runs
on 2026-10-13: player17-clean.log is Core and the five DLCs with the probe, and
player17-erroring.log adds the broken-def test mod at position 12."""

from pathlib import Path

from rimbisect.trial import DONE_LINE, strip_log_line

FIXTURES = Path(__file__).with_name("fixtures")


def lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace").splitlines()


def stripped(name: str) -> list[str]:
    return [strip_log_line(line) for line in lines(name)]


def test_the_done_marker_only_exists_behind_the_prefix():
    clean = lines("player17-clean.log")
    assert "RIMBISECT_DONE" not in clean
    assert DONE_LINE in stripped("player17-clean.log")


def test_the_clean_run_reaches_the_map_and_ends():
    text = stripped("player17-clean.log")
    assert "RIMBISECT_MAP_READY" in text
    assert text.index("RIMBISECT_MAP_READY") < text.index(DONE_LINE)


def test_the_erroring_run_carries_the_xml_error():
    text = stripped("player17-erroring.log")
    line = next(line for line in text if line.startswith("XML error:"))
    assert "<rimbisectNoSuchField>1</rimbisectNoSuchField>" in line
    assert "doesn't correspond to any field in type ThingCategoryDef" in line


def test_the_erroring_run_also_reaches_the_map_and_ends():
    assert DONE_LINE in stripped("player17-erroring.log")


def test_engine_lines_are_left_alone():
    for name in ("player17-clean.log", "player17-erroring.log"):
        for line in lines(name):
            if not line.startswith("2026-"):
                assert strip_log_line(line) == line


def test_plain_16_lines_are_left_alone():
    assert strip_log_line("Mono path[0] = 'fake'") == "Mono path[0] = 'fake'"
    assert strip_log_line("RIMBISECT_DONE") == "RIMBISECT_DONE"
    assert strip_log_line("XML error: plain") == "XML error: plain"


def test_every_timestamped_line_loses_its_prefix():
    import re

    stamped = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3,} +(INFO|WARN|ERROR|FATAL|DEBUG|TRACE) ")
    for name in ("player17-clean.log", "player17-erroring.log"):
        for line in lines(name):
            if stamped.match(line):
                assert not stamped.match(strip_log_line(line))
