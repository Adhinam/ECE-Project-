"""Legacy path — re-exports from pipeline.entry_exit."""

from pipeline.entry_exit import (
    EntryExitDetector,
    EntryExitRunner,
    EntryExitSettings,
    draw_entry_exit_overlay,
)

__all__ = [
    "EntryExitDetector",
    "EntryExitRunner",
    "EntryExitSettings",
    "draw_entry_exit_overlay",
]
