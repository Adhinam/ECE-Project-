"""Abstract interfaces for pipeline components — enables mocking and swapping implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np

from schemas.events import EventEnvelope


@dataclass
class Detection:
    bbox: tuple[int, int, int, int]
    confidence: float
    class_id: int = 0


@dataclass
class Track:
    track_id: int
    bbox: tuple[int, int, int, int]
    global_person_id: str | None = None
    is_staff: bool = False


class IDetector(ABC):
    @abstractmethod
    def detect(self, frame: np.ndarray) -> list[Detection]:
        ...


class ITracker(ABC):
    @abstractmethod
    def update(self, detections: list[Detection], frame: np.ndarray) -> list[Track]:
        ...


class IReIdentifier(ABC):
    @abstractmethod
    def assign_global_id(self, track: Track, crop: np.ndarray) -> str:
        ...


class IEventEmitter(ABC):
    @abstractmethod
    def emit(self, event: EventEnvelope) -> None:
        ...

    @abstractmethod
    def flush(self) -> None:
        ...


class IFrameSource(ABC):
    @abstractmethod
    def read(self) -> tuple[bool, np.ndarray | None]:
        ...

    @abstractmethod
    def release(self) -> None:
        ...
