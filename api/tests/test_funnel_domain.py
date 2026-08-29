# PROMPT:
# API domain funnel helper tests — conversion rate from stage counts.
#
# CHANGES MADE:
# - build_funnel_response conversion_rate assertion for entry → checkout_proxy

"""API package unit tests."""

from datetime import datetime, timezone

from api.domain.funnel import build_funnel_response


def test_build_funnel_response_conversion():
    now = datetime.now(timezone.utc)
    resp = build_funnel_response(
        store_id="store-001",
        from_time=now,
        to_time=now,
        stage_counts={"entry": 100, "checkout_proxy": 20},
    )
    assert resp.conversion_rate == 0.2
