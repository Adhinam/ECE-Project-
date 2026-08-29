"""
Store Intelligence — real-time Streamlit dashboard.

Run: streamlit run dashboard/streamlit_app.py
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.api_client import DashboardApiClient
from dashboard.settings import DashboardSettings
from schemas.api import (
    AnomaliesResponse,
    AnomalyItem,
    FunnelResponse,
    HeatmapResponse,
    HealthResponse,
    StoreMetricsResponse,
)

REFRESH_SECONDS = 2
HISTORY_LEN = 45

# ---------------------------------------------------------------------------
# Page config & styling
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Store Intelligence",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1400px; }
    div[data-testid="stMetric"] {
        background: linear-gradient(145deg, #1a1f2e 0%, #12151c 100%);
        border: 1px solid #2d3548;
        border-radius: 10px;
        padding: 14px 16px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.25);
    }
    div[data-testid="stMetric"] label { color: #9aa4b2; font-size: 0.8rem; }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] { color: #f0f3f7; }
    .status-pill {
        display: inline-flex; align-items: center; gap: 8px;
        padding: 6px 14px; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
    }
    .status-ok { background: #0d3320; color: #3dd68c; border: 1px solid #1a5c38; }
    .status-degraded { background: #3d3010; color: #f5c542; border: 1px solid #6b5218; }
    .status-down { background: #3d1214; color: #f56565; border: 1px solid #6b1f22; }
    .anomaly-card {
        padding: 12px 14px; margin-bottom: 8px; border-radius: 8px;
        border-left: 4px solid #64748b; background: #1a1f2e;
    }
    .anomaly-critical { border-left-color: #ef4444; }
    .anomaly-warn { border-left-color: #f59e0b; }
    .anomaly-info { border-left-color: #3b82f6; }
  </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Session state & client
# ---------------------------------------------------------------------------


@st.cache_resource
def _api_client(base_url: str) -> DashboardApiClient:
    return DashboardApiClient(base_url)


def _init_history() -> None:
    if "metric_history" not in st.session_state:
        st.session_state.metric_history = {
            "ts": deque(maxlen=HISTORY_LEN),
            "conversion": deque(maxlen=HISTORY_LEN),
            "queue_depth": deque(maxlen=HISTORY_LEN),
            "visitors": deque(maxlen=HISTORY_LEN),
        }


def _append_history(metrics: StoreMetricsResponse) -> None:
    hist = st.session_state.metric_history
    ts = metrics.as_of.astimezone(timezone.utc).strftime("%H:%M:%S")
    hist["ts"].append(ts)
    hist["conversion"].append(metrics.conversion_rate * 100)
    max_q = max((q.depth for q in metrics.queues), default=0)
    hist["queue_depth"].append(float(max_q))
    hist["visitors"].append(float(metrics.visitors_inside))


# ---------------------------------------------------------------------------
# UI components
# ---------------------------------------------------------------------------


def _status_pill(health: HealthResponse | None, store_id: str) -> None:
    if health is None:
        st.markdown(
            '<span class="status-pill status-down">● API UNREACHABLE</span>',
            unsafe_allow_html=True,
        )
        return

    store_status = next((s for s in health.stores if s.store_id == store_id), None)
    display = store_status.status if store_status else health.status
    css = {
        "ok": "status-ok",
        "degraded": "status-degraded",
        "down": "status-down",
        "unknown": "status-degraded",
    }.get(display, "status-degraded")
    label = display.upper()
    lag = ""
    if health.ingestion.lag_seconds is not None:
        lag = f" · ingest {health.ingestion.lag_seconds:.0f}s"
    st.markdown(
        f'<span class="status-pill {css}">● LIVE {label}{lag}</span>',
        unsafe_allow_html=True,
    )


def _render_metric_row(metrics: StoreMetricsResponse) -> None:
    avg_queue = (
        sum(q.depth for q in metrics.queues) / len(metrics.queues) if metrics.queues else 0.0
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Visitors inside", metrics.visitors_inside)
    c2.metric("Unique visitors", metrics.unique_visitors)
    c3.metric(
        "Conversion rate",
        f"{metrics.conversion_rate:.1%}",
        help="Session-based ENTRY → checkout zone",
    )
    c4.metric("Queue depth (max)", int(avg_queue) if metrics.queues else 0)
    c5.metric(
        "Abandonment",
        f"{metrics.abandonment_rate:.1%}",
        help="Exited without checkout",
    )


def _queue_chart(metrics: StoreMetricsResponse) -> None:
    if not metrics.queues:
        st.caption("No queue depth data in the current window.")
        return
    df = pd.DataFrame(
        [{"queue": q.queue_id, "depth": q.depth} for q in metrics.queues]
    ).sort_values("depth", ascending=True)
    fig = go.Figure(
        go.Bar(
            x=df["depth"],
            y=df["queue"],
            orientation="h",
            marker=dict(color=df["depth"], colorscale="Reds", showscale=False),
            text=df["depth"],
            textposition="outside",
        )
    )
    fig.update_layout(
        height=max(220, 56 * len(df)),
        margin=dict(l=8, r=24, t=28, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#9aa4b2"),
        title=dict(text="Live queue depth", font=dict(size=14, color="#e2e8f0")),
        xaxis=dict(title="People in queue", gridcolor="#2d3548"),
        yaxis=dict(gridcolor="#2d3548"),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _trend_charts() -> None:
    hist = st.session_state.metric_history
    if len(hist["ts"]) < 2:
        st.caption("Collecting trend data…")
        return

    ts = list(hist["ts"])
    col_a, col_b = st.columns(2)

    with col_a:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=list(hist["conversion"]),
                mode="lines+markers",
                name="Conversion %",
                line=dict(color="#3dd68c", width=2),
                fill="tozeroy",
                fillcolor="rgba(61,214,140,0.12)",
            )
        )
        fig.update_layout(
            height=260,
            margin=dict(l=8, r=8, t=36, b=8),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#9aa4b2"),
            title="Conversion rate (live)",
            yaxis=dict(title="%", gridcolor="#2d3548"),
            xaxis=dict(gridcolor="#2d3548"),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    with col_b:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=list(hist["queue_depth"]),
                mode="lines+markers",
                name="Max queue",
                line=dict(color="#f59e0b", width=2),
            )
        )
        fig.update_layout(
            height=260,
            margin=dict(l=8, r=8, t=36, b=8),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#9aa4b2"),
            title="Peak queue depth (live)",
            yaxis=dict(title="Depth", gridcolor="#2d3548"),
            xaxis=dict(gridcolor="#2d3548"),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _anomaly_card(item: AnomalyItem) -> None:
    sev = item.severity.value if hasattr(item.severity, "value") else str(item.severity)
    css = {
        "CRITICAL": "anomaly-critical",
        "WARN": "anomaly-warn",
        "INFO": "anomaly-info",
    }.get(sev, "anomaly-info")
    action = item.suggested_action or "Review in operations console."
    st.markdown(
        f"""
        <div class="anomaly-card {css}">
          <strong>{item.anomaly_type}</strong> · {sev}<br/>
          <span style="color:#cbd5e1;font-size:0.9rem;">{item.message}</span><br/>
          <span style="color:#94a3b8;font-size:0.8rem;">→ {action}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_anomalies(data: AnomaliesResponse | None) -> None:
    st.subheader("Anomaly alerts")
    if data is None or not data.items:
        st.success("No active anomalies")
        return
    critical = sum(
        1
        for i in data.items
        if (i.severity.value if hasattr(i.severity, "value") else str(i.severity)) == "CRITICAL"
    )
    if critical:
        st.error(f"{critical} critical alert(s) require attention")
    for item in data.items[:12]:
        _anomaly_card(item)


