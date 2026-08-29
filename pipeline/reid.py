"""
Lightweight visitor re-identification — histogram + cosine similarity.

See pipeline/REID.md for limitations, edge cases, and scaling notes.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import cv2
import numpy as np
import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pipeline.entry_exit import VideoClock
from schemas.events import EventEnvelope, EventType, generate_event_id
from shared.logging import get_logger

logger = get_logger(__name__)

# HSV histogram bins (compact, CPU-only appearance model)
_HIST_BINS = (16, 8, 8)
_MIN_CROP_PX = 24


class ReIDSettings(BaseSettings):
    """Configuration for lightweight Re-ID (no deep model training)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
        env_prefix="REID_",
    )

    enabled: bool = Field(default=True, validation_alias="ENABLED")
    similarity_threshold: float = Field(
        default=0.72,
        ge=0.0,
        le=1.0,
        validation_alias="SIMILARITY_THRESHOLD",
    )
    histogram_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    cosine_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    reentry_timeout_seconds: float = Field(
        default=300.0,
        ge=1.0,
        validation_alias="REENTRY_TIMEOUT_SECONDS",
    )
    gallery_max_entries: int = Field(default=500, ge=10, validation_alias="GALLERY_MAX")
    min_confidence_floor: float = Field(default=0.5, ge=0.0, le=1.0)
    calibration_method: str = "histogram_cosine_v1"


