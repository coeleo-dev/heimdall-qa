"""Gate 6 of E2: the browser path has no blind waits (A.19).

A cheap textual gate, on purpose: it is the only way to keep `sleep` from
creeping back in as a "temporary" fix. `UiSession` polls a condition instead —
URL matched, response observed, `data-value` present — and the poll always has a
deadline that fails the step.
"""

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "nokr_qa"
FILES = (SRC / "browser.py", SRC / "ui_step.py")
FORBIDDEN = ("sleep(", "time.sleep", "wait_for_timeout(30", "asyncio.sleep")


@pytest.mark.parametrize("path", FILES, ids=[item.name for item in FILES])
def test_no_blind_wait_in_the_browser_path(path: Path):
    text = path.read_text(encoding="utf-8")
    for needle in FORBIDDEN:
        assert needle not in text, f"{path.name} must not use {needle!r}"


def test_the_poll_interval_is_a_condition_check_not_a_fixed_wait():
    text = (SRC / "browser.py").read_text(encoding="utf-8")
    assert "_POLL_MS" in text
    # Both poll loops (URL match, screen readiness) are bounded by a deadline
    # taken from the step/config budget instead of a fixed number of sleeps.
    assert text.count("deadline = monotonic() +") >= 2


def test_the_collector_is_not_touched_by_the_browser_path():
    # §7.10: `logs/collector.py` is the invariant that makes the browser cheap to
    # correlate. The day this test needs to change, the design premise broke.
    collector = SRC / "logs" / "collector.py"
    text = collector.read_text(encoding="utf-8")
    assert "browser" not in text
    assert "ui_step" not in text


def test_the_step_reuses_the_existing_collector():
    text = (SRC / "ui_step.py").read_text(encoding="utf-8")
    assert "from nokr_qa.logs.collector import collect" in text
