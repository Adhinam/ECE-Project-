import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Store Intelligence Dashboard", layout="wide")

st.title("🏪 Store Intelligence Dashboard")
st.write("AI-powered retail analytics using YOLOv8 + ByteTrack")

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("Visitors", 61)

with col2:
    st.metric("Group Entries", 15)

with col3:
    st.metric("Average Occupancy", "19.87")

with col4:
    st.metric("Ended Tracks", 34)

st.divider()
# EXECUTIVE SUMMARY

st.header("📋 Executive Summary")

st.success("""
Store processed 61 visitors with an average occupancy of 19.87 people.

15 group entries were detected, indicating strong group-shopping behavior.

Peak occupancy periods suggest opportunities for optimized staffing.
""")

st.divider()

# BUSINESS INSIGHTS
st.header("📊 Business Insights")

st.success("Peak occupancy detected during busiest period.")
st.info("15 group entries observed.")
st.warning("34 visitor journeys completed.")

st.divider()

data = pd.DataFrame({
    "Metric": ["Visitors", "Group Entries", "Ended Tracks"],
    "Value": [61, 15, 34]
})

fig = px.bar(data, x="Metric", y="Value", title="Store Activity Summary")
st.plotly_chart(fig, use_container_width=True)

st.divider()

import os

st.header("🎥 Processed Tracking Video")

video_path = "tracking_outputs/tracked_h264.mp4"

st.write("File exists:", os.path.exists(video_path))
st.write(f"Video size: {os.path.getsize(video_path)/1024/1024:.2f} MB")

if os.path.exists(video_path):
    with open(video_path, "rb") as f:
        video_bytes = f.read()

    st.video(video_bytes)

    st.success("Tracked video loaded successfully.")
else:
    st.error("Tracked video not found.")

st.header("🚀 Solution Overview")

st.write("✅ Person Detection (YOLOv8)")
st.write("✅ Multi-Object Tracking (ByteTrack)")
st.write("✅ Visitor Counting")
st.write("✅ Group Detection")
st.write("✅ Occupancy Analytics")
st.write("✅ Business Insights Dashboard")