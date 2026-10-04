# Deep OC-SORT review — 4 October 2026

## Result

The full 1452-frame video was processed once after the 75–95 second test passed.
In the inspected 82, 84, 86, 88, 90 and 92 second frames, the blue-jacket shopper
keeps customer ID **8**, including after bending down. In the short test starting
at 75 seconds she keeps ID **5**; different run-local numbering is expected.
The previous full run changed her ID from 8 to 39.

Matching her previously recorded boxes at IoU > 0.5 over 75–95 seconds found 197
matching observations, all with customer ID 8 / raw track 9 in the new full run.
This comparison uses previous model boxes as references, not annotated ground
truth. The sequence was also visually inspected. It establishes a fix for the
reported sequence, not reliable identity for every shopper throughout the video.

| Measure | Previous gallery + ByteTrack | Deep OC-SORT |
|---|---:|---:|
| Frames processed | 1452 | 1452 |
| Raw tracker IDs | 48 | 25 |
| Assigned customer IDs | 41 | 23 |
| Processing FPS including output | 11.13 | 4.91 |
| Sampled process RAM | 644 MiB | 709 MiB |

The reduced ID count alone is not accuracy evidence: missed people and false
merges can also lower counts. No IDF1 or complete customer count is claimed.
The source is 13.09 FPS, so this CPU run is not real-time. Existing hardware can
process it offline. Timing is from one run and is affected by other machine load.
The saved video plays at source speed and its last frame was verified readable.

## Implementation

- Uses the installed Ultralytics 8.4.172 Deep OC-SORT implementation, with explicit
  YOLO26n ReID ONNX features injected into its association stage. ReID is enabled;
  this is not the default appearance-disabled configuration or native `auto` mode.
- The CPU encoder has two inference threads; crops are RGB / 255 at 224 pixels.
  It uses tight detection crops. ONNX weights are already local and remain ignored
  by Git. The required `lap==0.5.13` assignment solver is pinned.
- Static-camera compensation is disabled. Low-confidence association is enabled.
  Raw tracks remain available for ten seconds, converted using source FPS.
- The separate customer gallery now supports `identity_memory_seconds: null`:
  unresolved visits do not expire after five minutes. At capacity (500 identities)
  it can evict confirmed exits, but stops with an explicit error rather than silently
  forgetting unresolved customers. Descriptors are bounded to eight per identity.
- Gallery memory is local to this process/video. Restarting resets it; it is not a
  persistent store database or a cross-camera identity service.
- The existing conservative recovery gallery remains a fallback. Its one-shot
  new-track decision is not a general multi-frame reassignment implementation.
  This reported failure is fixed by Deep OC-SORT retaining its raw identity.
- No exits are inferred from missing detections. Actual entrance/exit calibration
  remains necessary. Payment and physical exit are separate events.

The full report's gallery recovery count is zero: that counter measures only the
fallback gallery, not Deep OC-SORT's internal appearance association. Stable raw
tracking means the fallback does not need to repair the blue-jacket sequence.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.pipeline.txt
.\.venv\Scripts\python.exe -m pipeline.review --source "sample_video/Resized video.mp4" --output sample_video/output/new_segment --start-seconds 75 --max-seconds 20
```

The default `configs/models.yaml` now uses Deep OC-SORT. Explicit profiles are
`configs/models.deepocsort.yaml` and `configs/models.bytetrack.yaml`; pass one with
`--models-config`. The historical `ByteTrackVisitorTracker` class name remains for
compatibility, but all existing runners use the backend selected by configuration.

Outputs: `sample_video/output/deepocsort_full/annotated.mp4`, `sequence.jpg`,
`tracks.jsonl`, `detections.jsonl`, `frames.csv` and `report.json`. The short test is
in `sample_video/output/deepocsort_segment_v1`. Prior outputs are preserved.

Validation: 194 tests passed, including the real Deep OC-SORT association backend
with deterministic appearance vectors across a three-second gap, indefinite visit
retention, capacity failure, and review start-time offsets. These unit tests are
not substitutes for video accuracy evaluation.

Sources: [Deep OC-SORT paper](https://arxiv.org/abs/2302.11813) and
[Ultralytics tracking documentation](https://docs.ultralytics.com/modes/track/).