@dataclass(frozen=True)
class AppearanceFeature:
    """Normalized appearance descriptor from an person crop."""

    histogram: np.ndarray  # flattened, L1-normalized HSV histogram
    vector: np.ndarray  # same vector for cosine similarity

    def to_dict(self) -> dict[str, Any]:
        return {
            "histogram": self.histogram.tolist(),
            "vector": self.vector.tolist(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppearanceFeature:
        return cls(
            histogram=np.asarray(data["histogram"], dtype=np.float32),
            vector=np.asarray(data["vector"], dtype=np.float32),
        )


@dataclass
class ExitedVisitorRecord:
    """Short-term memory of a visitor who exited (for re-entry matching)."""

    visitor_id: UUID
    exited_at: datetime
    feature: AppearanceFeature
    exit_event_id: UUID | None = None
    track_id: int | None = None
    exit_line_id: str | None = None


@dataclass(frozen=True)
class ReentryMatch:
    """Result of matching an entry appearance against exited-visitor memory."""

    visitor_id: UUID
    confidence: float
    histogram_score: float
    cosine_score: float
    prior_exit_event_id: UUID | None
    gap_seconds: float
    record: ExitedVisitorRecord


class LightweightReID:
    """
    CPU-only re-identification using HSV histogram correlation and cosine similarity.

    Graceful degradation: if disabled, misconfigured, or crop extraction fails, matching
    returns None and callers assign a fresh visitor UUID.
    """

    def __init__(self, settings: ReIDSettings | None = None) -> None:
        self._settings = settings or ReIDSettings()
        self._memory: deque[ExitedVisitorRecord] = deque(maxlen=self._settings.gallery_max_entries)
        self._degraded = False
        self._degrade_reason: str | None = None

        if not self._settings.enabled:
            self._set_degraded("disabled_via_config")

    @property
    def is_degraded(self) -> bool:
        return self._degraded or not self._settings.enabled

    @property
    def degrade_reason(self) -> str | None:
        return self._degrade_reason

    @property
    def exited_memory(self) -> list[ExitedVisitorRecord]:
        """Snapshot of temporary exited-visitor gallery (after TTL prune)."""
        return list(self._memory)

    def _set_degraded(self, reason: str) -> None:
        if not self._degraded:
            logger.warning("reid_degraded", reason=reason)
        self._degraded = True
        self._degrade_reason = reason

    def extract_feature(
        self,
        frame: np.ndarray,
        bbox_xyxy: tuple[int, int, int, int],
    ) -> AppearanceFeature | None:
        """
        Build appearance feature from a BGR frame and person bounding box.

        Returns None if crop is too small or extraction fails (graceful skip).
        """
        if self.is_degraded:
            return None
        try:
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = bbox_xyxy
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 - x1 < _MIN_CROP_PX or y2 - y1 < _MIN_CROP_PX:
                return None
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                return None
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1, 2], None, _HIST_BINS, [0, 180, 0, 256, 0, 256])
            cv2.normalize(hist, hist, norm_type=cv2.NORM_L1)
            vector = hist.flatten().astype(np.float32)
            if vector.sum() <= 0:
                return None
            return AppearanceFeature(histogram=vector, vector=vector)
        except Exception as exc:
            logger.debug("reid_extract_failed", error=str(exc))
            return None

    @staticmethod
    def histogram_similarity(a: AppearanceFeature, b: AppearanceFeature) -> float:
        """OpenCV correlation compare — mapped to [0, 1]."""
        score = float(cv2.compareHist(a.histogram, b.histogram, cv2.HISTCMP_CORREL))
        return max(0.0, min(1.0, score))

    @staticmethod
    def cosine_similarity(a: AppearanceFeature, b: AppearanceFeature) -> float:
        va, vb = a.vector, b.vector
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if denom <= 1e-9:
            return 0.0
        return max(0.0, min(1.0, float(np.dot(va, vb) / denom)))

    def combined_score(self, hist: float, cos: float) -> float:
        w_h = self._settings.histogram_weight
        w_c = self._settings.cosine_weight
        total = w_h + w_c
        if total <= 0:
            return (hist + cos) / 2.0
        return (w_h * hist + w_c * cos) / total

    def register_exit(
        self,
        *,
        visitor_id: UUID,
        frame: np.ndarray | None,
        bbox_xyxy: tuple[int, int, int, int] | None,
        exited_at: datetime,
        exit_event_id: UUID | None = None,
        track_id: int | None = None,
        exit_line_id: str | None = None,
        feature: AppearanceFeature | None = None,
    ) -> bool:
        """
        Store exited visitor appearance in short-term memory.

        Returns True if a feature was stored.
        """
        if self.is_degraded:
            return False

        self._prune_expired(exited_at)

        feat = feature
        if feat is None and frame is not None and bbox_xyxy is not None:
            feat = self.extract_feature(frame, bbox_xyxy)
        if feat is None:
            logger.debug("reid_exit_not_stored", visitor_id=str(visitor_id), reason="no_feature")
            return False

        record = ExitedVisitorRecord(
            visitor_id=visitor_id,
            exited_at=exited_at,
            feature=feat,
            exit_event_id=exit_event_id,
            track_id=track_id,
            exit_line_id=exit_line_id,
        )
        self._memory.append(record)
        logger.info(
            "reid_exit_registered",
            visitor_id=str(visitor_id),
            gallery_size=len(self._memory),
        )
        return True

    def match_reentry(
        self,
        *,
        frame: np.ndarray | None,
        bbox_xyxy: tuple[int, int, int, int] | None,
        occurred_at: datetime,
        feature: AppearanceFeature | None = None,
    ) -> ReentryMatch | None:
        """
        Compare entry appearance against exited-visitor memory.

        Returns best match above threshold within reentry timeout, else None.
        """
        if self.is_degraded:
            return None

        self._prune_expired(occurred_at)

        feat = feature
        if feat is None and frame is not None and bbox_xyxy is not None:
            feat = self.extract_feature(frame, bbox_xyxy)
        if feat is None or not self._memory:
            return None

        best: ReentryMatch | None = None
        for record in self._memory:
            gap = (occurred_at - record.exited_at).total_seconds()
            if gap < 0 or gap > self._settings.reentry_timeout_seconds:
                continue
            h_score = self.histogram_similarity(feat, record.feature)
            c_score = self.cosine_similarity(feat, record.feature)
            combined = self.combined_score(h_score, c_score)
            if combined < self._settings.similarity_threshold:
                continue
            confidence = max(self._settings.min_confidence_floor, combined)
            if best is None or confidence > best.confidence:
                best = ReentryMatch(
                    visitor_id=record.visitor_id,
                    confidence=confidence,
                    histogram_score=h_score,
                    cosine_score=c_score,
                    prior_exit_event_id=record.exit_event_id,
                    gap_seconds=gap,
                    record=record,
                )

        if best is not None:
            logger.info(
                "reid_reentry_match",
                visitor_id=str(best.visitor_id),
                confidence=round(best.confidence, 3),
                histogram=round(best.histogram_score, 3),
                cosine=round(best.cosine_score, 3),
                gap_seconds=round(best.gap_seconds, 1),
            )
        return best

    def remove_visitor(self, visitor_id: UUID) -> None:
        """Remove all gallery entries for a visitor (after successful re-entry)."""
        self._memory = deque(
            (r for r in self._memory if r.visitor_id != visitor_id),
            maxlen=self._settings.gallery_max_entries,
        )

    def _prune_expired(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self._settings.reentry_timeout_seconds)
        fresh = [r for r in self._memory if r.exited_at >= cutoff]
        if len(fresh) < len(self._memory):
            logger.debug("reid_gallery_pruned", removed=len(self._memory) - len(fresh))
        self._memory = deque(fresh, maxlen=self._settings.gallery_max_entries)


def load_reid_settings(models_config_path: Path | None = None) -> ReIDSettings:
    """Load ReID block from configs/models.yaml merged with env."""
    settings = ReIDSettings()
    path = models_config_path or Path("configs/models.yaml")
    if not path.is_file():
        return settings
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    block = data.get("reid", {})
    if not block:
        return settings
    kwargs: dict[str, Any] = dict(block)
    if "gallery_ttl_seconds" in kwargs and "reentry_timeout_seconds" not in kwargs:
        kwargs["reentry_timeout_seconds"] = kwargs.pop("gallery_ttl_seconds")
    return ReIDSettings.model_validate({**settings.model_dump(), **kwargs})


