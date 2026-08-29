# Known limitations

Documented gaps versus the Purplle challenge ideal; each is an explicit trade-off or deferred scope.

## Dataset and ground truth

- **Challenge ZIP not bundled** — Official clips, `store_layout.json` per store, and `assertions.py` are not in this repo. Use your downloaded dataset under `data/clips/` and point `STORE_LAYOUT_PATH` / `POS_TRANSACTIONS_PATH` accordingly.
- **Detection accuracy** — YOLOv8n on CPU is a baseline; mAP on real footage is not certified against challenge ground truth without the provided evaluation harness.

## Pipeline

- **Single-camera Re-ID** — Cross-camera visitor merge is best-effort (embedding gallery in `pipeline/reid.py`); no global ID graph across all cameras.
- **Queue abandon on occlusion** — `BILLING_QUEUE_ABANDON` fires when the centroid leaves the queue polygon; brief tracker drop-outs may delay or miss abandon events (no queue occlusion grace yet).
- **Group handling** — Group-entry candidates are logged in tracking; separate group visitor IDs are not fully split in v1.

## API and analytics

- **POS correlation** — Matches on `visitor_id` when POS CSV includes it; otherwise time-window proximity to ENTRY (configurable via `POS_MATCH_WINDOW_MINUTES`). Mis-linked POS rows inflate conversion.
- **Heatmap** — Built from `position_snapshot` and zone events with bbox centroids; sparse cameras yield `data_confidence: low`.
- **Funnel PURCHASE stage** — Proxied by checkout zone visit or POS match, not SKU-level basket analysis.

## Operations

- **GPU optional** — Default Docker image runs API + dashboard on CPU; full CV stack uses `make up-full` or local Ultralytics install.


