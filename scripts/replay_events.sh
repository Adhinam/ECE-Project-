#!/usr/bin/env bash
set -euo pipefail
python -m ingest.replay --input "${1:-data/events}" --api-url "${INGEST_API_URL:-http://localhost:8000}"
