"""Write store_layout.json from layout PNG reference + video frame dimensions.

Layout PNGs are floor-plan references; zone polygons are scaled to each store's
video resolution (1920x1080 for Store 1, 960x1080 for Store 2).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

TEMPLATES: dict[str, dict] = {
    "store1": {
        "store_id": "store1",
        "frame_width": 1920,
        "frame_height": 1080,
        "output": ROOT / "configs" / "store_layout_store1.json",
    },
    "store2": {
        "store_id": "store2",
        "frame_width": 960,
        "frame_height": 1080,
        "output": ROOT / "configs" / "store_layout_store2.json",
    },
}


def _load_template(store_key: str) -> dict:
    path = TEMPLATES[store_key]["output"]
    return json.loads(path.read_text(encoding="utf-8"))


def generate(layout_image: Path, store_key: str, output: Path | None = None) -> Path:
    if store_key not in TEMPLATES:
        raise ValueError(f"Unknown store key: {store_key}")
    meta = TEMPLATES[store_key]
    out = output or meta["output"]

    with Image.open(layout_image) as im:
        layout_w, layout_h = im.size

    layout = _load_template(store_key)
    layout["layout_reference"] = {
        "image_path": str(layout_image.resolve()),
        "image_width": layout_w,
        "image_height": layout_h,
        "video_width": meta["frame_width"],
        "video_height": meta["frame_height"],
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(layout, indent=2), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate store_layout.json from layout PNG")
    parser.add_argument("--store", choices=["store1", "store2"], required=True)
    parser.add_argument("--layout-image", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    out = generate(args.layout_image, args.store, args.output)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
