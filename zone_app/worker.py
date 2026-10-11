"""Runs inference in a separate process and publishes atomic status snapshots."""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from dataclasses import asdict

import cv2
import numpy as np

from zone_app.analytics import VideoAnalytics


def atomic_json(path, data):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(temp, path)


def process_run(folder):
    from imageio_ffmpeg import get_ffmpeg_exe
    from pipeline.orchestrator import FullPipelineRunner
    from pipeline.settings import PipelineSettings
    from schemas.config import StoreLayoutConfig
    from pipeline.entry_exit import _inward_normal, line_from_config
    from zone_app.calibration import calibration_warnings

    folder = Path(folder)
    spec = json.loads((folder / 'job.json').read_text(encoding='utf-8'))
    layout = StoreLayoutConfig.model_validate_json((folder / 'layout.json').read_text(encoding='utf-8'))
    stats = VideoAnalytics()
    status = {'state': 'running', 'message': 'Loading detector and tracker…', 'output_folder': str(folder)}
    encoder = None
    stderr = (folder / 'encoder.log').open('wb')
    last_publish = 0.0
    fps = spec['fps']
    track_log = (folder / 'tracks.jsonl').open('w', encoding='utf-8')
    warnings = calibration_warnings(layout)
    doorway_sides = {line.id: set() for line in layout.entry_exit_lines}
    try:
        atomic_json(folder / 'status.json', status)
        command = [get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y',
                   '-f', 'rawvideo', '-pixel_format', 'bgr24', '-video_size',
                   f"{spec['width']}x{spec['height']}", '-framerate', str(fps), '-i', 'pipe:0',
                   '-an', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-c:v', 'libx264',
                   '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
                   '-movflags', '+faststart', str(folder / 'annotated.mp4')]
        encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=stderr,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        runner = FullPipelineRunner(PipelineSettings(
            _env_file=None, store_layout_path=str(folder / 'layout.json'),
            pipeline_video_source=str(folder / spec['source']),
            pipeline_store_id=layout.store_id, pipeline_camera_id=spec['camera_id'],
            events_output_dir=str(folder / 'events'), models_config_path='configs/models.yaml',
            pipeline_fps_limit=0, enable_gpu=False, enable_reid=False,
        ))

        def observe(frame, tracks, events, zones, meta):
            nonlocal last_publish
            stats.update(tracks, events, zones, fps)
            track_log.write(json.dumps({'frame_index':tracks.frame_index,
                                        'tracks':[asdict(t) for t in tracks.tracks]})+'\n')
            overlay = frame.copy()
            for group, color in ((layout.zones, (255, 185, 70)),
                                 (layout.queues, (60, 190, 255)),
                                 (layout.staff_areas, (230, 120, 230))):
                for zone in group:
                    polygon = np.asarray(zone.polygon, dtype=np.int32)
                    cv2.polylines(overlay, [polygon], True, color, 2)
                    cv2.putText(overlay, zone.id, tuple(polygon[0]), cv2.FONT_HERSHEY_SIMPLEX, .6, color, 2)
            for line in layout.entry_exit_lines:
                cv2.line(overlay, tuple(line.p1), tuple(line.p2), (130, 240, 120), 3)
                nx, ny = _inward_normal(line_from_config(line))
                for track in tracks.tracks:
                    side=(track.centroid[0]-line.p1[0])*nx+(track.centroid[1]-line.p1[1])*ny
                    if abs(side)>3:
                        doorway_sides[line.id].add('inside' if side>0 else 'outside')
                mid = tuple(int((a+b)/2) for a,b in zip(line.p1,line.p2))
                tip = (int(mid[0]+nx*60), int(mid[1]+ny*60))
                cv2.arrowedLine(overlay, mid, tip, (130,240,120), 3)
                cv2.putText(overlay, 'IN: '+line.id, tip, cv2.FONT_HERSHEY_SIMPLEX, .6, (130,240,120), 2)
            for track in tracks.tracks:
                x1,y1,x2,y2 = track.bbox_xyxy
                cv2.rectangle(overlay, (x1,y1), (x2,y2), (70,230,200), 2)
                cv2.putText(overlay, f'ID {track.track_id}', (x1,max(20,y1-8)),
                            cv2.FONT_HERSHEY_SIMPLEX, .7, (70,230,200), 2)
            cv2.putText(overlay, f"Entries {sum(e['type']=='entry' for e in stats.crossings)} | Exits {sum(e['type']=='exit' for e in stats.crossings)} | {tracks.frame_index/fps:.1f}s",
                        (12,30), cv2.FONT_HERSHEY_SIMPLEX, .7, (255,255,255), 2)
            encoder.stdin.write(overlay.tobytes())
            now = time.monotonic()
            if now-last_publish >= 1:
                status.update(message='Processing video…', frames_processed=stats.frames,
                              total_frames=spec['frame_count'], report=stats.report())
                status['report']['calibration_warnings']=warnings
                atomic_json(folder / 'status.json', status)
                last_publish = now

        runner.run(on_frame=observe)
        status.update(state='finalizing', message='Finishing the playable video and reports…')
        atomic_json(folder / 'status.json', status)
        encoder.stdin.close()
        if encoder.wait(timeout=120) != 0:
            raise RuntimeError('Video encoder failed. See encoder.log in the output folder.')
        if not stats.frames:
            raise RuntimeError('No readable video frames were processed.')
        report = stats.report(complete=True)
        for line_id,sides in doorway_sides.items():
            if len(sides)<2:
                warnings.append(f'Doorway "{line_id}": no clear tracked centres were observed on both sides. Zero crossings does not establish that nobody entered or exited.')
        report['calibration_warnings']=warnings
        report['algorithm_version']='balanced-appearance-stable-crossings-v3'
        report['identity_recoveries']=sum(e['action']=='recovered' for e in runner.identity_diagnostics['events'])
        report['appearance_conflicts']=sum(e['action']=='appearance_conflict' for e in runner.identity_diagnostics['events'])
        report['raw_track_ids']=runner.identity_diagnostics['raw_track_ids']
        atomic_json(folder / 'identity_audit.json',runner.identity_diagnostics)
        atomic_json(folder / 'report.json', report)
        for name, rows, fields in (
            ('crossings.csv', report['crossings'], ['type','id','visitor_id','seconds','doorway']),
            ('zone_visits.csv', report['visits'], ['id','zone','visit','entered_seconds','exited_seconds','dwell_seconds','status']),
            ('zone_totals.csv', report['zone_totals'], ['id','zone','dwell_seconds']),
        ):
            with (folder / name).open('w', newline='', encoding='utf-8-sig') as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        atomic_json(folder / 'status.json', {**status, 'state':'complete',
                    'message':'Video and reports saved.', 'frames_processed':stats.frames, 'report':report})
    except Exception as error:
        atomic_json(folder / 'status.json', {**status, 'state':'failed', 'message':str(error), 'report':stats.report()})
        raise
    finally:
        if encoder is not None and encoder.poll() is None:
            if encoder.stdin and not encoder.stdin.closed:
                encoder.stdin.close()
            encoder.terminate()
            encoder.wait(timeout=15)
        stderr.close()
        track_log.close()


if __name__ == '__main__':
    process_run(sys.argv[1])
