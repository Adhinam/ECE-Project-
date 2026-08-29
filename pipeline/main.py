"""Pipeline CLI entrypoint — detection or full analytics runner."""

from __future__ import annotations

import argparse
import asyncio
import sys

from shared.logging import configure_logging, get_logger

logger = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    """
    Dispatch to detection-only or full pipeline (tracking/events stub).

    Examples:
        python -m pipeline.main detect --source data/sample/demo.mp4 --visualize
        python -m pipeline.detect --source videos/
    """
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description="Store intelligence pipeline")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "detect",
        help="YOLOv8n person detection (see also: python -m pipeline.detect)",
    )
    subparsers.add_parser(
        "track",
        help="Detection + ByteTrack (see also: python -m pipeline.tracker)",
    )
    subparsers.add_parser(
        "entry-exit",
        help="Tracking + entry/exit lines (python -m pipeline.entry_exit)",
    )
    subparsers.add_parser(
        "zones",
        help="Zone enter/exit/dwell (python -m pipeline.zones)",
    )
    subparsers.add_parser(
        "emit",
        help="Event log replay/validate (python -m pipeline.emit)",
    )
    subparsers.add_parser("run", help="Full pipeline runner (tracking/events — WIP)")

    # Allow `python -m pipeline.main detect --source ...` by passing detect args through
    if argv and argv[0] == "detect":
        from pipeline.detect import main as detect_main

        return detect_main(argv[1:])

    if argv and argv[0] == "track":
        from pipeline.tracker import main as track_main

        return track_main(argv[1:])

    if argv and argv[0] in ("entry-exit", "entry_exit"):
        from pipeline.entry_exit import main as entry_exit_main

        return entry_exit_main(argv[1:])

    if argv and argv[0] == "zones":
        from pipeline.zones import main as zones_main

        return zones_main(argv[1:])

    if argv and argv[0] == "emit":
        from pipeline.emit import main as emit_main

        return emit_main(argv[1:])

    args, unknown = parser.parse_known_args(argv)

    if args.command == "detect":
        from pipeline.detect import main as detect_main

        return detect_main(unknown)

    if args.command == "track":
        from pipeline.tracker import main as track_main

        return track_main(unknown)

    if args.command == "entry-exit":
        from pipeline.entry_exit import main as entry_exit_main

        return entry_exit_main(unknown)

    if args.command == "zones":
        from pipeline.zones import main as zones_main

        return zones_main(unknown)

    if args.command == "run" or args.command is None:
        from pipeline.orchestrator import FullPipelineRunner
        from pipeline.settings import PipelineSettings

        settings = PipelineSettings()
        configure_logging(json_logs=settings.log_json, log_level=settings.log_level)
        logger.info("pipeline_starting", store_id=settings.pipeline_store_id)
        summary = FullPipelineRunner(settings).run()
        logger.info(
            "pipeline_complete",
            events=summary.events_emitted,
            jsonl=str(summary.jsonl_path) if summary.jsonl_path else None,
        )
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
