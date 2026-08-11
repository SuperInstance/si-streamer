"""
Tests for muxer.py — audio concatenation, crossfades, HLS output.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from playlist import Track
from muxer import Muxer, MuxerConfig


# ── Fixtures ────────────────────────────────────────────────

@pytest.fixture
def sample_tracks():
    return [
        Track(path="/fake/a.mp3", title="A", duration_seconds=368.0, quality_score=0.8),
        Track(path="/fake/b.mp3", title="B", duration_seconds=180.0, quality_score=0.6),
        Track(path="/fake/c.mp3", title="C", duration_seconds=240.0, quality_score=0.9),
    ]


@pytest.fixture
def real_audio_dir():
    return "/home/eileen/projects/ai-writings/radio-theater/channel-42-dawn"


# ── Tests ───────────────────────────────────────────────────

class TestMuxerConfig:

    def test_default_config(self):
        """Test default MuxerConfig values."""
        cfg = MuxerConfig()
        assert cfg.crossfade_duration_seconds == 3.0
        assert cfg.normalization_enabled is True
        assert cfg.hls_segment_duration_seconds == 10
        assert cfg.inter_segment_silence_seconds == 1.0

    def test_config_properties(self):
        """Test that config properties convert to milliseconds."""
        cfg = MuxerConfig(crossfade_duration_seconds=5.0, inter_segment_silence_seconds=2.0)
        assert cfg.crossfade_ms == 5000
        assert cfg.silence_ms == 2000


class TestMuxer:

    def test_initialization(self):
        """Test that Muxer initializes with default config."""
        muxer = Muxer()
        assert muxer.config.crossfade_duration_seconds == 3.0

    def test_estimate_total_duration(self, sample_tracks):
        """Test duration estimation: 3 tracks = 788s, minus 2 crossfades (6s), plus 2 silences (2s) = 784s."""
        muxer = Muxer()
        est = muxer.estimate_total_duration(sample_tracks)
        assert 780 < est < 790

    def test_estimate_empty_playlist(self):
        """Test that empty playlist estimates to 0."""
        muxer = Muxer()
        assert muxer.estimate_total_duration([]) == 0.0

    def test_real_audio_load(self, real_audio_dir):
        """Integration: load a real MP3 file."""
        if not os.path.exists(real_audio_dir):
            pytest.skip("Audio directory not available")
        audio_file = Path(real_audio_dir) / "dawn-bed.mp3"
        if not audio_file.exists():
            pytest.skip("dawn-bed.mp3 not found")

        muxer = Muxer()
        track = Track(path=str(audio_file), title="Dawn Bed")
        audio = muxer.load_track(track)
        assert len(audio) > 0
        # dawn-bed.mp3 should be at least a few minutes
        assert len(audio) > 60000  # > 1 minute in ms

    def test_hls_segmentation(self, real_audio_dir, tmp_path):
        """Integration: segment real audio into HLS chunks."""
        if not os.path.exists(real_audio_dir):
            pytest.skip("Audio directory not available")
        audio_file = Path(real_audio_dir) / "dawn-bed.mp3"
        if not audio_file.exists():
            pytest.skip("dawn-bed.mp3 not found")

        muxer = Muxer()
        track = Track(path=str(audio_file), title="Dawn Bed")
        audio = muxer.load_track(track)

        output_dir = str(tmp_path / "hls")
        m3u8_path, segments = muxer.segment_to_hls(audio, output_dir, segment_duration_seconds=10)

        assert os.path.exists(m3u8_path)
        assert len(segments) > 0
        for seg in segments:
            assert os.path.exists(seg)
            assert os.path.getsize(seg) > 0

        with open(m3u8_path) as f:
            content = f.read()
        assert "#EXTM3U" in content
        assert "#EXT-X-TARGETDURATION:10" in content
        assert ".ts" in content
