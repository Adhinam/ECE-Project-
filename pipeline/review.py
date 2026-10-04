"""Review a local video without the website, database, or store calibration."""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
from datetime import UTC, datetime
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
import psutil
import torch

from pipeline.config import DetectionConfig
from pipeline.detect import PersonDetector
from pipeline.tracker import ByteTrackVisitorTracker, draw_tracked_frame
from pipeline.utils import VideoReader


def summarize_timings(samples: list[float]) -> dict[str, float | None]:
    """Milliseconds per processed frame; no timing samples means no FPS claim."""
    if not samples:
        return {"mean_ms": None, "p95_ms": None, "fps": None}
    mean = float(np.mean(samples))
    return {
        "mean_ms": mean,
        "p95_ms": float(np.percentile(samples, 95)),
        "fps": 1000.0 / mean if mean > 0 else None,
    }


def review_video(
    source: Path,
    output: Path,
    *,
    model: str | None = None,
    imgsz: int | None = None,
    max_seconds: float = 60,
    save_video: bool = True,
    threads: int = 4,
    models_config: Path = Path("configs/models.yaml"),
) -> dict:
    """Process consecutive frames. Warm-up is excluded; no frames are sampled out.

    The output is a tracking review, not a calibrated entry/exit accuracy score.
    A new directory is required so prior evidence cannot be overwritten.
    """
    if max_seconds <= 0 or threads < 1:
        raise ValueError("max_seconds and threads must be positive")
    if imgsz is not None and (imgsz < 32 or imgsz % 32):
        raise ValueError("imgsz must be a positive multiple of 32")
    if not source.is_file():
        raise FileNotFoundError(source)
    output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(threads)
    cv2.setNumThreads(threads)
    cfg = DetectionConfig(models_config_path=models_config, device="cpu", fps_limit=None)
    kwargs = cfg.detector_kwargs()
    kwargs["cpu_threads"] = threads
    if model is not None:
        kwargs["model_path"] = model
    if imgsz is not None:
        kwargs["imgsz"] = imgsz

    started = perf_counter()
    detector = PersonDetector(**kwargs)
    model_load_seconds = perf_counter() - started
    process = psutil.Process()
    peak_sampled_rss = process.memory_info().rss
    inference_times: list[float] = []
    tracking_times: list[float] = []
    track_ids: set[int] = set()
    writer = None
    warmup_seconds = 0.0
    frames = 0
    maximum_people_in_frame = 0
    video_path = output / "annotated.mp4"

    with (
        VideoReader(source) as reader,
        (output / "frames.csv").open("w", newline="", encoding="utf-8") as frame_log,
        (output / "tracks.jsonl").open("w", encoding="utf-8") as track_log,
    ):
        meta = reader.metadata
        if not np.isfinite(meta.fps) or meta.fps <= 0:
            raise ValueError("Video has no valid frame rate; timing cannot be assessed")
        tracker = ByteTrackVisitorTracker(
            tracker_config=cfg.load_tracker_yaml(),
            frame_rate=meta.fps,
            track_timeout_frames=cfg.resolved_track_timeout(),
            max_history_points=cfg.track_history_max_points,
        )
        rows = csv.writer(frame_log)
        rows.writerow(
            [
                "frame_index",
                "video_seconds",
                "detections",
                "tracked_people",
                "track_ids",
                "raw_track_ids",
                "detector_ms",
                "tracker_ms",
            ]
        )
        loop_started = perf_counter()
        try:
            for item in reader.frames():
                if item.frame_index / meta.fps >= max_seconds:
                    break
                if item.frame is None or item.dropped:
                    continue
                if frames == 0:
                    warmup_start = perf_counter()
                    for _ in range(3):
                        detector.detect_frame(item.frame, item.frame_index)
                    warmup_seconds = perf_counter() - warmup_start
                    if save_video:
                        writer = cv2.VideoWriter(
                            str(video_path),
                            cv2.VideoWriter_fourcc(*"mp4v"),
                            meta.fps,
                            (meta.width, meta.height),
                        )
                        if not writer.isOpened():
                            raise RuntimeError("Cannot create annotated MP4 with this OpenCV build")
                    loop_started = perf_counter()
                detections = detector.detect_frame(item.frame, item.frame_index)
                tracks = tracker.update(detections, item.frame)
                inference_times.append(float(detections.inference_ms or 0))
                tracking_times.append(float(tracks.tracking_ms or 0))
                track_log.write(json.dumps({"frame_index": item.frame_index,
                    "tracks": [asdict(v) for v in tracks.tracks]}) + "\n")
                ids = [visitor.track_id for visitor in tracks.tracks]
                track_ids.update(ids)
                maximum_people_in_frame = max(maximum_people_in_frame, len(ids))
                rows.writerow(
                    [
                        item.frame_index,
                        item.frame_index / meta.fps,
                        len(detections.detections),
                        len(ids),
                        json.dumps(ids),
                        json.dumps([v.raw_track_id or v.track_id for v in tracks.tracks]),
                        inference_times[-1],
                        tracking_times[-1],
                    ]
                )
                if writer is not None:
                    writer.write(draw_tracked_frame(item.frame, tracks, tracker.history_store))
                peak_sampled_rss = max(peak_sampled_rss, process.memory_info().rss)
                frames += 1
        finally:
            if writer is not None:
                writer.release()
        elapsed = perf_counter() - loop_started
        if frames == 0:
            raise ValueError("No readable frames were processed")
        fps = frames / elapsed
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "source": str(source.resolve()),
            "detector": kwargs,
            "tracker": {
                "type": "ByteTrack",
                **cfg.load_tracker_yaml().model_dump(),
                "frame_rate": meta.fps,
            },
            "hardware": {
                "processor": platform.processor(),
                "logical_cpus": os.cpu_count(),
                "ram_gib": psutil.virtual_memory().total / 1024**3,
                "threads": threads,
                "device": "cpu",
            },
            "versions": {name: version(name) for name in ("ultralytics", "torch", "supervision")},
            "source_fps": meta.fps,
            "source_resolution": [meta.width, meta.height],
            "max_seconds": max_seconds,
            "frames_processed": frames,
            "frames_dropped": reader.stats.frames_dropped,
            "model_load_seconds": model_load_seconds,
            "warmup_seconds": warmup_seconds,
            "processing_seconds": elapsed,
            "processing_fps_including_output": fps,
            "keeps_up_with_source_in_this_offline_run": fps >= meta.fps,
            "detector_timing": summarize_timings(inference_times),
            "tracker_timing": summarize_timings(tracking_times),
            "peak_sampled_process_ram_mib": peak_sampled_rss / 1024**2,
            "distinct_track_ids_not_unique_visitors": len(track_ids),
            "identity_recovery": {
                "enabled": tracker.identity_memory is not None,
                "raw_track_count": len(tracker.identity_memory.raw_ids) if tracker.identity_memory else len(track_ids),
                "recoveries": sum(e["action"] == "recovered" for e in tracker.identity_memory.events) if tracker.identity_memory else 0,
                "exit_calibrated": False,
            },
            "maximum_tracked_people_in_frame": maximum_people_in_frame,
            "annotated_video": str(video_path.resolve()) if save_video else None,
            "accuracy_status": "Not measured: requires manual labels and camera calibration",
            "measurement_notes": [
                "Detector timing includes preprocessing and prediction; no website or database.",
                "Loop FPS includes tracking, decoding after first frame, CSV and video output.",
                "Detector loading and three warm-up predictions are excluded from loop FPS.",
                "Appearance encoder initialization, when enabled, is included in loop FPS.",
                "RAM is sampled per frame, includes the whole process, and may miss brief peaks.",
                "Track IDs can fragment or switch; their count is not a unique-customer count.",
                "Offline throughput does not prove live-stream latency or retail-event accuracy.",
            ],
        }
    if tracker.identity_memory is not None:
        (output / "identity_events.json").write_text(
            json.dumps(tracker.identity_memory.events, indent=2), encoding="utf-8")
        (output / "identity_candidates.json").write_text(
            json.dumps(tracker.identity_memory.candidate_log, indent=2), encoding="utf-8")
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "REVIEW.md").write_text(
        f"# Video review\n\nModel: `{kwargs['model_path']}` on CPU.\n\n"
        f"Processed {frames} frames at **{fps:.2f} FPS**, including output writing.\n\n"
        f"Source: {meta.fps:.2f} FPS. Sampled process RAM: "
        f"{peak_sampled_rss / 1024**2:.0f} MiB.\n\n"
        "Accuracy is not measured yet. Inspect annotated.mp4 and frames.csv for missed people, "
        "false detections, and ID switches. Track counts are not visitor counts.\n\n"
        "See report.json for full settings, timing definitions, and versions.\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--model", help="Pretrained name or weights; default from models.yaml")
    parser.add_argument("--imgsz", type=int, default=None)
    parser.add_argument("--max-seconds", type=float, default=60)
    parser.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--models-config", type=Path, default=Path("configs/models.yaml"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-video", action="store_true", help="Measure without preview encoding")
    args = parser.parse_args(argv)
    output = args.output or Path("data/reviews") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        report = review_video(
            args.source,
            output,
            model=args.model,
            imgsz=args.imgsz,
            max_seconds=args.max_seconds,
            threads=args.threads,
            models_config=args.models_config,
            save_video=not args.no_video,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Review failed: {exc}\n")
    print(f"Review saved to {output.resolve()}")
    print(f"Processing FPS: {report['processing_fps_including_output']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
