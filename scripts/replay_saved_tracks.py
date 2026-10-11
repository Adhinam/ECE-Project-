"""Rebuild video/events from saved tracks without rerunning detection or ReID.

This is an explicit offline event-rule review. Tracking decisions are frozen;
it does not claim that changed event feedback was fed back into the tracker.
"""
import argparse
import json
from pathlib import Path
import shutil
from unittest.mock import patch
from uuid import uuid4

from pipeline.detect import FrameDetections
from pipeline.tracker import FrameTracks, TrackedVisitor
from zone_app.worker import atomic_json, process_run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    args=parser.parse_args()
    source=args.source.resolve()
    if json.loads((source/'status.json').read_text())['state']!='complete':
        raise ValueError('Only completed runs can be replayed')
    frames={}
    for line in (source/'tracks.jsonl').read_text().splitlines():
        record=json.loads(line)
        frames[record['frame_index']]=FrameTracks(record['frame_index'],tuple(
            TrackedVisitor(**{**t,'bbox_xyxy':tuple(t['bbox_xyxy']),'centroid':tuple(t['centroid'])})
            for t in record['tracks']))
    identity=uuid4().hex
    folder=Path('sample_video/output/app_runs')/('run_'+identity[:12])
    folder.mkdir(parents=True)
    spec=json.loads((source/'job.json').read_text())
    spec.update(id=identity,name='Verified cap sequence - crossing review',source='source.mp4')
    shutil.copy2(source/'source.mp4',folder/'source.mp4')
    shutil.copy2(source/'layout.json',folder/'layout.json')
    atomic_json(folder/'job.json',spec)

    class SavedDetector:
        def __init__(self,**kwargs): pass
        def detect_frame(self,image,index): return FrameDetections(index,())

    class SavedTracker:
        identity_memory=None
        def __init__(self,**kwargs): pass
        def update(self,detections,image): return frames[detections.frame_index]

    with patch('pipeline.orchestrator.PersonDetector',SavedDetector), patch('pipeline.orchestrator.ByteTrackVisitorTracker',SavedTracker):
        process_run(folder)
    previous=json.loads((source/'report.json').read_text())
    report=json.loads((folder/'report.json').read_text())
    for key in ('identity_recoveries','appearance_conflicts','raw_track_ids'):
        report[key]=previous[key]
    report['tracking_source_run']=str(source)
    report['event_replay']=True
    report['notes']+=' Events were recalculated from saved tracks; identities were not re-inferred.'
    atomic_json(folder/'report.json',report)
    status=json.loads((folder/'status.json').read_text())
    status['report']=report
    atomic_json(folder/'status.json',status)
    for filename in ('identity_audit.json','models_used.yaml','checkpoint_review.json'):
        if (source/filename).exists(): shutil.copy2(source/filename,folder/filename)
    (folder/'REVIEW_NOTES.txt').write_text(
        'Detection and identity tracks are frozen from '+str(source)+'.\n'
        'Crossings, sessions, zones, dashboard, CSVs and video overlays were rebuilt.\n'
        'Weak boxes below 0.6 cannot establish doorway crossings.\n'
        'The cap-shopper checkpoint pass is narrow; whole-video identity accuracy is not measured.\n')
    atomic_json(Path('.runtime/zone-app')/f'{identity}.json',{'folder':str(folder.resolve())})
    Path('.runtime/tracking-improvement/latest-reviewed-output.txt').write_text(str(folder.resolve()))
    print(json.dumps({'folder':str(folder.resolve()),'id':identity,'entries':report['entries'],'exits':report['exits']}),flush=True)


if __name__=='__main__': main()