def _render_funnel(funnel: FunnelResponse | None) -> None:
    st.subheader("Conversion funnel")
    if funnel is None or not funnel.stages:
        st.caption("Funnel unavailable.")
        return
    df = pd.DataFrame(
        [
            {"stage": s.stage, "sessions": s.count, "dropoff_%": s.dropoff_percent}
            for s in funnel.stages
        ]
    )
    fig = go.Figure(
        go.Bar(
            x=df["stage"],
            y=df["sessions"],
            marker=dict(color="#60a5fa"),
            text=df["sessions"],
            textposition="outside",
        )
    )
    fig.update_layout(
        height=280,
        margin=dict(l=8, r=8, t=32, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#9aa4b2"),
        title=f"ENTRY→PURCHASE {funnel.conversion_rate:.1%}",
        yaxis=dict(title="Sessions", gridcolor="#2d3548"),
        xaxis=dict(gridcolor="#2d3548"),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_heatmap(heatmap: HeatmapResponse | None) -> None:
    st.subheader("Heatmap")
    if heatmap is None or not heatmap.cells:
        st.caption("Heatmap unavailable or empty in selected window.")
        return
    df = pd.DataFrame(
        [{"x": c.x, "y": c.y, "intensity": c.visits_normalized} for c in heatmap.cells]
    )
    pivot = df.pivot_table(index="y", columns="x", values="intensity", fill_value=0)
    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=[str(v) for v in pivot.columns.tolist()],
            y=[str(v) for v in pivot.index.tolist()],
            colorscale="YlOrRd",
            zmin=0,
            zmax=100,
        )
    )
    fig.update_layout(
        height=300,
        margin=dict(l=8, r=8, t=28, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#9aa4b2"),
        title=f"Visit intensity (confidence: {heatmap.data_confidence})",
        xaxis_title="Cell X",
        yaxis_title="Cell Y",
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# Live refresh fragment
# ---------------------------------------------------------------------------


def _render_live_panel(
    client: DashboardApiClient,
    store_id: str,
    window_minutes: int,
) -> None:
    snapshot = client.fetch_live_snapshot(store_id, window_minutes=window_minutes)
    errors = snapshot.get("errors", {})
    metrics: StoreMetricsResponse | None = snapshot.get("metrics")
    anomalies: AnomaliesResponse | None = snapshot.get("anomalies")
    health: HealthResponse | None = snapshot.get("health")
    funnel: FunnelResponse | None = snapshot.get("funnel")
    heatmap: HeatmapResponse | None = snapshot.get("heatmap")

    header_l, header_r = st.columns([3, 1])
    with header_l:
        updated = (
            metrics.as_of.astimezone(timezone.utc).strftime("%H:%M:%S UTC")
            if metrics
            else "—"
        )
        last_event = (
            health.latest_event_at.astimezone(timezone.utc).strftime("%H:%M:%S UTC")
            if health and health.latest_event_at
            else "—"
        )
        st.caption(
            f"Last refresh · {updated} · last event · {last_event} · every {REFRESH_SECONDS}s"
        )
    with header_r:
        _status_pill(health, store_id)

    if errors:
        for key, msg in errors.items():
            st.warning(f"Could not load {key}: {msg}")

    if metrics is None:
        st.error("Metrics unavailable — check API and database.")
        return

    if metrics.is_empty:
        st.info("Store is quiet — no visitor activity in the selected window.")

    _append_history(metrics)
    _render_metric_row(metrics)

    st.divider()
    chart_l, chart_r = st.columns([1, 1])
    with chart_l:
        _queue_chart(metrics)
    with chart_r:
        _trend_charts()

    st.divider()
    _render_anomalies(anomalies)
    st.divider()
    lower_l, lower_r = st.columns(2)
    with lower_l:
        _render_funnel(funnel)
    with lower_r:
        _render_heatmap(heatmap)


def _live_fragment(
    client: DashboardApiClient,
    store_id: str,
    window_minutes: int,
) -> None:
    """Auto-refresh every 2s via st.fragment(run_every=...) when supported."""
    use_meta_fallback = False
    try:
        decorator = st.fragment(run_every=timedelta(seconds=REFRESH_SECONDS))
    except (TypeError, ValueError):
        decorator = st.fragment
        use_meta_fallback = True

    @decorator
    def _tick() -> None:
        _render_live_panel(client, store_id, window_minutes)

    _tick()

    if use_meta_fallback:
        st.markdown(
            f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    settings = DashboardSettings()
    if settings.dashboard_refresh_seconds != REFRESH_SECONDS:
        pass  # streamlit_app uses fixed 2s per requirements

    client = _api_client(settings.dashboard_api_url)
    _init_history()

    st.title("Store Intelligence")
    st.markdown("Real-time retail analytics — metrics, queues, conversion, anomalies")

    # Sidebar
    st.sidebar.header("Controls")
    store_options = [settings.default_store_id]
    try:
        health = client.get_health()
        store_options = sorted({s.store_id for s in health.stores} or store_options)
    except Exception:
        health = None

    store_id = st.sidebar.selectbox(
        "Store",
        options=store_options,
        index=0,
        help="Select store to monitor",
    )
    custom = st.sidebar.text_input("Or enter store ID", value="")
    if custom.strip():
        store_id = custom.strip()

    window_minutes = st.sidebar.slider(
        "Metrics window (minutes)",
        min_value=5,
        max_value=120,
        value=15,
        step=5,
    )
    st.sidebar.caption(f"Auto-refresh: **{REFRESH_SECONDS}s**")
    if st.sidebar.button("Refresh now"):
        st.rerun()

    if health:
        st.sidebar.markdown("---")
        st.sidebar.markdown("**Platform health**")
        st.sidebar.write(f"API: `{health.status}`")
        st.sidebar.write(f"DB: `{health.database.status}` ({health.database.latency_ms} ms)")
        if health.ingestion.lag_seconds is not None:
            st.sidebar.write(f"Ingestion lag: `{health.ingestion.lag_seconds:.0f}s`")

    _live_fragment(client, store_id, window_minutes)


if __name__ == "__main__":
    main()
