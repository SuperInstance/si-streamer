"""
playlist.py — Playlist management for LucidDreamer.AI streamer.

Manages a collection of audio tracks with weighted scoring,
rotation rules, and schedule-aware selection.

Pure Python. No external audio dependencies — this is the brain,
not the hands.
"""

from __future__ import annotations

import hashlib
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Track:
    """A single audio track in the playlist."""
    path: str
    title: str
    duration_seconds: float = 0.0
    quality_score: float = 0.5         # 0.0 to 1.0
    mood_tags: list[str] = field(default_factory=list)
    show_name: str = ""
    character: str = ""
    created_at: float = 0.0            # unix timestamp
    played_count: int = 0
    last_played: float = 0.0           # unix timestamp of last play

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def extension(self) -> str:
        return os.path.splitext(self.path)[1].lower()

    @property
    def is_audio(self) -> bool:
        return self.extension in {".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"}

    def fingerprint(self) -> str:
        """Stable hash for dedup and logging."""
        h = hashlib.md5(f"{self.title}:{self.path}".encode())
        return h.hexdigest()[:12]

    def __repr__(self) -> str:
        return f"Track({self.title!r}, q={self.quality_score:.2f}, dur={self.duration_seconds:.0f}s)"


class Playlist:
    """
    Weighted, schedule-aware playlist.

    Pulls audio files from a directory, scores them, and selects
    tracks based on quality, recency, mood, and rotation rules.

    The scoring system is the guardrail against Nemotron's drift warning:
    higher-quality tracks act as gravity wells that keep the stream
    in a recognizable semantic basin.
    """

    SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"}

    def __init__(
        self,
        audio_dir: str | None = None,
        tracks: list[Track] | None = None,
        no_repeat_window_minutes: int = 240,
        min_queue_size: int = 3,
        max_queue_size: int = 10,
        weight_by_quality: bool = True,
        weight_by_recency: bool = True,
        recency_boost_multiplier: float = 1.5,
    ):
        self.audio_dir = audio_dir
        self.no_repeat_window = no_repeat_window_minutes * 60  # convert to seconds
        self.min_queue_size = min_queue_size
        self.max_queue_size = max_queue_size
        self.weight_by_quality = weight_by_quality
        self.weight_by_recency = weight_by_recency
        self.recency_boost = recency_boost_multiplier

        self._tracks: list[Track] = []
        self._play_history: list[tuple[Track, float]] = []  # (track, played_at)

        if tracks:
            self._tracks = list(tracks)
        elif audio_dir:
            self.load_from_directory(audio_dir)

    # ── Loading ──────────────────────────────────────────────

    def load_from_directory(self, directory: str) -> None:
        """Load all audio files from a directory as Track objects."""
        self.audio_dir = directory
        directory_path = Path(directory)
        if not directory_path.exists():
            raise FileNotFoundError(f"Audio directory not found: {directory}")

        for filepath in sorted(directory_path.iterdir()):
            if filepath.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                title = filepath.stem.replace("-", " ").replace("_", " ").title()
                track = Track(
                    path=str(filepath),
                    title=title,
                    created_at=filepath.stat().st_mtime,
                )
                self._tracks.append(track)

    def add_track(self, track: Track) -> None:
        """Manually add a track to the playlist."""
        self._tracks.append(track)

    def remove_track(self, path: str) -> bool:
        """Remove a track by path. Returns True if removed."""
        before = len(self._tracks)
        self._tracks = [t for t in self._tracks if t.path != path]
        return len(self._tracks) < before

    # ── Properties ───────────────────────────────────────────

    @property
    def tracks(self) -> list[Track]:
        """All tracks in the playlist (read-only view)."""
        return list(self._tracks)

    @property
    def size(self) -> int:
        return len(self._tracks)

    @property
    def total_duration(self) -> float:
        return sum(t.duration_seconds for t in self._tracks)

    # ── Scoring ──────────────────────────────────────────────

    def _is_recently_played(self, track: Track, now: float) -> bool:
        """Check if track was played within the no-repeat window."""
        cutoff = now - self.no_repeat_window
        for played_track, played_at in self._play_history:
            if played_track.path == track.path and played_at > cutoff:
                return True
        return False

    def _score_track(
        self,
        track: Track,
        mood_filters: list[str] | None,
        min_quality: float,
        now: float,
    ) -> float:
        """
        Score a track for selection probability.

        Higher score = more likely to play.
        Considers: quality, mood match, recency boost, play count penalty.
        """
        # Hard filter: quality floor
        if track.quality_score < min_quality:
            return 0.0

        # Hard filter: recently played
        if self._is_recently_played(track, now):
            return 0.0

        # Hard filter: mood mismatch (if mood filters specified)
        if mood_filters:
            if not any(tag in mood_filters for tag in track.mood_tags):
                # Not a hard zero — just heavily penalized.
                # A track with no mood tags can still play, just rarely.
                if track.mood_tags:
                    return 0.0

        score = 1.0

        # Quality weighting
        if self.weight_by_quality:
            score *= (0.3 + track.quality_score * 0.7)

        # Recency boost (newer content gets a boost)
        if self.weight_by_recency and track.created_at > 0:
            age_days = (now - track.created_at) / 86400
            if age_days < 7:  # within the last week
                score *= self.recency_boost

        # Play count penalty (overplayed tracks lose weight)
        if track.played_count > 0:
            score *= max(0.1, 1.0 / (1.0 + track.played_count * 0.15))

        # Mood match bonus
        if mood_filters and track.mood_tags:
            matches = len(set(track.mood_tags) & set(mood_filters))
            if matches > 0:
                score *= (1.0 + matches * 0.25)

        return score

    # ── Selection ────────────────────────────────────────────

    def get_eligible_tracks(
        self,
        mood_filters: list[str] | None = None,
        min_quality: float = 0.0,
        max_duration: float | None = None,
        now: float | None = None,
    ) -> list[Track]:
        """Get all tracks eligible for play right now (filters applied)."""
        now = now if now is not None else time.time()
        eligible = []
        for track in self._tracks:
            if max_duration and track.duration_seconds > max_duration:
                continue
            if self._is_recently_played(track, now):
                continue
            if track.quality_score < min_quality:
                continue
            if mood_filters and track.mood_tags:
                if not any(tag in mood_filters for tag in track.mood_tags):
                    continue
            eligible.append(track)
        return eligible

    def select_next(
        self,
        mood_filters: list[str] | None = None,
        min_quality: float = 0.0,
        max_duration: float | None = None,
        now: float | None = None,
        rng: random.Random | None = None,
    ) -> Track | None:
        """
        Select the next track using weighted random sampling.

        Returns None if no tracks are eligible.
        """
        now = now if now is not None else time.time()
        rng = rng or random.Random()

        candidates: list[Track] = []
        weights: list[float] = []

        for track in self._tracks:
            if max_duration and track.duration_seconds > max_duration and track.duration_seconds > 0:
                continue
            weight = self._score_track(track, mood_filters, min_quality, now)
            if weight > 0:
                candidates.append(track)
                weights.append(weight)

        if not candidates:
            # Fallback: relax the no-repeat rule
            for track in self._tracks:
                if max_duration and track.duration_seconds > max_duration and track.duration_seconds > 0:
                    continue
                if track.quality_score < min_quality:
                    continue
                candidates.append(track)
                weights.append(max(0.01, 0.3 + track.quality_score * 0.3))

        if not candidates:
            return None

        # Weighted random choice
        chosen = rng.choices(candidates, weights=weights, k=1)[0]
        return chosen

    def build_queue(
        self,
        size: int | None = None,
        mood_filters: list[str] | None = None,
        min_quality: float = 0.0,
        max_duration: float | None = None,
        now: float | None = None,
        rng: random.Random | None = None,
    ) -> list[Track]:
        """
        Build a queue of upcoming tracks.

        Temporarily marks tracks as "played" during selection so the
        no-repeat rule applies within the queue itself.
        """
        now = now if now is not None else time.time()
        rng = rng or random.Random()
        size = size or self.min_queue_size
        size = min(size, self.max_queue_size)

        queue: list[Track] = []
        original_history = list(self._play_history)

        for _ in range(size):
            track = self.select_next(
                mood_filters=mood_filters,
                min_quality=min_quality,
                max_duration=max_duration,
                now=now,
                rng=rng,
            )
            if track is None:
                break
            queue.append(track)
            # Temporarily record so next selection avoids repeat
            self._play_history.append((track, now))

        # Restore original history (queue building doesn't actually play)
        self._play_history = original_history
        return queue

    # ── Playback tracking ────────────────────────────────────

    def mark_played(self, track: Track, played_at: float | None = None) -> None:
        """Record that a track was played."""
        played_at = played_at if played_at is not None else time.time()
        track.played_count += 1
        track.last_played = played_at
        self._play_history.append((track, played_at))

        # Trim old history
        cutoff = played_at - self.no_repeat_window
        self._play_history = [
            (t, ts) for t, ts in self._play_history if ts > cutoff
        ]

    # ── Coherence anchors ────────────────────────────────────

    def get_anchor_track(
        self,
        min_quality: float = 0.8,
        now: float | None = None,
    ) -> Track | None:
        """
        Get a high-quality anchor track for coherence checkpointing.

        This is the defense against temporal coherence drift.
        Every N tracks, we force a high-quality piece to reset
        the stream's semantic trajectory.

        "The dream that forgot it was dreaming. We anchor."
        """
        now = now if now is not None else time.time()
        best: Track | None = None
        best_score = 0.0

        for track in self._tracks:
            if track.quality_score < min_quality:
                continue
            if self._is_recently_played(track, now):
                continue
            # Prefer highest quality, least played
            score = track.quality_score / (1.0 + track.played_count * 0.2)
            if score > best_score:
                best = track
                best_score = score

        return best

    # ── Utility ──────────────────────────────────────────────

    def stats(self) -> dict:
        """Return playlist statistics."""
        return {
            "total_tracks": len(self._tracks),
            "total_duration_seconds": self.total_duration,
            "avg_quality": (
                sum(t.quality_score for t in self._tracks) / len(self._tracks)
                if self._tracks else 0.0
            ),
            "tracks_played": len({t.path for t, _ in self._play_history}),
            "history_size": len(self._play_history),
        }

    def __len__(self) -> int:
        return len(self._tracks)

    def __repr__(self) -> str:
        return f"Playlist({len(self._tracks)} tracks, {self.total_duration:.0f}s total)"
