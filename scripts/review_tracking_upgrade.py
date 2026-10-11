"""Create a separate review run from a completed app run without overwriting it."""
import json
from pathlib import Path
import shutil
from uuid import uuid4

from zone_app.worker import atomic_json, process_run


def main():
    root=Path.cwd()
    previous=root/'sample_video/output/app_runs/run_5e2ae82c9e7c'
    identity=uuid4().hex
    folder=root/'sample_video/output/app_runs'/('run_'+identity[:12])
    folder.mkdir(parents=True)
    spec=json.loads((previous/'job.json').read_text())
    spec.update(id=identity, name='Tracking upgrade review - '+spec['name'],source='source.mp4')
    shutil.copy2(previous/'source.mp4',folder/'source.mp4')
    layout=json.loads((previous/'layout.json').read_text())
    # Candidate calibration for review: move the border line into the visible path.
    # Preserve the user's original run and layout unchanged.
    for line in layout['entry_exit_lines']:
        line['p1'][0]-=150
        line['p2'][0]-=150
    atomic_json(folder/'job.json',spec)
    atomic_json(folder/'layout.json',layout)
    atomic_json(folder/'status.json',{'state':'running','message':'Evaluating tracking upgrade','output_folder':str(folder)})
    (folder/'REVIEW_NOTES.txt').write_text(
        'Candidate calibration: original doorway was at x=1266..1269, on the image edge. '
        'This review shifts it 150 pixels inward to observe centres on both sides. '
        'Verify its location against actual doorway traffic. More detected identities are not an accuracy score.\n',encoding='utf-8')
    print('Review output: '+str(folder),flush=True)
    process_run(folder)
    # Register only after completion; this worker is not owned by a running app server.
    atomic_json(root/'.runtime/zone-app'/f'{identity}.json',{'folder':str(folder)})
    (root/'.runtime/tracking-improvement/latest-review.txt').write_text(str(folder))
    report=json.loads((folder/'report.json').read_text())
    print(json.dumps({k:report[k] for k in ('entries','exits','observed_ids','identity_recoveries','appearance_conflicts','raw_track_ids')}),flush=True)


if __name__=='__main__':
    main()
