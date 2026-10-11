"""Score manually reviewed checkpoints, not overall tracking accuracy.

The user supplied screenshots at 19.5, 24 and 35.2 seconds. Boxes are in the
1270x720 source image. The separate white-hooded shopper checks ID contamination.
"""
import argparse
import json
from pathlib import Path

CHECKPOINTS = [
    (19.5, 'cap-shopper', (833, 95, 966, 405)),
    (24.0, 'cap-shopper', (71, 277, 285, 706)),
    (35.2, 'cap-shopper', (451, 3, 505, 136)),
    (24.0, 'hood-shopper', (799, 122, 873, 282)),
    (35.2, 'hood-shopper', (783, 120, 849, 273)),
]


def iou(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection / union if union else 0


def check(path, fps):
    frames = [json.loads(line) for line in path.read_text().splitlines()]
    result = []
    for seconds, subject, box in CHECKPOINTS:
        frame = min(frames, key=lambda f: abs(f['frame_index']/fps-seconds))
        ranked = sorted(((iou(box, t['bbox_xyxy']), t['track_id']) for t in frame['tracks']), reverse=True)
        score, identity = ranked[0] if ranked else (0, None)
        found = abs(frame['frame_index']/fps-seconds)<.1 and score>=.4
        result.append(dict(seconds=seconds, subject=subject, id=identity if found else None, box_iou=round(score,3)))
    cap_ids = {r['id'] for r in result if r['subject']=='cap-shopper'}
    hood_ids = {r['id'] for r in result if r['subject']=='hood-shopper'}
    return {'checkpoints': result, 'cap_consistent': len(cap_ids)==1 and None not in cap_ids,
            'different_people_separated': not (cap_ids-{None}) & (hood_ids-{None}),
            'scope': 'Only these five manually reviewed checkpoints; not a full-video accuracy score.'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tracks', type=Path)
    parser.add_argument('--fps', type=float, default=13.093)
    args=parser.parse_args()
    print(json.dumps(check(args.tracks,args.fps),indent=2))
