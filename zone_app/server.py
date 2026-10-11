"""Loopback-only app server. Run from the project with python -m zone_app.server."""
import argparse
import json
import math
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
from uuid import uuid4
import webbrowser

import cv2
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from shapely.geometry import Polygon

from schemas.config import StoreLayoutConfig
from zone_app.worker import atomic_json

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
REGISTRY = ROOT / '.runtime' / 'zone-app'
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
PROCESSES = {}
RESULT_FILES = ('annotated.mp4', 'report.json', 'layout.json', 'crossings.csv',
                'zone_visits.csv', 'zone_totals.csv', 'queue_totals.csv', 'queue_visits.csv')
app = FastAPI(title='Store Intelligence Video App')
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])


@app.middleware('http')
async def local_only(request, call_next):
    if request.method in ('POST','PUT','DELETE','PATCH'):
        if request.headers.get('x-app-token') != TOKEN:
            return JSONResponse({'detail':'Open the app at its local website before saving or processing.'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'Requests must come from this app.'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def output_base(value):
    path = Path(value or 'sample_video/output/app_runs')
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(WORKSPACE):
        raise HTTPException(400, f'Choose an output folder inside {WORKSPACE}.')
    if any(part in {'.git','.venv','.codex','.agents','.aws'} for part in path.relative_to(WORKSPACE).parts):
        raise HTTPException(400, 'Choose an ordinary project output folder.')
    return path


def job_folder(identity):
    if not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise HTTPException(404, 'Run not found.')
    file = REGISTRY / f'{identity}.json'
    if not file.is_file():
        raise HTTPException(404, 'Run not found.')
    folder = Path(json.loads(file.read_text())['folder']).resolve()
    if not folder.is_relative_to(WORKSPACE):
        raise HTTPException(400, 'Invalid output folder.')
    return folder


def validate_layout(data, spec):
    try:
        layout = StoreLayoutConfig.model_validate(data)
        if (layout.frame_width,layout.frame_height) != (spec['width'],spec['height']):
            raise ValueError('Layout dimensions do not match the uploaded video.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', layout.store_id):
            raise ValueError('Store ID must use letters, numbers, hyphens or underscores.')
        names = set()
        for kind in ('zones','queues','staff_areas','entry_exit_lines'):
            for shape in getattr(layout,kind):
                if not shape.id.strip() or shape.id in names:
                    raise ValueError('Each shape needs a unique name.')
                names.add(shape.id)
                pts = [shape.p1,shape.p2] if kind=='entry_exit_lines' else shape.polygon
                if any(len(p)!=2 or not all(isinstance(v,int) and not isinstance(v,bool) for v in p)
                       or not 0<=p[0]<spec['width'] or not 0<=p[1]<spec['height'] for p in pts):
                    raise ValueError('Shape points must be inside the video.')
                if kind=='entry_exit_lines':
                    if pts[0]==pts[1] or shape.direction_in not in ('top','bottom','left','right'):
                        raise ValueError('Invalid doorway line or entry direction.')
                elif len(pts)<3 or not Polygon(pts).is_valid or Polygon(pts).area<1:
                    raise ValueError('Sections must enclose an area without crossing themselves.')
                if kind=='queues' and shape.max_capacity<1:
                    raise ValueError('Queue capacity must be positive.')
        if not names:
            raise ValueError('Draw at least one section or doorway.')
        if layout.dwell_threshold_seconds<0:
            raise ValueError('Dwell threshold cannot be negative.')
        return layout.model_dump()
    except (ValueError,TypeError) as error:
        raise HTTPException(400, str(error)) from error


@app.get('/')
def home():
    return FileResponse(ROOT / 'zone-editor/index.html')


@app.get('/{script_name}')
def script(script_name: str):
    if script_name not in ('editor.js','processing.js'):
        raise HTTPException(404)
    return FileResponse(ROOT / 'zone-editor' / script_name, media_type='application/javascript')


@app.get('/api/info')
def info():
    return {'token':TOKEN, 'project_folder':str(ROOT), 'workspace':str(WORKSPACE),
            'default_output':'sample_video/output/app_runs'}


@app.post('/api/jobs')
async def upload(request: Request, output: str='sample_video/output/app_runs', filename: str='video.mp4'):
    base = output_base(output)
    suffix = Path(filename).suffix.lower()
    if suffix not in ('.mp4','.avi','.mov','.mkv','.webm','.m4v'):
        raise HTTPException(400, 'Choose a supported video file.')
    identity = uuid4().hex
    folder = base / ('run_'+identity[:12])
    try:
        folder.mkdir(parents=True, exist_ok=False)
        source = folder / ('source'+suffix)
        size=0
        with source.open('wb') as handle:
            async for chunk in request.stream():
                size+=len(chunk)
                if size>8*1024**3:
                    raise HTTPException(413, 'Video exceeds the 8 GB upload limit.')
                handle.write(chunk)
        cap = cv2.VideoCapture(str(source))
        try:
            ok, frame = cap.read()
            fps=cap.get(cv2.CAP_PROP_FPS)
            if not ok or frame is None or not math.isfinite(fps) or fps<=0:
                raise HTTPException(400,'The processing engine cannot decode this video or read its frame rate.')
            spec={'id':identity, 'source':source.name, 'name':Path(filename).name,
                  'width':frame.shape[1], 'height':frame.shape[0], 'fps':fps,
                  'frame_count':int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 'camera_id':'camera-1'}
        finally:
            cap.release()
        atomic_json(folder / 'job.json', spec)
        atomic_json(folder / 'status.json', {'state':'ready','message':'Uploaded. Ready to process.', 'output_folder':str(folder)})
        REGISTRY.mkdir(parents=True,exist_ok=True)
        atomic_json(REGISTRY / f'{identity}.json', {'folder':str(folder)})
        return {**spec,'output_folder':str(folder)}
    except HTTPException:
        # Retain neither an invalid upload nor a large partially uploaded source.
        for child in folder.iterdir() if folder.exists() else []:
            if child.is_file():
                child.unlink()
        if folder.exists():
            folder.rmdir()
        raise
    except OSError as error:
        raise HTTPException(400, f'Cannot save into this folder: {error}') from error


def read_status(identity):
    folder=job_folder(identity)
    data=json.loads((folder/'status.json').read_text(encoding='utf-8'))
    process=PROCESSES.get(identity)
    if data['state'] in ('running','finalizing') and (process is None or process.poll() is not None):
        data.update(state='failed',message='Processing stopped before completion. Start a new run; see processing.log for details.')
        atomic_json(folder/'status.json',data)
    return data


@app.get('/api/jobs')
def jobs():
    found=[]
    for file in sorted(REGISTRY.glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:30]:
        try:
            spec=json.loads((job_folder(file.stem)/'job.json').read_text(encoding='utf-8'))
            state=read_status(file.stem)
            found.append({'id':file.stem,'name':spec['name'],'state':state['state'],'output_folder':state['output_folder']})
        except (OSError, ValueError, HTTPException):
            continue
    return found


@app.get('/api/jobs/{identity}')
def status(identity: str):
    data = read_status(identity)
    folder = job_folder(identity)
    data['available_files'] = [name for name in RESULT_FILES if (folder / name).is_file()
                               and (name == 'layout.json' or data['state'] == 'complete')]
    return data


@app.post('/api/jobs/{identity}/run')
async def run(identity: str, request: Request):
    payload=await request.json()
    folder=job_folder(identity)
    spec=json.loads((folder/'job.json').read_text(encoding='utf-8'))
    layout=validate_layout(payload.get('layout',{}),spec)
    camera=payload.get('camera_id','camera-1')
    if not isinstance(camera,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',camera):
        raise HTTPException(400,'Camera ID must use letters, numbers, hyphens or underscores.')
    with LOCK:
        if any(p.poll() is None for p in PROCESSES.values()):
            raise HTTPException(409,'Another video is processing. Wait until it finishes.')
        if read_status(identity)['state']!='ready':
            raise HTTPException(409,'This run has already started. Upload again to create a new run.')
        spec['camera_id']=camera
        atomic_json(folder/'job.json',spec)
        atomic_json(folder/'layout.json',layout)
        atomic_json(folder/'status.json',{'state':'running','message':'Starting detector…','output_folder':str(folder)})
        with (folder/'processing.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,'-m','zone_app.worker',str(folder)], cwd=ROOT,
                                     stdout=log,stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        PROCESSES[identity]=process
    return {'id':identity,'state':'running'}


@app.get('/api/jobs/{identity}/files/{filename}')
def output_file(identity: str, filename: str, download: bool=False):
    folder=job_folder(identity)
    if filename not in RESULT_FILES:
        raise HTTPException(404, 'This file is not available for this saved run. Older runs may not contain billing queue reports.')
    if filename!='layout.json' and read_status(identity)['state']!='complete':
        raise HTTPException(409,'Results are available when processing finishes.')
    file=folder/filename
    if not file.is_file():
        raise HTTPException(404, 'This file is not available for this saved run. Older runs may not contain billing queue reports.')
    return FileResponse(file, filename=filename if download else None,
                        media_type='video/mp4' if filename.endswith('.mp4') else None)


def main():
    import os
    import uvicorn
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    os.chdir(ROOT)
    if not args.no_browser:
        threading.Timer(2,lambda:webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    uvicorn.run(app,host='127.0.0.1',port=args.port)


if __name__=='__main__':
    main()
