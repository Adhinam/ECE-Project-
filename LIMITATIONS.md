# Known limitations

Documented gaps versus the Purplle challenge ideal; each is an explicit trade-off or deferred scope.

## Dataset and ground truth

- **Challenge ZIP not bundled** — Official clips, `store_layout.json` per store, and `assertions.py` are not in this repo. Use your downloaded dataset under `data/clips/` and point `STORE_LAYOUT_PATH` / `POS_TRANSACTIONS_PATH` accordingly.
- **Detection accuracy** — YOLO26n on CPU is the current baseline; real-footage accuracy and speed have not been certified. See `docs/VIDEO_REVIEW.md` for evaluation.

## Pipeline

- **Single-camera Re-ID** — Cross-camera visitor merge is best-effort (embedding gallery in `pipeline/reid.py`); no global ID graph across all cameras.
- **Queue abandon on occlusion** — `BILLING_QUEUE_ABANDON` fires when the centroid leaves the queue polygon; brief tracker drop-outs may delay or miss abandon events (no queue occlusion grace yet).
- **Group handling** — Group-entry candidates are logged in tracking; separate group visitor IDs are not fully split in v1.

## API and analytics

- **POS correlation** — Billing-zone time-window matching only accepts a single eligible visitor. Ambiguous cases remain unresolved; all such estimates are low-confidence and may undercount sales.
- **Heatmap** — Built from `position_snapshot` and zone events with bbox centroids; sparse cameras yield `data_confidence: low`.
- **Funnel PURCHASE stage** — Proxied by checkout zone visit or POS match, not SKU-level basket analysis.

## Operations

- **GPU optional** — Default Docker image runs API + dashboard on CPU; full CV stack uses `make up-full` or local Ultralytics install.


