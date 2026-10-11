"""Replay the user-reported ID-switch interval; outputs are review candidates."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import types

import cv2
import numpy as np
import yaml

from pipeline.config import TrackerYamlConfig
from pipeline.detect import PersonDetector
from pipeline.tracker import ByteTrackVisitorTracker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='yolo26n.pt')
    parser.add_argument('--gate', type=float, default=0)
    parser.add_argument('--weighted', action='store_true')
    args = parser.parse_args()
    folder = Path('.runtime/tracking-improvement') / f'cap-{Path(args.model).stem}-gate{args.gate}{"-weighted" if args.weighted else ""}'
    folder.mkdir(parents=True, exist_ok=True)
    source = Path('sample_video/output/app_runs/run_5e2ae82c9e7c/source.mp4')
    cap = cv2.VideoCapture(str(source))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cfg = TrackerYamlConfig(**yaml.safe_load(Path('configs/models.yaml').read_text())['tracker'])
    cfg.deep_appearance_weight = 0  # The comparison hook below controls this explicitly.
    tracker = ByteTrackVisitorTracker(tracker_config=cfg, frame_rate=fps, track_timeout_frames=130)
    backend = tracker._byte_track.backend
    original = backend._fuse_appearance

    def guarded(self, dists, tracks, detections, iou_dists=None):
        fused = original(dists, tracks, detections, iou_dists)
        if args.gate:
            for i, track in enumerate(tracks):
                for j, det in enumerate(detections):
                    if track.smooth_feat is not None and det.curr_feat is not None and det.score >= .6:
                        similarity = float(np.dot(track.smooth_feat, det.curr_feat))
                        if args.weighted:
                            fused[i, j] = .45*dists[i,j] + .55*(1-similarity)
                        if similarity < args.gate:
                            fused[i, j] = 1.0
        return fused

    backend._fuse_appearance = types.MethodType(guarded, backend)
    detector = PersonDetector(model_path=args.model, confidence=.1, iou=.45,
                              person_class_id=0, device='cpu', imgsz=960)
    targets = {round(t*fps): t for t in (19.5, 24, 35.2)}
    tiles = []
    with (folder/'tracks.jsonl').open('w') as log:
        for index in range(round(37*fps)):
            ok, frame = cap.read()
            if not ok:
                break
            tracks = tracker.update(detector.detect_frame(frame, index), frame)
            log.write(json.dumps({'frame_index': index, 'tracks': [asdict(t) for t in tracks.tracks]})+'\n')
            if index in targets:
                for track in tracks.tracks:
                    x1,y1,x2,y2=track.bbox_xyxy
                    cv2.rectangle(frame,(x1,y1),(x2,y2),(30,255,255),2)
                    cv2.putText(frame,f'{track.track_id} (raw {track.raw_track_id})',(x1,max(y1,20)),0,.6,(30,255,255),2)
                cv2.putText(frame,f'{targets[index]} seconds',(10,30),0,.8,(255,255,255),2)
                tiles.append(cv2.resize(frame,(889,504)))
            if index % 100 == 0:
                print(f'{index} / {round(37*fps)}', flush=True)
    cap.release()
    cv2.imwrite(str(folder/'checkpoints.jpg'),np.vstack(tiles))
    (folder/'identity_audit.json').write_text(json.dumps({'events':tracker.identity_memory.events,
                                                       'candidates':tracker.identity_memory.candidate_log},indent=2))
    print(str(folder),flush=True)


if __name__ == '__main__':
    main()
