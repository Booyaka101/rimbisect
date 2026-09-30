from pathlib import Path

from fakegame import make_order
from rimbisect import report
from rimbisect.install import Game
from rimbisect.trial import CRASH, PASS, Trial


def build(tmp_path, trials, **kwargs):
    return report.build(status=report.FOUND, run_dir=tmp_path, game=Game(Path("RimWorld"), "1.6"),
                        config_path=Path("ModsConfig.xml"), order=make_order(4), criterion=None,
                        trials=trials, culprits={"m01"}, warnings=[], **kwargs)


def test_notes_name_the_trials_that_hit_the_message_limit(tmp_path):
    trials = [Trial(1, "baseline", [], PASS, log_resets=1), Trial(2, "bisect", [], PASS),
              Trial(3, "bisect", [], PASS, log_resets=2)]
    data = build(tmp_path, trials)
    assert any("message limit was hit in trials 1, 3" in note for note in data["notes"])
    assert [t["logResets"] for t in data["trials"]] == [1, 0, 2]
    assert "TRIALS  3 runs," in report.render_text(data)


def test_crash_note_depends_on_crash_is_fail(tmp_path):
    trials = [Trial(1, "baseline", [], CRASH)]
    assert any("1 trial crashed" in note for note in build(tmp_path, trials)["notes"])
    assert not any("crashed" in note for note in build(tmp_path, trials, crash_is_fail=True)["notes"])
