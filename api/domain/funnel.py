"""Funnel analytics — legacy adapter over app.funnel."""

from __future__ import annotations

from app.funnel import FunnelStageName, build_funnel_stages
from schemas.api import FunnelResponse, FunnelStage


def build_funnel_response(
    *,
    store_id: str,
    from_time,
    to_time,
    stage_counts: dict[str, int],
) -> FunnelResponse:
    """Map legacy stage keys to canonical funnel stages."""
    key_map = {
        "entry": FunnelStageName.ENTRY,
        "zone_visit": FunnelStageName.ZONE_VISIT,
        "browsing": FunnelStageName.ZONE_VISIT,
        "queue": FunnelStageName.BILLING_QUEUE,
        "billing_queue": FunnelStageName.BILLING_QUEUE,
        "checkout_proxy": FunnelStageName.PURCHASE,
        "purchase": FunnelStageName.PURCHASE,
        "exit": FunnelStageName.PURCHASE,
    }
    counts = {stage: 0 for stage in FunnelStageName}
    for key, value in stage_counts.items():
        mapped = key_map.get(key.lower())
        if mapped is not None:
            counts[mapped] = max(counts[mapped], value)

    stages = build_funnel_stages(counts)
    entry = counts[FunnelStageName.ENTRY]
    purchase = counts[FunnelStageName.PURCHASE]
    conversion = (purchase / entry) if entry else 0.0

    return FunnelResponse(
        store_id=store_id,
        from_time=from_time,
        to_time=to_time,
        stages=stages,
        conversion_rate=conversion,
        is_empty=entry == 0,
    )
