"""Video frame source — file, RTSP, or webcam."""

from __future__ import annotations

import numpy as np

from pipeline.app.interfaces import IFrameSource


class VideoFrameSource(IFrameSource):
    def __init__(self, source: str) -> None:
        self._source = source
        self._cap = None  # TODO: cv2.VideoCapture

    def read(self) -> tuple[bool, np.ndarray | None]:
        # TODO: implement
        return False, None

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
