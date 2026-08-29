"""Visitor track state — centroids, history, lifecycle, and group-entry helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterator

from pydantic import BaseModel, Field


class TrackStatus(StrEnum):
    """Lifecycle state for a visitor track (orthogonal to ByteTrack internal state)."""

    ACTIVE = "active"
    LOST = "lost"  # temporarily missing — occlusion / dropped detection
    ENDED = "ended"  # exceeded timeout; history retained for entry/exit analytics


@dataclass(frozen=True)
class Centroid:
    """Image-space centroid in pixel coordinates."""

    x: float
    y: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    def distance_to(self, other: Centroid) -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5


def bbox_centroid(bbox_xyxy: tuple[int, int, int, int]) -> Centroid:
    """Compute bbox center — used for line-crossing and heatmap binning later."""
    x1, y1, x2, y2 = bbox_xyxy
    return Centroid(x=(x1 + x2) / 2.0, y=(y1 + y2) / 2.0)


def bbox_area(bbox_xyxy: tuple[int, int, int, int]) -> float:
    x1, y1, x2, y2 = bbox_xyxy
    return max(0.0, float(x2 - x1)) * max(0.0, float(y2 - y1))


@dataclass(frozen=True)
class TrackPoint:
    """One observation of a visitor on a single frame."""

    frame_index: int
    centroid: Centroid
    bbox_xyxy: tuple[int, int, int, int]
    confidence: float


class MovementSample(BaseModel):
    """Serializable movement sample for API / event layer (future)."""

    frame_index: int
    x: float
    y: float
    confidence: float


class VisitorMovementHistory(BaseModel):
    """
    Exposed movement history for a single track ID.

    Used by entry/exit and funnel logic in later pipeline stages.
    """

    track_id: int
    status: TrackStatus
    first_seen_frame: int
    last_seen_frame: int
    samples: list[MovementSample] = Field(default_factory=list)
    total_distance_px: float = 0.0

    @property
    def duration_frames(self) -> int:
        return max(0, self.last_seen_frame - self.first_seen_frame)


@dataclass
class VisitorTrack:
    """
    Mutable per-visitor state accumulated across frames.

    Maintains a bounded deque of TrackPoints for memory safety on long videos.
    """

    track_id: int
    status: TrackStatus = TrackStatus.ACTIVE
    first_seen_frame: int = 0
    last_seen_frame: int = 0
    lost_since_frame: int | None = None
    points: deque[TrackPoint] = field(default_factory=deque)
    max_points: int = 500

    def append_observation(self, point: TrackPoint) -> None:
        """Add a new observation and update lifecycle metadata."""
        if not self.points:
            self.first_seen_frame = point.frame_index
        self.last_seen_frame = point.frame_index
        self.points.append(point)
        while len(self.points) > self.max_points:
            self.points.popleft()

    def mark_active(self, frame_index: int) -> None:
        """Re-activate after occlusion recovery."""
        if self.status == TrackStatus.LOST:
            # TODO: emit occlusion_recovery event when event layer exists
            pass
        self.status = TrackStatus.ACTIVE
        self.lost_since_frame = None
        self.last_seen_frame = frame_index

    def mark_lost(self, frame_index: int) -> None:
        if self.status == TrackStatus.ENDED:
            return
        self.status = TrackStatus.LOST
        self.lost_since_frame = frame_index

    def mark_ended(self, frame_index: int) -> None:
        self.status = TrackStatus.ENDED
        self.last_seen_frame = max(self.last_seen_frame, frame_index)

    def movement_history(self) -> VisitorMovementHistory:
        """Export typed history for downstream analytics."""
        samples = [
            MovementSample(
                frame_index=p.frame_index,
                x=p.centroid.x,
                y=p.centroid.y,
                confidence=p.confidence,
            )
            for p in self.points
        ]
        total_dist = 0.0
        prev: Centroid | None = None
        for p in self.points:
            if prev is not None:
                total_dist += prev.distance_to(p.centroid)
            prev = p.centroid
        return VisitorMovementHistory(
            track_id=self.track_id,
            status=self.status,
            first_seen_frame=self.first_seen_frame,
            last_seen_frame=self.last_seen_frame,
            samples=samples,
            total_distance_px=total_dist,
        )

    def recent_centroids(self, n: int) -> list[Centroid]:
        """Last N centroids — used for trajectory visualization."""
        if n <= 0:
            return []
        return [p.centroid for p in list(self.points)[-n:]]


@dataclass(frozen=True)
class GroupEntryCandidate:
    """
    Multiple tracks appearing together within a frame window.

    Entry/exit module will consume this; tracking only detects the pattern.
    """

    frame_index: int
    track_ids: tuple[int, ...]

    @property
    def group_size(self) -> int:
        return len(self.track_ids)


class TrackHistoryStore:
    """
    Central registry of all visitor tracks for one video pass.

    Handles dropped-track timeouts and preserves history after tracks end.
    ByteTrack provides short-term occlusion matching; this store keeps longer
    movement history for analytics.
    """

    def __init__(
        self,
        *,
        track_timeout_frames: int,
        max_history_points: int = 500,
        group_entry_min_size: int = 2,
        group_entry_frame_window: int = 1,
    ) -> None:
        self._track_timeout_frames = track_timeout_frames
        self._max_history_points = max_history_points
        self._group_entry_min_size = group_entry_min_size
        self._group_entry_frame_window = group_entry_frame_window
        self._tracks: dict[int, VisitorTrack] = {}

    @property
    def tracks(self) -> dict[int, VisitorTrack]:
        return self._tracks

    def get(self, track_id: int) -> VisitorTrack | None:
        return self._tracks.get(track_id)

    def active_track_ids(self) -> list[int]:
        return [tid for tid, t in self._tracks.items() if t.status == TrackStatus.ACTIVE]

    def iter_histories(self, *, include_ended: bool = True) -> Iterator[VisitorMovementHistory]:
        for track in self._tracks.values():
            if not include_ended and track.status == TrackStatus.ENDED:
                continue
            yield track.movement_history()

    def update_frame(
        self,
        frame_index: int,
        observations: list[TrackPoint],
        *,
        track_ids: list[int],
    ) -> None:
        """
        Update store from current-frame tracked observations.

        Args:
            frame_index: Current video frame index
            observations: Aligned with track_ids (same length)
            track_ids: Stable ByteTrack IDs present this frame
        """
        if len(observations) != len(track_ids):
            raise ValueError("observations and track_ids must have the same length")

        seen: set[int] = set()
        for tid, point in zip(track_ids, observations, strict=True):
            seen.add(tid)
            track = self._tracks.get(tid)
            if track is None:
                track = VisitorTrack(
                    track_id=tid,
                    first_seen_frame=frame_index,
                    max_points=self._max_history_points,
                )
                self._tracks[tid] = track
            track.append_observation(point)
            track.mark_active(frame_index)

        # Dropped from ByteTrack output → LOST, then ENDED after timeout
        for tid, track in self._tracks.items():
            if tid in seen:
                continue
            if track.status == TrackStatus.ACTIVE:
                track.mark_lost(frame_index)
            elif track.status == TrackStatus.LOST and track.lost_since_frame is not None:
                if frame_index - track.lost_since_frame > self._track_timeout_frames:
                    track.mark_ended(frame_index)

    def detect_group_entries(self, frame_index: int) -> list[GroupEntryCandidate]:
        """
        Detect multiple new tracks starting on the same frame (group entry).

        A "new" track is one whose first_seen_frame is within the window of
        the current frame. Used later for entry-line analytics.
        """
        window = self._group_entry_frame_window
        new_ids = [
            tid
            for tid, t in self._tracks.items()
            if t.status == TrackStatus.ACTIVE and abs(t.first_seen_frame - frame_index) <= window
        ]
        if len(new_ids) < self._group_entry_min_size:
            return []
        return [
            GroupEntryCandidate(
                frame_index=frame_index,
                track_ids=tuple(sorted(new_ids)),
            )
        ]