@dataclass
class ReentryEventBuilder:
    """Build REENTRY EventEnvelope from a match."""

    store_id: str
    camera_id: str
    clock: VideoClock
    settings: ReIDSettings

    def build(
        self,
        match: ReentryMatch,
        *,
        track_id: int,
        frame_index: int,
        bbox_xyxy: tuple[int, int, int, int] | None,
        entry_line_id: str | None = None,
    ) -> EventEnvelope:
        return EventEnvelope(
            event_id=generate_event_id(
                store_id=self.store_id,
                camera_id=self.camera_id,
                event_type=EventType.REENTRY.value,
                track_id=track_id,
                frame_index=frame_index,
                subtype=str(match.visitor_id),
            ),
            event_type=EventType.REENTRY,
            store_id=self.store_id,
            camera_id=self.camera_id,
            occurred_at=self.clock.timestamp_for_frame(frame_index),
            track_id=track_id,
            global_person_id=str(match.visitor_id),
            is_staff=False,
            confidence=match.confidence,
            calibration_method=self.settings.calibration_method,
            bbox=bbox_xyxy,
            frame_index=frame_index,
            payload={
                "visitor_id": str(match.visitor_id),
                "prior_exit_event_id": str(match.prior_exit_event_id)
                if match.prior_exit_event_id
                else None,
                "gap_seconds": round(match.gap_seconds, 2),
                "histogram_similarity": round(match.histogram_score, 4),
                "cosine_similarity": round(match.cosine_score, 4),
                "combined_similarity": round(match.confidence, 4),
                "entry_line_id": entry_line_id,
            },
        )


class ReentryCoordinator:
    """
    Coordinates exit registration, entry matching, and REENTRY event emission.

    Intended to sit between vision pipeline frames and SessionEngine.
    """

    def __init__(
        self,
        *,
        store_id: str,
        camera_id: str,
        clock: VideoClock,
        reid: LightweightReID | None = None,
        settings: ReIDSettings | None = None,
    ) -> None:
        self._store_id = store_id
        self._camera_id = camera_id
        self._clock = clock
        self._settings = settings or load_reid_settings()
        self._reid = reid or LightweightReID(self._settings)
        self._builder = ReentryEventBuilder(
            store_id=store_id,
            camera_id=camera_id,
            clock=clock,
            settings=self._settings,
        )

    @property
    def reid(self) -> LightweightReID:
        return self._reid

    def on_visitor_exit(
        self,
        *,
        visitor_id: UUID,
        frame: np.ndarray | None,
        bbox_xyxy: tuple[int, int, int, int] | None,
        exit_event: EventEnvelope,
    ) -> None:
        self._reid.register_exit(
            visitor_id=visitor_id,
            frame=frame,
            bbox_xyxy=bbox_xyxy,
            exited_at=exit_event.occurred_at,
            exit_event_id=exit_event.event_id,
            track_id=exit_event.track_id,
            exit_line_id=str(exit_event.payload.get("line_id", "")) or None,
        )

    def check_reentry(
        self,
        *,
        frame: np.ndarray | None,
        bbox_xyxy: tuple[int, int, int, int],
        track_id: int,
        frame_index: int,
        occurred_at: datetime,
        entry_line_id: str | None = None,
    ) -> tuple[EventEnvelope | None, ReentryMatch | None]:
        """
        Attempt re-entry match before creating a new visitor session.

        Returns (REENTRY event or None, match or None).
        """
        match = self._reid.match_reentry(
            frame=frame,
            bbox_xyxy=bbox_xyxy,
            occurred_at=occurred_at,
        )
        if match is None:
            return None, None

        event = self._builder.build(
            match,
            track_id=track_id,
            frame_index=frame_index,
            bbox_xyxy=bbox_xyxy,
            entry_line_id=entry_line_id,
        )
        self._reid.remove_visitor(match.visitor_id)
        return event, match


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def feature_to_json(feature: AppearanceFeature) -> str:
    return json.dumps(feature.to_dict())


def feature_from_json(text: str) -> AppearanceFeature:
    return AppearanceFeature.from_dict(json.loads(text))


def exited_record_to_dict(record: ExitedVisitorRecord) -> dict[str, Any]:
    return {
        "visitor_id": str(record.visitor_id),
        "exited_at": record.exited_at.isoformat(),
        "exit_event_id": str(record.exit_event_id) if record.exit_event_id else None,
        "track_id": record.track_id,
        "exit_line_id": record.exit_line_id,
        "feature": record.feature.to_dict(),
    }
