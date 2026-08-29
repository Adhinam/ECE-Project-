"""KPI metric cards for Streamlit."""

from __future__ import annotations

import streamlit as st

from schemas.api import RealtimeMetricsResponse


def render_kpi_cards(metrics: RealtimeMetricsResponse) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Visitors inside", metrics.visitors_inside)
    c2.metric("Entries", metrics.entries)
    c3.metric("Conversion rate", f"{metrics.conversion_rate:.1%}")
    c4.metric("Avg queue depth", metrics.avg_queue_depth or 0)
