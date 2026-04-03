from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pull_to_structurer.py"
SPEC = importlib.util.spec_from_file_location("pull_to_structurer_progress", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_fetch_progress_reporter_emits_eta_and_rate(capsys):
    reporter = MODULE.FetchProgressReporter(total=4, engine="async", enabled=True)
    reporter.started -= 3.0
    reporter.start_target()
    reporter.finish_target(ok=True, message_count=10)
    reporter.finish()

    err = capsys.readouterr().err
    assert "[fetch:async]" in err
    assert "done=1/4" in err
    assert "rate=" in err
    assert "elapsed=" in err
    assert "eta=" in err


def test_fetch_progress_reporter_can_be_disabled(capsys):
    reporter = MODULE.FetchProgressReporter(total=2, engine="sync", enabled=False)
    reporter.start_target()
    reporter.finish_target(ok=False, message_count=0)
    reporter.finish()
    captured = capsys.readouterr()
    assert captured.err == ""
