"""Live anomaly feed component."""

from __future__ import annotations

import streamlit as st

from schemas.api import AnomaliesResponse


def render_anomaly_feed(data: AnomaliesResponse) -> None:
    st.subheader("Live anomalies")
    if not data.items:
        st.info("No anomalies detected.")
        return
    for item in data.items:
        sev = item.severity.value if hasattr(item.severity, "value") else str(item.severity)
        level = "error" if sev == "CRITICAL" else "warning" if sev == "WARN" else "info"
        text = (
            f"**{item.anomaly_type}** · {sev} — {item.message}\n\n"
            f"_{item.suggested_action}_"
        )
        getattr(st, level)(text)
