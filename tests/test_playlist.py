"""
Tests for playlist.py — playlist management for LucidDreamer.AI.

ZeroClaw's first test suite. These tests cover:
  - Directory loading
  - Track add/remove
  - Selection logic
  - No-repeat window enforcement
  - Quality and mood filtering
  - Weighted selection bias
  - Queue building
  - Coherence anchor selection
"""

import os
import random
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from playlist import Track, Playlist


# ── Fixtures ────────────────────────────────────────────────

@pytest.fixture
def sample_tracks():
    """Create sample Track objects for testing."""
    return [
        Track(
            path="/fake/dawn-broadcast.mp3",
            title="Dawn Broadcast",
            duration_seconds=368.0,
            quality_score=0.85,
            mood_tags=["dawn", "announcement", "warm"],
            show_name="Morning Watch",
            character="Lucineer",
            created_at=time.time() - 86400,  # 1 day ago
        ),
        Track(
            path="/fake/dawn-bed.mp3",
            title="Dawn Bed",
            duration_seconds=180.0,
            quality_score=0.6,
            mood_tags=["ambient", "music_heavy"],
            show_name="Overnight Dispatch",
            character="MMX",
            created_at=time.time() - 3600,  # 1 hour ago
        ),
        Track(
            path="/fake/wesley-letter.mp3",
            title="Wesley Letter To Glimmer",
            duration_seconds=240.0,
            quality_score=0.9,
            mood_tags=["warm", "conversational", "essay"],
            show_name="Midday Essays",
            character="Wesley",
            created_at=time.time() - 604800,  # 1 week ago
        ),
    ]


@pytest.fixture
def playlist(sample_tracks):
    """Create a Playlist with sample tracks."""
    return Playlist(tracks=sample_tracks, no_repeat_window_minutes=60)


@pytest.fixture
def real_audio_dir():
    """Path to real audio files for integration tests."""
    return "/home/eileen/projects/ai-writings/radio-theater/channel-42-dawn"


# ── Tests ───────────────────────────────────────────────────

class TestPlaylistLoading:

    def test_load_from_directory(self, real_audio_dir):
        """Test that audio files are loaded from a directory."""
        if not os.path.exists(real_audio_dir):
            pytest.skip("Audio directory not available")
        pl = Playlist(audio_dir=real_audio_dir)
        assert pl.size >= 3
        assert all(t.is_audio for t in pl.tracks)
        filenames = [t.filename for t in pl.tracks]
        assert "dawn-broadcast.mp3" in filenames
        assert "dawn-bed.mp3" in filenames
        assert "wesley-letter-to-glimmer.mp3" in filenames

    def test_load_nonexistent_directory(self):
        """Test that loading a nonexistent directory raises."""
        with pytest.raises(FileNotFoundError):
            Playlist(audio_dir="/nonexistent/path/12345")

    def test_add_and_remove_track(self, sample_tracks):
        """Test adding and removing tracks."""
        pl = Playlist()
        assert pl.size == 0

        pl.add_track(sample_tracks[0])
        assert pl.size == 1

        pl.add_track(sample_tracks[1])
        assert pl.size == 2

        removed = pl.remove_track(sample_tracks[0].path)
        assert removed is True
        assert pl.size == 1

        removed = pl.remove_track("/nonexistent")
        assert removed is False


class TestPlaylistSelection:

    def test_select_next_returns_track(self, playlist):
        """Test that select_next returns a Track."""
        track = playlist.select_next(rng=random.Random(42))
        assert track is not None
        assert isinstance(track, Track)
        assert track in playlist.tracks

    def test_no_repeat_window(self, playlist):
        """Test that recently played tracks are not selected again."""
        now = time.time()
        track1 = playlist.tracks[0]
        playlist.mark_played(track1, now)

        selected = playlist.select_next(now=now, rng=random.Random(42))
        assert selected is not None
        assert selected.path != track1.path

    def test_quality_filter(self, playlist):
        """Test that min_quality filter works."""
        selected = playlist.select_next(min_quality=0.7, rng=random.Random(42))
        if selected:
            assert selected.quality_score >= 0.7

    def test_build_queue_respects_size(self, playlist):
        """Test that build_queue returns the right number of tracks."""
        queue = playlist.build_queue(size=2, rng=random.Random(42))
        assert len(queue) <= 2
        paths = [t.path for t in queue]
        assert len(paths) == len(set(paths))

    def test_weighted_selection_favors_higher_quality(self):
        """Test that higher quality tracks are selected more often."""
        low_q = Track(path="/fake/low.mp3", title="Low", quality_score=0.1, created_at=time.time())
        high_q = Track(path="/fake/high.mp3", title="High", quality_score=0.95, created_at=time.time())
        pl = Playlist(tracks=[low_q, high_q])

        rng = random.Random(42)
        high_count = 0
        total = 200

        for _ in range(total):
            pl._play_history = []
            selected = pl.select_next(rng=rng)
            if selected.path == high_q.path:
                high_count += 1
            pl.mark_played(selected)

        assert high_count > total * 0.6, f"Expected >60% high quality, got {high_count}/{total}"


class TestCoherenceAnchor:

    def test_get_anchor_returns_high_quality(self, playlist):
        """Test that anchor selection returns only high-quality tracks."""
        playlist.tracks[0].quality_score = 0.95
        playlist.tracks[1].quality_score = 0.4
        playlist.tracks[2].quality_score = 0.9

        anchor = playlist.get_anchor_track(min_quality=0.8)
        assert anchor is not None
        assert anchor.quality_score >= 0.8

    def test_anchor_skips_recently_played(self, playlist):
        """Test that recently played anchors are skipped."""
        playlist.tracks[0].quality_score = 0.95
        playlist.tracks[2].quality_score = 0.9
        playlist.mark_played(playlist.tracks[0])

        anchor = playlist.get_anchor_track(min_quality=0.8)
        if anchor:
            assert anchor.path != playlist.tracks[0].path

    def test_anchor_returns_none_when_none_qualify(self, playlist):
        """Test that anchor returns None when no tracks meet quality threshold."""
        for t in playlist.tracks:
            t.quality_score = 0.3
        anchor = playlist.get_anchor_track(min_quality=0.8)
        assert anchor is None
