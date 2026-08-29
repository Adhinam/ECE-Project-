"""Structured event emission — append-only JSONL, batching, validation, replay."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pipeline.schemas import (
    DedupKey,
    EventEnvelope,
    ValidationResult,
    dedup_key,
    event_to_jsonl_line,
    filter_duplicates,
    iter_jsonl_events,
    parse_jsonl_line,
    validate_batch,
    validate_event,
)
from schemas.challenge_events import envelope_to_challenge
from shared.logging import configure_logging, get_logger

logger = get_logger(__name__)


class EmitSettings(BaseSettings):
    """Configuration for the structured event emitter."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
        env_prefix="EMIT_",
    )

    output_dir: Path = Field(
        default=Path("data/events"),
        validation_alias="EVENTS_OUTPUT_DIR",
    )
    store_id: str = Field(default="store-001", validation_alias="PIPELINE_STORE_ID")
    camera_id: str = Field(default="cam-entrance", validation_alias="PIPELINE_CAMERA_ID")
    batch_size: int = Field(default=50, ge=1, validation_alias="INGEST_BATCH_SIZE")
    flush_on_every_event: bool = Field(default=False)
    dedup_in_memory: bool = Field(default=True, description="Skip duplicate event_ids in-session")
    max_dedup_keys: int = Field(default=100_000, ge=1000)
    fsync_on_flush: bool = Field(default=False)
    validate_on_emit: bool = Field(default=True)
    challenge_format: bool = Field(
        default=True,
        description="Write Purplle challenge JSON (visitor_id, timestamp) instead of internal envelope",
        validation_alias="CHALLENGE_FORMAT",
    )
    log_json: bool = True
    log_level: str = "INFO"


@dataclass
class FlushStats:
    """Statistics from a single flush operation."""

    path: Path
    events_written: int
    duplicates_skipped: int
    bytes_written: int = 0


class EventDedupStore:
    """
    In-memory set of event_ids for append-time deduplication.

    Replay-compatible: same event_id is rejected on second emit in-process.
    API ingest remains authoritative for cross-run idempotency.
    """

    def __init__(self, *, max_keys: int = 100_000) -> None:
        self._max_keys = max_keys
        self._seen: set[DedupKey] = set()
        self._order: deque[DedupKey] = deque()

    def __len__(self) -> int:
        return len(self._seen)

    def add(self, event_id: DedupKey) -> bool:
        """
        Register event_id.

        Returns True if new, False if duplicate.
        """
        if event_id in self._seen:
            return False
        self._seen.add(event_id)
        self._order.append(event_id)
        while len(self._order) > self._max_keys:
            old = self._order.popleft()
            self._seen.discard(old)
        return True

    def contains(self, event_id: DedupKey) -> bool:
        return event_id in self._seen


