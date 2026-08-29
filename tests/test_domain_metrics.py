# PROMPT:
# Domain metrics tests — see test_domain_suite.py.
#
# CHANGES MADE:
# - compute_visitors_inside edge cases

"""Domain metrics tests."""

from __future__ import annotations

import pytest

from api.domain.metrics import compute_visitors_inside

pytestmark = pytest.mark.unit


def test_visitors_inside_non_negative() -> None:
    assert compute_visitors_inside(10, 3) == 7
    assert compute_visitors_inside(3, 10) == 0
