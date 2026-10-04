# Re-ID sample review — 4 October 2026

Open **sample_video/output/reid_final/annotated.mp4** locally for the final run. The original baseline
remains in the parent output folder. reid_v2, reid_v3 and reid_review are experiments.

| Observed measure | Original baseline | Final run |
|---|---:|---:|
| Processed frames | 1452 | 1452 |
| Raw tracker IDs | 52 | 48 |
| Assigned/displayed identities | 52 | 41 |
| Appearance recoveries | 0 | 3 |
| Processing FPS including output | 11.70 | 11.13 |
| Sampled process RAM | 489 MiB | 644 MiB |

The source is 13.09 FPS. This remains slower than real-time source speed on this
CPU, although the saved video plays at the original frame rate. These are single
offline runs, not controlled hardware benchmarks; timings vary with machine load.
No hardware purchase was required to process the clip.

## What changed

- Four-second ByteTrack retention instead of about one second at this source FPS.
- Learned clothing/body appearance gallery: up to eight descriptors per person,
  retained for five minutes of missing video time, with a 500-person gallery cap.
- A short confirmation period before creating IDs; weak/overlapping/flat crops
  cannot become appearance evidence. Fully hidden people have no drawn box.
- Detection confidence remains 0.10. Lowering track association to 0.30 increased
  fragments in a trial, so the final setting uses 0.50 association and 0.60 new-track
  creation. Weak detections can still maintain an already established track.
- Matches require appearance similarity, separation from competing candidates,
  plausible position/time, and an identity not already visible elsewhere.
- Missing people are not declared to have exited. A calibrated crossing is needed;
  a recovered track jumping across a line during missing frames is not counted.

## Inspected recoveries

See **recovery_pairs.jpg** and **pair_review.json**. The inspected before/after
crops look consistent for a white-hooded shopper (15.5-second gap), a blue-jacketed
shopper (2.2 seconds), and a dark-jacketed shopper (0.8 seconds). This is a visual
spot check, not identity ground truth or an IDF1 score.

## Remaining problems

**41 IDs is not 41 customers.** Fragmentation is still substantial relative to the
user's estimate of 10–20 people. Some apparent improvement comes from suppressing
short tentative tracks, which can also hide brief true observations. Existing raw
tracks can still switch people during crowding, and the gallery can miss or wrongly
match people with similar clothing. A fully hidden body cannot be recovered by
lowering confidence alone. The thresholds were tuned on this same clip and need
validation on additional labelled footage before claiming reliable accuracy.

Entry/exit and occupancy are **not calibrated** for this video. The real doorway
and inside direction still need to be specified. After calibration, label actual
people, occlusion sequences and crossings; measure ID switches, false merges and
missed recovery before presenting unique-customer accuracy.

## Evidence

- frames.csv: frame timing, displayed IDs and raw IDs.
- tracks.jsonl: bounding boxes and both ID types.
- identity_events.json: new identities and recovery scores/gaps.
- identity_candidates.json: candidate scores including rejected matches.
- report.json: exact settings, source metadata, environment and timing definitions.
