# PROMPT:
# VideoReader and path utility tests.
#
# CHANGES MADE:
# - Frame iteration and metadata helpers

"""Tests for pipeline.utils path helpers."""

from pathlib import Path

import pytest

from pipeline.utils import iter_video_paths


def test_iter_video_paths_single_file(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00")
    paths = iter_video_paths(video)
    assert paths == [video.resolve()]


def test_iter_video_paths_directory(tmp_path: Path):
    (tmp_path / "a.mp4").write_bytes(b"\x00")
    (tmp_path / "b.mp4").write_bytes(b"\x00")
    (tmp_path / "ignore.txt").write_text("x")
    paths = iter_video_paths(tmp_path)
    assert len(paths) == 2


def test_iter_video_paths_empty_dir_raises(tmp_path: Path):
    with pytest.raises(ValueError, match="No videos"):
        iter_video_paths(tmp_path)


def test_iter_video_paths_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        iter_video_paths(tmp_path / "nope")
