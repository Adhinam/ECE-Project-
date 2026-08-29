#!/usr/bin/env bash
# Run the full store intelligence detection pipeline (YOLOv8 + ByteTrack + events → JSONL).
#
# Usage:
#   ./pipeline/run.sh
#   ./pipeline/run.sh /path/to/video.mp4
#   PIPELINE_VIDEO_SOURCE=clips/store.mp4 ./pipeline/run.sh
#
# Environment: see .env.example (PIPELINE_*, STORE_LAYOUT_PATH, MODELS_CONFIG_PATH)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

VIDEO_ARG=()
if [[ $# -gt 0 ]]; then
  export PIPELINE_VIDEO_SOURCE="$1"
  shift
fi

VIDEO="${PIPELINE_VIDEO_SOURCE:-data/sample/demo.mp4}"
CLIPS_DIR="${PIPELINE_CLIPS_DIR:-data/clips}"

if [[ "${PIPELINE_BATCH_ALL_CLIPS:-0}" == "1" ]] && [[ -d "$CLIPS_DIR" ]]; then
  echo "Batch mode — processing all videos under $CLIPS_DIR"
  shopt -s nullglob
  for clip in "$CLIPS_DIR"/*.{mp4,avi,mkv,mov}; do
    echo "=== $clip ==="
    PIPELINE_VIDEO_SOURCE="$clip" python -m pipeline.main run "$@"
  done
  exit 0
fi

echo "Store Intelligence pipeline — video=$VIDEO"
PIPELINE_VIDEO_SOURCE="$VIDEO" python -m pipeline.main run "$@"