class AppendOnlyEventLog:
    """
    Append-only JSONL writer — one file per store/camera/day by default.

    Files are never modified in place; replay reads the same bytes.
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        store_id: str,
        camera_id: str,
        fsync_on_flush: bool = False,
    ) -> None:
        self._output_dir = output_dir.expanduser().resolve()
        self._store_id = store_id
        self._camera_id = camera_id
        self._fsync = fsync_on_flush
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, occurred_at: datetime | None = None) -> Path:
        """Resolve log path: {output_dir}/{store_id}/{date}/{camera_id}.jsonl"""
        ts = occurred_at or datetime.now(timezone.utc)
        day = ts.strftime("%Y-%m-%d")
        directory = self._output_dir / self._store_id / day
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{self._camera_id}.jsonl"

    def append_lines(self, path: Path, lines: list[str]) -> int:
        """Append pre-serialized JSONL lines; return bytes written."""
        if not lines:
            return 0
        payload = "".join(lines)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(payload)
            if self._fsync:
                fh.flush()
                import os

                os.fsync(fh.fileno())
        return len(payload.encode("utf-8"))


class StructuredEventEmitter:
    """
    Schema-validated, batched, append-only event emission engine.

    - Validates each event before buffering
    - Optional in-memory dedup by event_id
    - Flushes batches to JSONL
    - Supports replay via iter_events / load_all
    """

    def __init__(self, settings: EmitSettings | None = None) -> None:
        self._settings = settings or EmitSettings()
        self._buffer: list[EventEnvelope] = []
        self._log = AppendOnlyEventLog(
            self._settings.output_dir,
            store_id=self._settings.store_id,
            camera_id=self._settings.camera_id,
            fsync_on_flush=self._settings.fsync_on_flush,
        )
        self._dedup = EventDedupStore(max_keys=self._settings.max_dedup_keys) if self._settings.dedup_in_memory else None
        self._total_emitted = 0
        self._total_duplicates = 0
        self._current_log_path: Path | None = None

    @property
    def settings(self) -> EmitSettings:
        return self._settings

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    @property
    def current_log_path(self) -> Path | None:
        return self._current_log_path

    @property
    def stats(self) -> dict[str, int]:
        return {
            "total_emitted": self._total_emitted,
            "total_duplicates": self._total_duplicates,
            "buffered": len(self._buffer),
            "dedup_keys": len(self._dedup) if self._dedup else 0,
        }

    def emit(self, event: EventEnvelope) -> bool:
        """
        Queue an event for batch write.

        Returns False if validation fails or event is duplicate.
        """
        if self._settings.validate_on_emit:
            result = validate_event(event)
            if not result.valid:
                logger.error(
                    "event_validation_failed",
                    event_id=str(event.event_id),
                    issues=[i.model_dump() for i in result.issues],
                )
                return False

        if self._dedup is not None and not self._dedup.add(dedup_key(event)):
            self._total_duplicates += 1
            logger.debug("event_duplicate_skipped", event_id=str(event.event_id))
            return False

        self._buffer.append(event)
        if self._settings.flush_on_every_event or len(self._buffer) >= self._settings.batch_size:
            self.flush()
        return True

    def emit_many(self, events: list[EventEnvelope]) -> int:
        """Emit multiple events; return count accepted."""
        if self._settings.validate_on_emit:
            batch_result = validate_batch(events)
            if not batch_result.valid:
                logger.error("batch_validation_failed", issues=batch_result.issues)
                return 0
        accepted = 0
        for ev in events:
            if self.emit(ev):
                accepted += 1
        return accepted

    def flush(self) -> FlushStats | None:
        """Write buffered events to append-only JSONL."""
        if not self._buffer:
            return None

        occurred = self._buffer[0].occurred_at
        path = self._log.path_for(occurred)
        self._current_log_path = path

        if self._settings.challenge_format:
            lines = [envelope_to_challenge(e).model_dump_json() + "\n" for e in self._buffer]
        else:
            lines = [event_to_jsonl_line(e) for e in self._buffer]
        bytes_written = self._log.append_lines(path, lines)
        count = len(self._buffer)
        self._total_emitted += count

        logger.info(
            "events_flushed",
            count=count,
            path=str(path),
            bytes=bytes_written,
        )

        stats = FlushStats(
            path=path,
            events_written=count,
            duplicates_skipped=0,
            bytes_written=bytes_written,
        )
        self._buffer.clear()
        return stats

    def close(self) -> FlushStats | None:
        """Flush remaining buffer — call at end of pipeline run."""
        return self.flush()


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def iter_log_events(path: Path) -> Iterator[EventEnvelope]:
    """
    Replay-compatible iterator over a JSONL event log.

    Validates each line against EventEnvelope schema.
    """
    file_path = path.expanduser().resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"Event log not found: {file_path}")

    with file_path.open(encoding="utf-8") as fh:
        yield from iter_jsonl_events(fh)


def load_log_events(path: Path) -> list[EventEnvelope]:
    """Load entire JSONL file into memory."""
    return list(iter_log_events(path))


def iter_log_directory(
    directory: Path,
    *,
    pattern: str = "*.jsonl",
) -> Iterator[EventEnvelope]:
    """Iterate all JSONL files under a directory in sorted path order."""
    root = directory.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Directory not found: {root}")
    for file_path in sorted(root.rglob(pattern)):
        if file_path.is_file():
            yield from iter_log_events(file_path)


class EventReplayer:
    """
    Replay JSONL events to the ingestion API with batching and dedup reporting.

    Idempotent ingest: duplicate event_ids are accepted by API but counted here
    for observability when replaying into a test stack.
    """

    def __init__(
        self,
        *,
        api_url: str,
        batch_size: int = 50,
        api_key: str | None = None,
    ) -> None:
        self._api_url = api_url
        self._batch_size = batch_size
        self._api_key = api_key

    async def replay_path(self, path: Path) -> dict[str, int]:
        from ingest.client import IngestClient

        client = IngestClient(self._api_url, api_key=self._api_key)
        accepted = duplicates = rejected = 0

        batch: list[EventEnvelope] = []
        for event in iter_log_events(path):
            batch.append(event)
            if len(batch) >= self._batch_size:
                result = await client.post_batch(batch)
                accepted += result.accepted
                duplicates += result.duplicates
                rejected += result.rejected
                batch = []

        if batch:
            result = await client.post_batch(batch)
            accepted += result.accepted
            duplicates += result.duplicates
            rejected += result.rejected

        logger.info(
            "replay_complete",
            path=str(path),
            accepted=accepted,
            duplicates=duplicates,
            rejected=rejected,
        )
        return {
            "accepted": accepted,
            "duplicates": duplicates,
            "rejected": rejected,
        }

    async def replay_directory(self, directory: Path) -> dict[str, int]:
        totals = {"accepted": 0, "duplicates": 0, "rejected": 0}
        root = directory.expanduser().resolve()
        for file_path in sorted(root.rglob("*.jsonl")):
            stats = await self.replay_path(file_path)
            for k in totals:
                totals[k] += stats[k]
        return totals


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Event log replay and utilities")
    sub = parser.add_subparsers(dest="command")

    replay = sub.add_parser("replay", help="Replay JSONL to ingestion API")
    replay.add_argument("--input", type=Path, required=True)
    replay.add_argument("--api-url", default="http://localhost:8000")
    replay.add_argument("--batch-size", type=int, default=50)

    validate = sub.add_parser("validate", help="Validate a JSONL event log")
    validate.add_argument("--input", type=Path, required=True)

    dedup = sub.add_parser("dedup-check", help="Report duplicate event_ids in a log")
    dedup.add_argument("--input", type=Path, required=True)

    return parser


async def _main_async(args: argparse.Namespace) -> int:
    if args.command == "replay":
        replayer = EventReplayer(api_url=args.api_url, batch_size=args.batch_size)
        input_path = args.input.expanduser().resolve()
        if input_path.is_dir():
            await replayer.replay_directory(input_path)
        else:
            await replayer.replay_path(input_path)
        return 0

    if args.command == "validate":
        events = load_log_events(args.input)
        result = validate_batch(events)
        if result.valid:
            logger.info("validation_ok", events=len(events))
            return 0
        logger.error("validation_failed", issues=result.issues)
        return 1

    if args.command == "dedup-check":
        events = load_log_events(args.input)
        unique, dup_count = filter_duplicates(events)
        logger.info(
            "dedup_report",
            total=len(events),
            unique=len(unique),
            duplicates=dup_count,
        )
        return 0

    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    configure_logging(json_logs=True, log_level="INFO")
    if not args.command:
        parser.print_help()
        return 1
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
