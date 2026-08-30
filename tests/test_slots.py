"""Decision-slot scheduling: fire once inside the window, never replay stale
slots after a mid-session restart."""
import os
import sys
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import main
from main import due_decision_slot

ET = ZoneInfo("America/New_York")


def at(hour, minute):
    return datetime(2026, 8, 31, hour, minute, tzinfo=ET)


def run_at(hour, minute, done):
    with patch.object(main, "datetime") as dt:
        dt.now.return_value = at(hour, minute)
        return due_decision_slot(done)


def test_fires_inside_window():
    done = set()
    assert run_at(9, 46, done) is not None
    print("test_fires_inside_window OK")


def test_not_before_slot():
    assert run_at(9, 30, set()) is None
    print("test_not_before_slot OK")


def test_fires_once():
    done = set()
    key = run_at(9, 50, done)
    done.add(key)
    assert run_at(9, 55, done) is None, "must not re-fire the same slot"
    print("test_fires_once OK")


def test_stale_slots_not_replayed():
    """Restart at 15:00: 09:45 and 12:30 are stale, 15:15 has not arrived."""
    done = set()
    assert run_at(15, 0, done) is None
    assert len(done) == 2, f"stale slots should be marked done, got {done}"
    # 15:15 still fires normally afterwards
    assert run_at(15, 20, done) is not None
    print("test_stale_slots_not_replayed OK")


if __name__ == "__main__":
    test_fires_inside_window()
    test_not_before_slot()
    test_fires_once()
    test_stale_slots_not_replayed()
    print("ALL SLOT TESTS PASSED")
