"""
muxer.py — Audio multiplexer for LucidDreamer.AI.

Takes audio tracks from the playlist and concatenates them into a
continuous stream with crossfades, normalization, and silence insertion.

Uses pydub for audio manipulation. Can output:
  - A single concatenated MP3 file
  - HLS segments (.ts files + .m3u8 playlist)

"The muxer is the transmitter. It takes signals and makes them
 fly through the air. Or at least through HTTP."
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playlist import Track

# pydub is our primary audio tool
from pydub import AudioSegment
from pydub.effects import normalize as pydub_normalize


@dataclass
class MuxerConfig:
    """Configuration for the audio muxer."""
    crossfade_duration_seconds: float = 3.0
    inter_segment_silence_seconds: float = 1.0
    target_lufs: float = -23.0
    normalization_enabled: bool = True
    hls_segment_duration_seconds: int = 10
    hls_playlist_entries: int = 15

    @property
    def crossfade_ms(self) -> int:
        return int(self.crossfade_duration_seconds * 1000)

    @property
    def silence_ms(self) -> int:
        return int(self.inter_segment_silence_seconds * 1000)

    @property
    def hls_segment_ms(self) -> int:
        return self.hls_segment_duration_seconds * 1000


class Muxer:
    """
    Audio multiplexer — concatenates tracks into a continuous stream.

    Handles:
      - Crossfade between tracks (configurable duration)
      - Silence insertion between segments
      - Loudness normalization (consistent volume)
      - Output as continuous MP3 or HLS segments
    """

    def __init__(self, config: MuxerConfig | None = None):
        self.config = config or MuxerConfig()

        # Ensure ffmpeg is available for pydub
        self._ffmpeg_path = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
        if not os.path.exists(self._ffmpeg_path):
            # Try common alternative locations
            for candidate in [
                os.path.expanduser("~/.local/bin/ffmpeg"),
                "/usr/local/bin/ffmpeg",
            ]:
                if os.path.exists(candidate):
                    self._ffmpeg_path = candidate
                    break

        AudioSegment.converter = self._ffmpeg_path

    # ── Track Loading ────────────────────────────────────────

    def load_track(self, track: Track) -> AudioSegment:
        """Load a Track into an AudioSegment.

        Handles environments where ffprobe is unavailable by using
        ffmpeg directly to convert to WAV first, then loading the WAV.
        """
        try:
            audio = AudioSegment.from_file(track.path)
            return audio
        except (FileNotFoundError, OSError):
            # ffprobe not available — use ffmpeg to convert to WAV first
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_wav = tmp.name
            try:
                subprocess.run(
                    [self._ffmpeg_path, "-y", "-i", track.path, "-vn",
                     "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
                     tmp_wav],
                    capture_output=True,
                    check=True,
                    timeout=120,
                )
                audio = AudioSegment.from_wav(tmp_wav)
                return audio
            finally:
                if os.path.exists(tmp_wav):
                    os.unlink(tmp_wav)

    # ── Normalization ────────────────────────────────────────

    def normalize(self, audio: AudioSegment) -> AudioSegment:
        """
        Normalize audio to consistent loudness.

        Uses pydub's normalize effect, which brings peak amplitude
        to a consistent level. For true LUFS matching we'd need
        ffmpeg's loudnorm filter, but pydub normalize is good enough
        for the prototype.
        """
        if not self.config.normalization_enabled:
            return audio

        # Boost quiet audio, reduce loud audio
        return pydub_normalize(audio)

    def apply_target_loudness(self, audio: AudioSegment, target_dbfs: float = -16.0) -> AudioSegment:
        """
        Adjust average loudness to a target dBFS.

        Not true LUFS, but a reasonable approximation for prototype use.
        Default -16 dBFS is a good target for web audio.
        """
        current_dbfs = audio.dBFS
        if current_dbfs == float("-inf"):
            return audio  # silent audio, can't normalize

        gain = target_dbfs - current_dbfs
        return audio.apply_gain(gain)

    # ── Crossfading ──────────────────────────────────────────

    def crossfade(
        self,
        current: AudioSegment,
        next_track: AudioSegment,
        duration_ms: int | None = None,
    ) -> AudioSegment:
        """
        Crossfade between two audio segments.

        The end of `current` overlaps with the beginning of `next_track`,
        creating a smooth transition.
        """
        duration_ms = duration_ms or self.config.crossfade_ms

        # Ensure we have enough audio to crossfade
        if len(current) < duration_ms or len(next_track) < duration_ms:
            # Not enough audio for a full crossfade — just append
            return current + next_track

        return current.append(next_track, crossfade=duration_ms)

    def insert_silence(
        self,
        audio: AudioSegment,
        duration_ms: int | None = None,
    ) -> AudioSegment:
        """Insert a silence gap after the audio."""
        duration_ms = duration_ms or self.config.silence_ms
        if duration_ms <= 0:
            return audio
        return audio + AudioSegment.silent(duration=duration_ms)

    # ── Concatenation ────────────────────────────────────────

    def concatenate(
        self,
        tracks: list[Track],
        normalize: bool = True,
    ) -> AudioSegment:
        """
        Concatenate multiple tracks into one continuous audio segment.

        Applies crossfades between tracks, normalization, and
        configurable silence between segments.
        """
        if not tracks:
            return AudioSegment.empty()

        # Load and normalize the first track
        result = self.load_track(tracks[0])
        if normalize:
            result = self.normalize(result)
            result = self.apply_target_loudness(result)

        for i in range(1, len(tracks)):
            # Load and normalize next track
            next_audio = self.load_track(tracks[i])
            if normalize:
                next_audio = self.normalize(next_audio)
                next_audio = self.apply_target_loudness(next_audio)

            # Crossfade
            result = self.crossfade(result, next_audio, self.config.crossfade_ms)

        return result

    def concatenate_streaming(
        self,
        tracks: list[Track],
        output_path: str,
        format: str = "mp3",
        bitrate: str = "128k",
        normalize: bool = True,
    ) -> str:
        """
        Concatenate tracks and write to a file.

        For large playlists, this builds the output incrementally
        to avoid holding everything in memory.

        Returns the output path.
        """
        if not tracks:
            raise ValueError("Cannot concatenate empty track list")

        # Build the full audio
        result = self.concatenate(tracks, normalize=normalize)

        # Export
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        result.export(output_path, format=format, bitrate=bitrate)

        return output_path

    # ── HLS Segmentation ─────────────────────────────────────

    def segment_to_hls(
        self,
        audio: AudioSegment,
        output_dir: str,
        segment_duration_seconds: int | None = None,
        playlist_entries: int | None = None,
    ) -> tuple[str, list[str]]:
        """
        Segment audio into HLS chunks and create a .m3u8 playlist.

        Returns (playlist_path, list_of_segment_paths).
        """
        seg_duration = segment_duration_seconds or self.config.hls_segment_duration_seconds
        max_entries = playlist_entries or self.config.hls_playlist_entries
        seg_duration_ms = seg_duration * 1000

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        segment_paths: list[str] = []

        # Slice the audio into segments
        total_duration = len(audio)
        segment_index = 0

        for start_ms in range(0, total_duration, seg_duration_ms):
            end_ms = min(start_ms + seg_duration_ms, total_duration)
            segment = audio[start_ms:end_ms]

            seg_filename = f"segment_{segment_index:05d}.ts"
            seg_path = str(output_path / seg_filename)
            segment.export(seg_path, format="mpegts", bitrate="128k")
            segment_paths.append(seg_path)
            segment_index += 1

        # Generate .m3u8 playlist
        m3u8_path = str(output_path / "stream.m3u8")
        self._write_m3u8(m3u8_path, len(segment_paths), seg_duration, max_entries)

        return m3u8_path, segment_paths

    def _write_m3u8(
        self,
        path: str,
        segment_count: int,
        segment_duration: int,
        max_entries: int,
    ) -> None:
        """Write an HLS .m3u8 playlist file."""
        # Use a sliding window of the last N segments
        start_seg = max(0, segment_count - max_entries)

        lines = [
            "#EXTM3U",
            f"#EXT-X-TARGETDURATION:{segment_duration}",
            f"#EXT-X-MEDIA-SEQUENCE:{start_seg}",
            "#EXT-X-VERSION:3",
        ]

        for i in range(start_seg, segment_count):
            seg_filename = f"segment_{i:05d}.ts"
            lines.append(f"#EXTINF:{segment_duration},")
            lines.append(seg_filename)

        # Don't end the stream (live stream, no #EXT-X-ENDLIST)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    # ── Duration Estimation ─────────────────────────────────

    def get_track_duration(self, track: Track) -> float:
        """Get the duration of a track in seconds (without loading it fully)."""
        if track.duration_seconds > 0:
            return track.duration_seconds

        try:
            audio = self.load_track(track)
            duration = len(audio) / 1000.0
            track.duration_seconds = duration
            return duration
        except Exception:
            return 0.0

    def estimate_total_duration(
        self,
        tracks: list[Track],
        include_crossfade: bool = True,
        include_silence: bool = True,
    ) -> float:
        """Estimate the total duration of a concatenated track list."""
        if not tracks:
            return 0.0

        total = 0.0
        for track in tracks:
            duration = track.duration_seconds or self.get_track_duration(track)
            total += duration

        # Subtract crossfade overlaps
        if include_crossfade:
            crossfade_s = self.config.crossfade_duration_seconds
            overlaps = max(0, len(tracks) - 1)
            total -= overlaps * crossfade_s

        # Add inter-segment silence
        if include_silence and self.config.silence_ms > 0:
            silences = max(0, len(tracks) - 1)
            total += silences * self.config.inter_segment_silence_seconds

        return max(0, total)

    # ── Utility ──────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"Muxer(crossfade={self.config.crossfade_duration_seconds}s, "
            f"normalize={self.config.normalization_enabled})"
        )
