"""Store layout configuration schema — validates configs/store_layout.yaml."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Point(BaseModel):
    x: int
    y: int

    @classmethod
    def from_pair(cls, pair: list[int]) -> Point:
        return cls(x=pair[0], y=pair[1])


class EntryExitLine(BaseModel):
    id: str
    p1: list[int]
    p2: list[int]
    direction_in: str = "bottom"


class ZoneConfig(BaseModel):
    id: str
    polygon: list[list[int]]


class QueueConfig(BaseModel):
    id: str
    polygon: list[list[int]]
    max_capacity: int = 20


class StaffAreaConfig(BaseModel):
    id: str
    polygon: list[list[int]]


class StoreLayoutConfig(BaseModel):
    store_id: str
    frame_width: int = 1920
    frame_height: int = 1080
    entry_exit_lines: list[EntryExitLine] = Field(default_factory=list)
    zones: list[ZoneConfig] = Field(default_factory=list)
    queues: list[QueueConfig] = Field(default_factory=list)
    staff_areas: list[StaffAreaConfig] = Field(default_factory=list)
    dwell_threshold_seconds: int = 30
    reentry_window_seconds: int = 300
    heatmap_resolution: int = 32
