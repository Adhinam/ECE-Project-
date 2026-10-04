"""Create a contact sheet of recovered identities for human review (not ground truth)."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def create_pairs(review: Path):
    report = json.loads((review / "report.json").read_text(encoding="utf-8"))
    events = json.loads((review / "identity_events.json").read_text(encoding="utf-8"))
    recoveries = [e for e in events if e["action"] == "recovered"]
    frames = [json.loads(line) for line in (review / "tracks.jsonl").read_text(encoding="utf-8").splitlines()]
    cap = cv2.VideoCapture(report["source"])
    tiles, pairs = [], []
    try:
        for event in recoveries:
            index, person = event["frame_index"], event["person_id"]
            previous = [(f["frame_index"], t) for f in frames if f["frame_index"] < index
                        for t in f["tracks"] if t["track_id"] == person]
            current = [(f["frame_index"], t) for f in frames if f["frame_index"] == index
                       for t in f["tracks"] if t["track_id"] == person]
            if not previous or not current:
                continue
            tile = np.full((280, 320, 3), 245, dtype=np.uint8)
            for column, (frame_index, track) in enumerate([previous[-1], current[0]]):
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ok, image = cap.read()
                if not ok:
                    raise RuntimeError(f"Cannot decode frame {frame_index}")
                x1, y1, x2, y2 = track["bbox_xyxy"]
                crop = image[max(0, y1):min(image.shape[0], y2), max(0, x1):min(image.shape[1], x2)]
                if not crop.size:
                    raise ValueError(f"Invalid crop at frame {frame_index}")
                scale = min(155 / crop.shape[1], 220 / crop.shape[0])
                crop = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)),
                                        max(1, round(crop.shape[0] * scale))))
                tile[30:30 + crop.shape[0], column * 160:column * 160 + crop.shape[1]] = crop
            title = f"ID {person}: frames {previous[-1][0]} -> {index}"
            cv2.putText(tile, title, (4, 18), 0, 0.45, (0, 0, 0), 1)
            cv2.putText(tile, f"similarity {event['similarity']:.2f}; gap {event['gap_seconds']:.1f}s",
                        (4, 270), 0, 0.43, (0, 0, 0), 1)
            tiles.append(tile)
            pairs.append({**event, "previous_frame": previous[-1][0], "manual_verdict": "unreviewed"})
    finally:
        cap.release()
    if tiles:
        while len(tiles) % 3:
            tiles.append(np.full_like(tiles[0], 245))
        sheet = np.vstack([np.hstack(tiles[i:i + 3]) for i in range(0, len(tiles), 3)])
        cv2.imwrite(str(review / "recovery_pairs.jpg"), sheet)
    (review / "pair_review.json").write_text(json.dumps(pairs, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    create_pairs(parser.parse_args().review)
