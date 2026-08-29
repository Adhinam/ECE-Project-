"""Initial schema — raw_events, rollups, anomalies.

Revision ID: 001
Revises:
Create Date: 2026-05-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "raw_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("store_id", sa.String(64), nullable=False),
        sa.Column("camera_id", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("global_person_id", sa.String(64), nullable=True),
        sa.Column("track_id", sa.Integer, nullable=True),
        sa.Column("is_staff", sa.Boolean, server_default="false"),
        sa.Column("schema_version", sa.SmallInteger, server_default="1"),
    )
    op.create_index("idx_raw_events_occurred", "raw_events", ["store_id", "occurred_at"])

    op.create_table(
        "metric_buckets",
        sa.Column("store_id", sa.String(64), primary_key=True),
        sa.Column("bucket_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("entries", sa.Integer, server_default="0"),
        sa.Column("exits", sa.Integer, server_default="0"),
        sa.Column("unique_visitors", sa.Integer, server_default="0"),
        sa.Column("staff_excluded", sa.Integer, server_default="0"),
        sa.Column("avg_queue_depth", sa.Float, nullable=True),
        sa.Column("max_queue_depth", sa.Float, nullable=True),
        sa.Column("avg_dwell_seconds", sa.Float, nullable=True),
        sa.Column("conversion_rate", sa.Float, nullable=True),
    )

    op.create_table(
        "funnel_counts",
        sa.Column("store_id", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage", sa.String(32), primary_key=True),
        sa.Column("count", sa.Integer, nullable=False),
    )

    op.create_table(
        "heatmap_cells",
        sa.Column("store_id", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("cell_x", sa.SmallInteger, primary_key=True),
        sa.Column("cell_y", sa.SmallInteger, primary_key=True),
        sa.Column("visit_count", sa.Integer, server_default="0"),
        sa.Column("dwell_seconds", sa.Float, server_default="0"),
    )

    op.create_table(
        "anomalies",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("store_id", sa.String(64), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("observed_value", sa.Float, nullable=False),
        sa.Column("expected_value", sa.Float, nullable=True),
        sa.Column("z_score", sa.Float, nullable=True),
        sa.Column("details", postgresql.JSONB, nullable=True),
    )

    op.create_table(
        "ingest_batches",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("accepted", sa.Integer, server_default="0"),
        sa.Column("duplicates", sa.Integer, server_default="0"),
        sa.Column("rejected", sa.Integer, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("ingest_batches")
    op.drop_table("anomalies")
    op.drop_table("heatmap_cells")
    op.drop_table("funnel_counts")
    op.drop_table("metric_buckets")
    op.drop_index("idx_raw_events_occurred", table_name="raw_events")
    op.drop_table("raw_events")
