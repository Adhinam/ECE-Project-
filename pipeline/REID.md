# Lightweight Re-ID — limitations and operations

Implementation: `pipeline/reid.py` (HSV histogram + cosine similarity, no training).

## Approach

| Component | Method |
|-----------|--------|
| Appearance | 16×8×8 HSV histogram on person crop |
| Histogram score | `cv2.HISTCMP_CORREL` → [0, 1] |
| Cosine score | Dot product on normalized histogram vector |
| Combined | Weighted average (`histogram_weight`, `cosine_weight`) |
| Memory | Ring buffer of exited visitors (`gallery_max_entries`) |
| TTL | `reentry_timeout_seconds` (default 300s, aligns with layout `reentry_window_seconds`) |

## REENTRY events

Emitted when an entry appearance matches a recently exited visitor above `similarity_threshold`.

Payload includes: `visitor_id`, `prior_exit_event_id`, `gap_seconds`, `histogram_similarity`, `cosine_similarity`, `combined_similarity`.

## Graceful degradation

Re-ID is skipped when:

- `REID_ENABLED=false` or `reid.enabled: false` in `models.yaml`
- Crop smaller than 24×24 px
- OpenCV histogram extraction fails
- Gallery empty or all records expired

Callers should always assign a **new UUID** when matching returns `None`.

## Limitations

1. **Appearance-only** — same outfit/colors required; clothing changes break matches.
2. **Lighting & camera** — white balance and exposure shift histograms.
3. **Occlusion & pose** — partial crops reduce similarity.
4. **Crowds** — similar clothing → false positives (raise threshold).
5. **No cross-camera** — single stream gallery only (no global embedding DB).
6. **Not a replacement for ByteTrack** — `track_id` is still per video segment; Re-ID links store **visitor sessions** across exit/re-entry.

## Edge cases

| Case | Behaviour |
|------|-----------|
| Exit without storable crop | No gallery entry; re-entry cannot match |
| Re-entry after timeout | Treated as new visitor |
| Two visitors exit, similar appearance | Best-score wins; may mis-associate |
| Visitor never exited (dwell loop) | No REENTRY; normal session continues |
| Overlapping entries | Match against most recent eligible exit first by confidence |
| Staff / false detections | Low-quality crops → no feature → degraded path |

## Scaling considerations

| Dimension | Guidance |
|-----------|----------|
| **Memory** | O(`gallery_max_entries`) × ~1–2 KB per histogram; default 500 entries |
| **CPU** | One histogram per exit + per entry candidate; negligible vs YOLO |
| **Latency** | Linear scan of gallery; keep `gallery_max_entries` < 2k for real-time |
| **Multi-store** | One `LightweightReID` instance per store/camera stream |
| **Production upgrade** | Swap `extract_feature` for OSNet/CLIP later; keep `ReentryCoordinator` API |

## Configuration

```yaml
# configs/models.yaml
reid:
  enabled: true
  similarity_threshold: 0.72
  gallery_ttl_seconds: 300
```

```env
REID_ENABLED=true
REID_SIMILARITY_THRESHOLD=0.72
REID_REENTRY_TIMEOUT_SECONDS=300
REID_GALLERY_MAX=500
```

## Integration

```python
from pipeline.reid import ReentryCoordinator, load_reid_settings

coord = ReentryCoordinator(store_id="store-001", camera_id="cam-1", clock=clock)
coord.on_visitor_exit(visitor_id=vid, frame=frame, bbox_xyxy=bbox, exit_event=exit_ev)
reentry_ev, match = coord.check_reentry(frame=frame, bbox_xyxy=bbox, track_id=tid, ...)
if match:
    session_engine.resume_visitor(match.visitor_id, entry_event)
```
