"""Compare detector input sizes on the same local, crowded-zone frames."""
import json
import argparse
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

from pipeline.detect import PersonDetector


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',default='yolo26n.pt')
    args=parser.parse_args()
    source=Path('sample_video/output/app_runs/run_5e2ae82c9e7c/source.mp4')
    output=Path('.runtime/tracking-improvement')
    output.mkdir(exist_ok=True,parents=True)
    cap=cv2.VideoCapture(str(source))
    times=[10,20,35,50,65,80,95,105]
    frames=[]
    for seconds in times:
        cap.set(cv2.CAP_PROP_POS_MSEC,seconds*1000)
        ok,frame=cap.read()
        if ok:
            frames.append((seconds,frame))
    cap.release()
    results=[]
    for size in (640,960):
        detector=PersonDetector(model_path=args.model,confidence=.1,iou=.45,
                                person_class_id=0,device='cpu',imgsz=size)
        detector.detect_frame(frames[0][1],0)
        tiles=[]
        for seconds,frame in frames:
            started=perf_counter()
            detections=detector.detect_frame(frame,round(seconds*13.093))
            row={'size':size,'seconds':seconds,'ms':(perf_counter()-started)*1000,'detections':[]}
            image=frame.copy()
            for det in detections.detections:
                x1,y1,x2,y2=det.bbox_xyxy
                row['detections'].append({'box':list(det.bbox_xyxy),'score':det.confidence})
                color=(70,255,70) if det.confidence>=.6 else (0,160,255)
                cv2.rectangle(image,(x1,y1),(x2,y2),color,2)
                cv2.putText(image,f'{det.confidence:.2f}',(x1,max(15,y1)),0,.5,color,1)
            crop=image[0:340,460:990]
            cv2.rectangle(crop,(0,0),(220,28),(0,0,0),-1)
            cv2.putText(crop,f'{size}px at {seconds}s',(6,22),0,.6,(255,255,255),1)
            tiles.append(crop)
            results.append(row)
        cv2.imwrite(str(output/f'{Path(args.model).stem}-dairy-{size}.jpg'),np.vstack([np.hstack(tiles[i:i+2]) for i in range(0,8,2)]))
    (output/f'{Path(args.model).stem}-detector-comparison.json').write_text(json.dumps(results,indent=2))
    print(json.dumps({str(size):{'average_ms':np.mean([r['ms'] for r in results if r['size']==size]),
                                'detections':[len(r['detections']) for r in results if r['size']==size]}
                      for size in (640,960)},indent=2))


if __name__=='__main__':
    main()
