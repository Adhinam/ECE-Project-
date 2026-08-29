"""Legacy path — re-exports from pipeline.reid."""

from pipeline.reid import (
    LightweightReID,
    ReentryCoordinator,
    ReentryMatch,
    ReIDSettings,
    load_reid_settings,
)

__all__ = [
    "LightweightReID",
    "ReentryCoordinator",
    "ReentryMatch",
    "ReIDSettings",
    "load_reid_settings",
]
