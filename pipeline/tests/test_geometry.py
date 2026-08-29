# PROMPT:
# Pipeline geometry unit tests (point_in_polygon).
#
# CHANGES MADE:
# - Staff/zone polygon hit tests without video I/O

"""Pipeline geometry unit tests."""

from pipeline.app.geometry import point_in_polygon


def test_point_in_polygon_placeholder():
    # TODO: replace with real polygon once implemented
    assert point_in_polygon(0, 0, [[0, 0], [10, 0], [10, 10], [0, 10]]) is False
