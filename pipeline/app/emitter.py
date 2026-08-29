"""Legacy path — re-exports structured emitter from pipeline.emit."""

from pipeline.emit import AppendOnlyEventLog, EmitSettings, StructuredEventEmitter
from pipeline.schemas import EventEnvelope, build_event, event_to_jsonl_line

__all__ = [
    "AppendOnlyEventLog",
    "EmitSettings",
    "StructuredEventEmitter",
    "EventEnvelope",
    "build_event",
    "event_to_jsonl_line",
]
