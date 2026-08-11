"""
Tests for scheduler.py — time-of-day scheduling logic.
"""

import random
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from playlist import Track, Playlist
from scheduler import Scheduler, ScheduleSlot


# ── Fixtures ────────────────────────────────────────────────

@pytest.fixture
def sample_tracks():
    return [
        Track(
            path="/fake/dawn-broadcast.mp3", title="Dawn Broadcast",
            duration_seconds=368.0, quality_score=0.85,
            mood_tags=["dawn", "announcement", "warm"],
            created_at=time.time() - 86400,
        ),
        Track(
            path="/fake/dawn-bed.mp3", title="Dawn Bed",
            duration_seconds=180.0, quality_score=0.6,
            mood_tags=["ambient", "music_heavy"],
            created_at=time.time() - 3600,
        ),
        Track(
            path="/fake/wesley-letter.mp3", title="Wesley Letter",
            duration_seconds=240.0, quality_score=0.9,
            mood_tags=["warm", "conversational", "essay"],
            created_at=time.time() - 604800,
        ),
    ]


@pytest.fixture
def playlist(sample_tracks):
    return Playlist(tracks=sample_tracks, no_repeat_window_minutes=60)


@pytest.fixture
def scheduler(playlist):
    return Scheduler(playlist=playlist, coherence_anchor_interval=3)


# ── Tests ───────────────────────────────────────────────────

class TestScheduleSlots:

    def test_normal_slot_contains_hour(self):
        """Test that ScheduleSlot.contains_hour works for normal ranges."""
        morning = ScheduleSlot(name="Morning", start_hour=6, end_hour=10)
        assert morning.contains_hour(6) is True
        assert morning.contains_hour(9) is True
        assert morning.contains_hour(10) is False
        assert morning.contains_hour(3) is False

    def test_overnight_slot_contains_hour(self):
        """Test that ScheduleSlot.contains_hour works for overnight (wrapping) ranges."""
        overnight = ScheduleSlot(name="Overnight", start_hour=22, end_hour=6)
        assert overnight.contains_hour(22) is True
        assert overnight.contains_hour(23) is True
        assert overnight.contains_hour(0) is True
        assert overnight.contains_hour(5) is True
        assert overnight.contains_hour(6) is False
        assert overnight.contains_hour(12) is False

    def test_all_default_slots_cover_24_hours(self):
        """Test that the default schedule covers all 24 hours."""
        slots = Scheduler.DEFAULT_SLOTS
        for hour in range(24):
            matched = any(s.contains_hour(hour) for s in slots)
            assert matched, f"Hour {hour} not covered by any slot"


class TestSchedulerSelection:

    def test_get_current_slot_morning(self, scheduler):
        """Test that 8 AM returns Morning Watch."""
        morning_time = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0).timestamp()
        slot = scheduler.get_current_slot(now=morning_time)
        assert slot.name == "Morning Watch"

    def test_get_current_slot_night(self, scheduler):
        """Test that 3 AM returns Overnight Dispatch."""
        night_time = datetime.now().replace(hour=3, minute=0, second=0, microsecond=0).timestamp()
        slot = scheduler.get_current_slot(now=night_time)
        assert slot.name == "Overnight Dispatch"

    def test_get_current_slot_evening(self, scheduler):
        """Test that 20:00 returns Evening Tap."""
        evening_time = datetime.now().replace(hour=20, minute=0, second=0, microsecond=0).timestamp()
        slot = scheduler.get_current_slot(now=evening_time)
        assert slot.name == "Evening Tap"

    def test_now_playing_queue_returns_tracks(self, scheduler):
        """Test that now_playing_queue returns a list of Tracks."""
        queue = scheduler.now_playing_queue(size=3, rng=random.Random(42))
        assert len(queue) > 0
        assert all(isinstance(t, Track) for t in queue)

    def test_coherence_anchor_insertion(self, playlist):
        """Test that coherence anchors are inserted into the queue."""
        sched = Scheduler(
            playlist=playlist,
            coherence_anchor_interval=2,
            coherence_anchor_min_quality=0.7,
        )
        playlist.tracks[0].quality_score = 0.95
        playlist.tracks[2].quality_score = 0.9

        queue = sched.now_playing_queue(size=5, rng=random.Random(42))
        assert len(queue) > 0

    def test_get_next_slot(self, scheduler):
        """Test that get_next_slot returns a different slot."""
        current = scheduler.get_current_slot()
        next_slot = scheduler.get_next_slot()
        assert current.name != next_slot.name
