"""
scheduler.py — Time-of-day content scheduling for LucidDreamer.AI.

Decides what plays when. Takes the current time, the playlist, and
a schedule config to produce a "now playing" queue.

Show schedule (America/Anchorage):
  Morning (6-10):    Fleet Radio morning show, dawn broadcasts, energizing
  Midday (10-14):    Essays, interviews, longer pieces
  Afternoon (14-18): Radio theater, creative pieces, remix table sessions
  Evening (18-22):   Tap sessions, open mic, live-feel content
  Night (22-6):      Ambient, music-heavy, quiet pieces, overnight dispatch

"Nemotron's warning: temporal coherence drift is the systemic risk."
The scheduler is the first line of defense — it matches content mood
to time of day, preventing the stream from drifting into
context-inappropriate territory at 3 AM.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from playlist import Playlist, Track


@dataclass
class ScheduleSlot:
    """A time-of-day programming slot."""
    name: str
    start_hour: int           # 0-23
    end_hour: int             # 0-23 (wraps past midnight if end < start)
    mood: list[str] = field(default_factory=list)
    min_quality: float = 0.0
    max_duration_seconds: float | None = None
    description: str = ""

    def contains_hour(self, hour: int) -> bool:
        """Check if an hour falls within this slot."""
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour < self.end_hour
        else:
            # Wraps midnight (e.g., 22 to 6)
            return hour >= self.start_hour or hour < self.end_hour

    def duration_hours(self) -> float:
        if self.start_hour <= self.end_hour:
            return self.end_hour - self.start_hour
        else:
            return (24 - self.start_hour) + self.end_hour

    def __repr__(self) -> str:
        return f"ScheduleSlot({self.name}, {self.start_hour:02d}-{self.end_hour:02d}, mood={self.mood})"


class Scheduler:
    """
    Decides what plays when.

    Given a playlist and a set of schedule slots, produces a queue
    of tracks appropriate for the current time of day.
    """

    # Default schedule matching the station's programming
    DEFAULT_SLOTS = [
        ScheduleSlot(
            name="Morning Watch",
            start_hour=6,
            end_hour=10,
            mood=["energizing", "dawn", "announcement", "warm"],
            min_quality=0.3,
            max_duration_seconds=600,
            description="Fleet Radio morning show, dawn broadcasts, energizing pieces",
        ),
        ScheduleSlot(
            name="Midday Essays",
            start_hour=10,
            end_hour=14,
            mood=["essay", "interview", "educational", "thoughtful"],
            min_quality=0.4,
            max_duration_seconds=1800,
            description="Essays, interviews, longer pieces",
        ),
        ScheduleSlot(
            name="Afternoon Theater",
            start_hour=14,
            end_hour=18,
            mood=["drama", "creative", "experimental", "playful"],
            min_quality=0.3,
            max_duration_seconds=1200,
            description="Radio theater, creative pieces, remix table sessions",
        ),
        ScheduleSlot(
            name="Evening Tap",
            start_hour=18,
            end_hour=22,
            mood=["live", "conversational", "open_mic", "warm"],
            min_quality=0.4,
            max_duration_seconds=1800,
            description="Tap sessions, open mic, live-feel content",
        ),
        ScheduleSlot(
            name="Overnight Dispatch",
            start_hour=22,
            end_hour=6,
            mood=["ambient", "quiet", "meditative", "music_heavy"],
            min_quality=0.2,
            max_duration_seconds=2400,
            description="Ambient, music-heavy, quiet pieces, overnight dispatch",
        ),
    ]

    def __init__(
        self,
        playlist: Playlist,
        slots: list[ScheduleSlot] | None = None,
        coherence_anchor_interval: int = 8,
        coherence_anchor_min_quality: float = 0.8,
    ):
        self.playlist = playlist
        self.slots = slots or list(self.DEFAULT_SLOTS)
        self.anchor_interval = coherence_anchor_interval
        self.anchor_min_quality = coherence_anchor_min_quality
        self._tracks_since_anchor = 0

    def _current_hour(self, now: float | None = None) -> int:
        """Get the current hour (0-23)."""
        if now is None:
            now = time.time()
        return datetime.fromtimestamp(now).hour

    def get_current_slot(self, now: float | None = None) -> ScheduleSlot:
        """
        Get the schedule slot for the current time.

        Falls back to the overnight slot if no slot matches
        (shouldn't happen with default config, but safety first).
        """
        hour = self._current_hour(now)
        for slot in self.slots:
            if slot.contains_hour(hour):
                return slot

        # Fallback: overnight
        for slot in self.slots:
            if slot.name == "Overnight Dispatch":
                return slot

        # Last resort: first slot
        return self.slots[0]

    def get_slot_by_name(self, name: str) -> ScheduleSlot | None:
        """Look up a slot by name."""
        for slot in self.slots:
            if slot.name.lower() == name.lower():
                return slot
        return None

    def get_next_slot(self, now: float | None = None) -> ScheduleSlot:
        """Get the slot that comes after the current one."""
        current = self.get_current_slot(now)
        current_idx = self.slots.index(current)
        next_idx = (current_idx + 1) % len(self.slots)
        return self.slots[next_idx]

    def now_playing_queue(
        self,
        size: int = 5,
        now: float | None = None,
        rng: random.Random | None = None,
    ) -> list[Track]:
        """
        Produce a queue of tracks for the current time slot.

        Inserts a coherence anchor every N tracks to guard against
        temporal drift.
        """
        now = now if now is not None else time.time()
        rng = rng or random.Random()

        slot = self.get_current_slot(now)
        queue: list[Track] = []

        # Temporarily build queue using slot constraints
        needed = size
        while needed > 0:
            # Check if we need an anchor
            if self._tracks_since_anchor >= self.anchor_interval:
                anchor = self.playlist.get_anchor_track(
                    min_quality=self.anchor_min_quality,
                    now=now,
                )
                if anchor:
                    queue.append(anchor)
                    needed -= 1
                    self._tracks_since_anchor = 0
                    continue

            # Normal selection
            track = self.playlist.select_next(
                mood_filters=slot.mood,
                min_quality=slot.min_quality,
                max_duration=slot.max_duration_seconds,
                now=now,
                rng=rng,
            )

            if track is None:
                # Relax constraints if nothing found
                track = self.playlist.select_next(
                    min_quality=0.0,
                    now=now,
                    rng=rng,
                )

            if track is None:
                break

            queue.append(track)
            self._tracks_since_anchor += 1
            needed -= 1

            # Mark as temporarily played so next pick differs
            self.playlist._play_history.append((track, now))

        # Clean up temporary history entries we added
        # (only keep entries that were there before)
        # Actually, select_next already skips recently played via _play_history,
        # so we need to clean the entries we just added.
        # We do this by trimming to entries older than our additions.
        # Simpler approach: build queue via build_queue with slot constraints.
        # Let's just use build_queue for cleanliness.

        # Reset and use build_queue
        self._tracks_since_anchor = 0  # reset for next call

        # Actually, let's do this more cleanly:
        # Use playlist.build_queue with the slot's constraints
        return self._build_queue_with_anchors(size, slot, now, rng)

    def _build_queue_with_anchors(
        self,
        size: int,
        slot: ScheduleSlot,
        now: float,
        rng: random.Random,
    ) -> list[Track]:
        """Build a queue, inserting coherence anchors as needed."""
        queue: list[Track] = []
        tracks_since_anchor = self._tracks_since_anchor
        original_history = list(self.playlist._play_history)

        while len(queue) < size:
            # Anchor insertion
            if tracks_since_anchor >= self.anchor_interval:
                anchor = self.playlist.get_anchor_track(
                    min_quality=self.anchor_min_quality,
                    now=now,
                )
                if anchor:
                    queue.append(anchor)
                    tracks_since_anchor = 0
                    self.playlist._play_history.append((anchor, now))
                    continue

            # Normal track selection
            track = self.playlist.select_next(
                mood_filters=slot.mood,
                min_quality=slot.min_quality,
                max_duration=slot.max_duration_seconds,
                now=now,
                rng=rng,
            )

            if track is None:
                # Relax all constraints
                track = self.playlist.select_next(
                    min_quality=0.0,
                    now=now,
                    rng=rng,
                )

            if track is None:
                break

            queue.append(track)
            tracks_since_anchor += 1
            self.playlist._play_history.append((track, now))

        # Restore history (queue building is non-destructive)
        self.playlist._play_history = original_history

        return queue

    def mark_played(self, track: Track, now: float | None = None) -> None:
        """Record playback and update anchor counter."""
        self.playlist.mark_played(track, now)
        self._tracks_since_anchor += 1

    def get_now_playing_info(self, track: Track, now: float | None = None) -> dict:
        """Return metadata about what's currently playing."""
        slot = self.get_current_slot(now)
        return {
            "title": track.title,
            "show": slot.name,
            "character": track.character or "Unknown",
            "mood": track.mood_tags or slot.mood,
            "quality": track.quality_score,
            "duration": track.duration_seconds,
            "slot_description": slot.description,
            "timestamp": now or time.time(),
        }

    def list_schedule(self) -> list[dict]:
        """List all schedule slots as dictionaries."""
        return [
            {
                "name": slot.name,
                "start_hour": slot.start_hour,
                "end_hour": slot.end_hour,
                "mood": slot.mood,
                "min_quality": slot.min_quality,
                "description": slot.description,
            }
            for slot in self.slots
        ]

    def __repr__(self) -> str:
        return f"Scheduler({len(self.slots)} slots, playlist={self.playlist})"
