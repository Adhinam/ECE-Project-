"""Explain camera geometry that cannot yield observed doorway crossings."""
import math


def calibration_warnings(layout):
    warnings=[]
    w,h=layout.frame_width,layout.frame_height
    for line in layout.entry_exit_lines:
        xs=[line.p1[0],line.p2[0]]; ys=[line.p1[1],line.p2[1]]
        if (max(xs)<w*.025 or min(xs)>w*.975 or max(ys)<h*.025 or min(ys)>h*.975):
            warnings.append(f'Doorway "{line.id}" is at the image edge. Move it into the visible walkway so a tracked person’s centre can be observed on both sides.')
        if math.dist(line.p1,line.p2)<min(w,h)*.05:
            warnings.append(f'Doorway "{line.id}" is very short. Make sure it spans the visible path through the doorway.')
    return warnings
