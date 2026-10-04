> Current default: Deep OC-SORT with explicit Re-ID. See [current results and configuration](DEEP_OCSORT_REVIEW.md).
> The ByteTrack/gallery sections below describe the earlier baseline; use configs/models.bytetrack.yaml to reproduce that backend.

# CPU video review and YOLO26 upgrade

The default detector is YOLO26 Nano (`yolo26n.pt`), with ByteTrack for tracking.
Use this workflow before starting the website or making hardware purchases.

## Setup on Windows

From the repository directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.pipeline.txt
```

The first model run downloads official pretrained weights if absent. Later runs
can use those local weights offline. A GPU and model training are not required.

## Review a sample

Place the video in `data/videos/` (ignored by Git). Run:

```powershell
.\.venv\Scripts\python.exe -m pipeline.review --source "data/videos/sample.mp4"
```

This processes the first 60 seconds consecutively on CPU, without artificial
sleep or frame skipping, and creates a new folder under `data/reviews/` containing:

- `annotated.mp4`: boxes, track IDs, and paths at the source playback frame rate.
- `frames.csv`: per-frame detections, tracked people, IDs, and timings.
- `report.json`: model settings, versions, source FPS, measured processing FPS,
  detector/tracker timings, CPU information, and sampled process RAM.
- `REVIEW.md`: a short summary with interpretation limits.

Use `--max-seconds 120` for a longer clip. Custom output folders must not already
exist, to prevent overwriting evidence. No video is uploaded by this command.

## Compare models fairly

Run each model in a separate process, using the same video, duration, input size,
thread count, confidence settings, and output options. Close heavy background
applications. For example:

```powershell
.\.venv\Scripts\python.exe -m pipeline.review --source "data/videos/sample.mp4" --model yolov8n.pt --output data/reviews/v8n
.\.venv\Scripts\python.exe -m pipeline.review --source "data/videos/sample.mp4" --model yolo26n.pt --output data/reviews/26n
.\.venv\Scripts\python.exe -m pipeline.review --source "data/videos/sample.mp4" --model yolo26s.pt --output data/reviews/26s
```

The comparison deliberately uses one common pipeline; it does not reproduce the
old website's configuration. Use `--no-video` on all comparison runs to isolate
preview-encoding overhead. Test `--imgsz 480` against 640 to assess the speed vs
small-person recall trade-off. `--threads 2` or `--threads 4` controls CPU threads.
Use a new output directory for every experiment. Do not assume a smaller image or
more threads improves the final result.

## Interpret the evidence

Detector FPS excludes the tracker and output work. Processing FPS includes them.
Loading and three warm-up predictions are separately recorded. RAM is sampled
once per frame for the whole process; this is not an exact transient peak or GPU
memory measurement. Offline throughput does not demonstrate live-feed latency.

Distinct track IDs are **not unique customers**. Occlusion and identity switches
can split one person into several tracks. Review the annotated video and manually
label representative frames to measure count error and missed/false detections.
For entry/exit accuracy, mark actual crossing times and compare event counts.

The video-review command does not invent a store layout. Before running retail
events on a new camera, calibrate entrance lines, zone polygons, staff areas, and
queue areas against that video's dimensions. Existing layouts are examples and
must not be treated as valid for arbitrary footage.

## Changes in this upgrade

- YOLO26 Nano default; pinned Ultralytics and Supervision versions.
- Shared detector configuration for detection, tracking, and full event processing.
- Configurable image size and CPU threads; optional CLAHE preprocessing adapted
  from the teammate repository, disabled pending footage evaluation.
- Removed batch-script YOLOv8 overrides and the default offline FPS sleep cap.
  Existing personal `.env` files can still override the cap: use `PIPELINE_FPS_LIMIT=0`.
- Low-confidence detections reach ByteTrack's second association pass; the higher
  association threshold is 0.50 and new-track threshold is 0.60. Thresholds need footage validation.
- EXIT events retain the session identity after closure; run summaries count
  distinct visitor IDs instead of sessions. End timestamps use source-frame position.
- POS matching rejects ambiguous billing-window candidates and remains low-confidence.
  This is a conservative estimate and can undercount sales; it is not proof of purchase.
- Included pipeline tests in the standard test command. Repaired invalid crossing
  fixtures, auto-flush expectations, timeout boundaries, and dwell-event collection.
  The POS fixture now generates its own test data instead of requiring an absent CSV.

## What still needs validation

ByteTrack is retained as the CPU baseline. The teammate's BoT-SORT, offline
stitching, and zone re-linking changes are not enabled in this branch: their
identity propagation and false-merge behaviour need independent testing. The
existing optional histogram Re-ID is still approximate. Cross-camera visitor
identity, staff classification, checkout-based conversion proxies, production
deployment, and live-stream performance are not certified by this upgrade.

The authoritative event runner is `pipeline.orchestrator.FullPipelineRunner`,
invoked by `python -m pipeline.main run`. Older component scaffolds under
`pipeline/app/` are not the detector/tracker used by that command.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.api.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

The upgrade was checked on Windows with Python 3.14, Ultralytics 8.4.172,
Supervision 0.27.0.post2, and PyTorch 2.14.1. Automated tests passed and the real
YOLO26 Nano weights processed a short blank synthetic clip through both runners.
Synthetic footage only verifies execution and file output; it is not an accuracy
or representative CCTV performance benchmark. The local sample review is documented below.


## Appearance memory and occlusion recovery

`tracker.identity_enabled: true` enables a camera-local identity gallery in every
ByteTrack runner, including `pipeline.review`. It uses the dedicated
`yolo26n-reid.onnx` encoder with ONNX Runtime on CPU. This is separate from the
older optional histogram-based exit/re-entry coordinator. See the
[official encoder documentation](https://docs.ultralytics.com/modes/track/).
Weights download on first use; they are not committed. Install requirements.pipeline.txt.

The detector retains scores above 0.10. ByteTrack uses scores above 0.50 for its
first association pass, but requires 0.60 to create a raw track. A new identity
must persist for at least three observations, with a usable appearance crop;
a track without a usable crop waits up to one source second. This delays new
labels slightly and suppresses brief detections. Fully hidden people cannot be
detected; their identity stays in memory rather than drawing an invented box.

The 120-frame ByteTrack buffer is expressed at a 30 FPS reference rate:
at this clip's 13 FPS it retains about 52 missing frames (four seconds).
The appearance gallery retains missing identities for 300 source-video seconds,
with up to eight samples each and 500 retained people. Expiry means forgotten,
not confirmed exit. Only a calibrated outward line crossing marks an exit.
Unobserved jumps across a line do not generate entry/exit events.

Recovery requires sufficient cosine similarity, a margin over the next candidate,
a plausible time/distance relationship, and a unique assignment. Visible people
are excluded from recovery candidates. Weak, very flat, and heavily overlapping crops do not
update appearance. These checks reduce false merges but can leave fragmented IDs.
ByteTrack can still switch existing IDs at crossings; this layer does not certify
continuous identity, and matching clothing is not proof that two people are the same.

`frames.csv` retains person IDs and underlying raw track IDs; `tracks.jsonl` saves
boxes and both IDs. `identity_events.json` records new/recovered assignments and
`identity_candidates.json` records candidate similarities for manual review.
The number of resulting identities is never reported as a verified customer count.
Store footage and generated outputs remain local under `sample_video/output/`.


For a visual recovery audit, run:

```powershell
.\.venv\Scripts\python.exe scripts/review_identity_pairs.py sample_video/output/reid_final
```

This creates `recovery_pairs.jpg` and an initially unreviewed `pair_review.json`.
Inspect complete video context as well as crops before marking a pair consistent.
The sample's similarity threshold (0.55) and margin (0.08) are preliminary,
selected using this same clip, not validated on a held-out dataset. A visually
consistent match is not a measured IDF1 score. Do not deploy these parameters
unchanged to other cameras without checking both missed matches and false merges.
