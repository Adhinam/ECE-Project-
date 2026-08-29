"""Video I/O utilities, visualization, and path helpers for the detection pipeline."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from shared.logging import get_logger

if TYPE_CHECKING:
    from pipeline.detect import FrameDetections

logger = get_logger(__name__)

# COCO person class name used by YOLOv8
PERSON_CLASS_NAME = "person"


@dataclass(frozen=True)
class VideoMetadata:
    """Cached properties of an opened video source."""

    path: Path
    width: int
    height: int
    fps: float
    frame_count: int


@dataclass
class FrameReadResult:
    """Result of reading one frame, including drop/dirty flags."""

    ok: bool
    frame: np.ndarray | None
    frame_index: int
    dropped: bool = False
    reason: str | None = None


@dataclass
class VideoReaderStats:
    """Aggregated read statistics for a single video pass."""

    frames_read: int = 0
    frames_dropped: int = 0
    consecutive_drops: int = 0
    max_consecutive_drops: int = 0


@dataclass
class VideoReader:
    """
    OpenCV-backed frame iterator with graceful handling of dropped frames.

    A dropped frame is any failed `read()` or empty/invalid array. The reader
    logs warnings and aborts if consecutive drops exceed `max_consecutive_drops`.
    """

    path: Path
    max_consecutive_drops: int = 30
    stats: VideoReaderStats = field(default_factory=VideoReaderStats)

    _cap: cv2.VideoCapture | None = field(default=None, repr=False)
    _metadata: VideoMetadata | None = field(default=None, repr=False)
    _frame_index: int = field(default=-1, repr=False)

    def open(self) -> VideoMetadata:
        """Open the capture and return metadata; raises on failure."""
        resolved = self.path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Video file not found: {resolved}")

        cap = cv2.VideoCapture(str(resolved))
        if not cap.isOpened():
            raise RuntimeError(f"Failed to open video: {resolved}")

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        if width <= 0 or height <= 0:
            cap.release()
            raise RuntimeError(f"Invalid video dimensions {width}x{height}: {resolved}")

        self._cap = cap
        self._metadata = VideoMetadata(
            path=resolved,
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
        )
        self._frame_index = -1
        self.stats = VideoReaderStats()

        logger.info(
            "video_opened",
            path=str(resolved),
            width=width,
            height=height,
            fps=round(fps, 2),
            frame_count=frame_count,
        )
        return self._metadata

    @property
    def metadata(self) -> VideoMetadata:
        if self._metadata is None:
            raise RuntimeError("VideoReader is not open; call open() first")
        return self._metadata

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        logger.info(
            "video_closed",
            path=str(self.path),
            frames_read=self.stats.frames_read,
            frames_dropped=self.stats.frames_dropped,
            max_consecutive_drops=self.stats.max_consecutive_drops,
        )

    def __enter__(self) -> VideoReader:
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def frames(self) -> Iterator[FrameReadResult]:
        """
        Yield frames one at a time.

        Yields dropped markers without incrementing the logical frame index used
        for detection alignment (detector skips dropped frames).
        """
        if self._cap is None:
            raise RuntimeError("VideoReader is not open")

        while True:
            ok, raw = self._cap.read()
            self._frame_index += 1
            idx = self._frame_index

            if not ok or raw is None:
                # After at least one good frame, a failed read usually means EOF.
                if self.stats.frames_read > 0:
                    logger.debug("video_eof", path=str(self.path), frame_index=idx)
                    break
                self._register_drop(idx, "read_failed")
                yield FrameReadResult(
                    ok=False,
                    frame=None,
                    frame_index=idx,
                    dropped=True,
                    reason="read_failed",
                )
                if self.stats.consecutive_drops >= self.max_consecutive_drops:
                    logger.error(
                        "video_abort_consecutive_drops",
                        path=str(self.path),
                        consecutive=self.stats.consecutive_drops,
                        limit=self.max_consecutive_drops,
                    )
                    break
                continue

            if raw.size == 0 or raw.shape[0] == 0 or raw.shape[1] == 0:
                self._register_drop(idx, "empty_frame")
                yield FrameReadResult(ok=False, frame=None, frame_index=idx, dropped=True, reason="empty_frame")
                if self.stats.consecutive_drops >= self.max_consecutive_drops:
                    logger.error("video_abort_empty_frames", path=str(self.path))
                    break
                continue

            # Successful read — reset consecutive drop counter
            self.stats.consecutive_drops = 0
            self.stats.frames_read += 1
            yield FrameReadResult(ok=True, frame=raw, frame_index=idx, dropped=False)

        # Caller / context manager is responsible for close()

    def _register_drop(self, frame_index: int, reason: str) -> None:
        self.stats.frames_dropped += 1
        self.stats.consecutive_drops += 1
        self.stats.max_consecutive_drops = max(
            self.stats.max_consecutive_drops,
            self.stats.consecutive_drops,
        )
        logger.warning(
            "frame_dropped",
            path=str(self.path),
            frame_index=frame_index,
            reason=reason,
            consecutive_drops=self.stats.consecutive_drops,
        )


def iter_video_paths(source: Path, glob_pattern: str = "*.mp4") -> list[Path]:
    """
    Resolve a single file or expand a directory to sorted video paths.

    Raises:
        FileNotFoundError: source does not exist
        ValueError: source is a directory with no matching videos
    """
    resolved = source.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Video source not found: {resolved}")

    if resolved.is_file():
        if resolved.suffix.lower() != ".mp4":
            logger.warning("unexpected_extension", path=str(resolved), suffix=resolved.suffix)
        return [resolved]

    if resolved.is_dir():
        paths = sorted(resolved.glob(glob_pattern))
        if not paths:
            raise ValueError(f"No videos matching '{glob_pattern}' in {resolved}")
        return paths

    raise ValueError(f"Video source must be a file or directory: {resolved}")


def ensure_directory(path: Path | None) -> Path | None:
    """Create directory if path is set; return resolved path."""
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


class FpsThrottle:
    """Optional processing rate limiter — skips sleep when fps_limit is None."""

    def __init__(self, fps_limit: int | None) -> None:
        self._fps_limit = fps_limit
        self._frame_interval = (1.0 / fps_limit) if fps_limit else None
        self._last_tick: float | None = None

    def wait(self) -> None:
        if self._frame_interval is None:
            return
        now = time.perf_counter()
        if self._last_tick is not None:
            elapsed = now - self._last_tick
            sleep_for = self._frame_interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._last_tick = time.perf_counter()


@dataclass
class AnnotatedVideoWriter:
    """Write annotated frames to MP4 via OpenCV VideoWriter."""

    output_path: Path
    width: int
    height: int
    fps: float
    fourcc: int = field(default_factory=lambda: cv2.VideoWriter_fourcc(*"mp4v"))

    _writer: cv2.VideoWriter | None = field(default=None, repr=False)

    def open(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(
            str(self.output_path),
            self.fourcc,
            max(self.fps, 1.0),
            (self.width, self.height),
        )
        if not writer.isOpened():
            raise RuntimeError(f"Failed to create video writer: {self.output_path}")
        self._writer = writer
        logger.info("annotated_writer_opened", path=str(self.output_path))

    def write(self, frame: np.ndarray) -> None:
        if self._writer is None:
            raise RuntimeError("AnnotatedVideoWriter is not open")
        self._writer.write(frame)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None


def draw_detections(frame: np.ndarray, result: FrameDetections) -> np.ndarray:
    """
    Draw person bounding boxes and confidence scores on a copy of the frame.

    Args:
        frame: BGR image
        result: Detections for this frame

    Returns:
        Annotated BGR image (new array)
    """
    annotated = frame.copy()
    for det in result.detections:
        x1, y1, x2, y2 = det.bbox_xyxy
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = f"{PERSON_CLASS_NAME} {det.confidence:.2f}"
        cv2.putText(
            annotated,
            label,
            (x1, max(y1 - 8, 0)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )
    # HUD: frame index + count
    hud = f"frame={result.frame_index} persons={len(result.detections)}"
    cv2.putText(
        annotated,
        hud,
        (10, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return annotated


def show_frame(window_name: str, frame: np.ndarray, wait_ms: int = 1) -> bool:
    """
    Display a frame; return False if user pressed 'q' (quit visualization).

    TODO: support pause/resume hotkeys in visualization mode.
    """
    cv2.imshow(window_name, frame)
    key = cv2.waitKey(wait_ms) & 0xFF
    return key != ord("q")
