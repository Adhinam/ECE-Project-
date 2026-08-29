"""Replay JSONL files to the ingestion API — idempotency verification."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ingest.client import IngestClient
from schemas.events import EventEnvelope
from shared.logging import configure_logging, get_logger

logger = get_logger(__name__)


def load_events(path: Path) -> list[EventEnvelope]:
    events: list[EventEnvelope] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(EventEnvelope.model_validate(json.loads(line)))
    return events


async def replay_file(path: Path, client: IngestClient, batch_size: int) -> None:
    events = load_events(path)
    for i in range(0, len(events), batch_size):
        batch = events[i : i + batch_size]
        result = await client.post_batch(batch)
        logger.info("replay_batch", file=str(path), **result.model_dump())


async def main_async(args: argparse.Namespace) -> None:
    configure_logging()
    client = IngestClient(args.api_url)
    input_path = Path(args.input)
    files = list(input_path.rglob("*.jsonl")) if input_path.is_dir() else [input_path]
    for f in files:
        await replay_file(f, client, args.batch_size)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay JSONL events to API")
    parser.add_argument("--input", required=True, help="JSONL file or directory")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
