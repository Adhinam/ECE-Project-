"""Data access layer — SQLAlchemy repositories (no business rules)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Float, cast, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Anomaly, IngestBatch, MetricBucket, RawEvent
from schemas.events import EventEnvelope, EventType


def _payload_with_bbox(event: EventEnvelope) -> dict:
    """Persist bbox inside JSONB for heatmap aggregation (challenge contract)."""
    payload = dict(event.payload)
    if event.bbox is not None and "bbox" not in payload:
        payload["bbox"] = list(event.bbox)
    return payload


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_existing_ids(self, event_ids: list[UUID]) -> set[UUID]:
        if not event_ids:
            return set()
        stmt = select(RawEvent.event_id).where(RawEvent.event_id.in_(event_ids))
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def insert_events(
        self,
        events: list[EventEnvelope],
        *,
        commit: bool = True,
    ) -> int:
        """
        Insert events; skip duplicates via ON CONFLICT DO NOTHING.

        TODO(scaling): batch inserts >500 rows with COPY or partitioned tables.
        """
        if not events:
            return 0
        now = datetime.now(timezone.utc)
        rows = [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type),
                "store_id": e.store_id,
                "camera_id": e.camera_id,
                "occurred_at": e.occurred_at,
                "ingested_at": now,
                "payload": _payload_with_bbox(e),
                "confidence": e.confidence,
                "global_person_id": e.global_person_id,
                "track_id": e.track_id,
                "is_staff": e.is_staff,
                "schema_version": e.schema_version,
            }
            for e in events
        ]
        stmt = (
            pg_insert(RawEvent)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[RawEvent.event_id])
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        inserted = int(result.rowcount or 0)
        if commit:
            await self._session.commit()
        return inserted

    async def record_ingest_batch(
        self,
        batch_id: UUID,
        *,
        accepted: int,
        duplicates: int,
        rejected: int,
        commit: bool = True,
    ) -> None:
        self._session.add(
            IngestBatch(
                batch_id=batch_id,
                received_at=datetime.now(timezone.utc),
                accepted=accepted,
                duplicates=duplicates,
                rejected=rejected,
            )
        )
        await self._session.flush()
        if commit:
            await self._session.commit()

    async def get_latest_event_time(self, store_id: str) -> datetime | None:
        stmt = select(func.max(RawEvent.occurred_at)).where(RawEvent.store_id == store_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class MetricRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_buckets(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
    ) -> list[MetricBucket]:
        stmt = (
            select(MetricBucket)
            .where(MetricBucket.store_id == store_id)
            .where(MetricBucket.bucket_start >= from_time)
            .where(MetricBucket.bucket_start <= to_time)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


_SESSION_EVENT_TYPES = (
    EventType.ENTRY.value,
    EventType.EXIT.value,
    EventType.REENTRY.value,
    EventType.ZONE_ENTER.value,
    EventType.ZONE_EXIT.value,
    EventType.ZONE_DWELL.value,
    EventType.DWELL.value,
    EventType.QUEUE_JOIN.value,
    EventType.QUEUE_LEAVE.value,
)


class StoreMetricsRepository:
    """Optimized read queries for store metrics (single round-trip bundle)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fetch_session_events(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
    ) -> list[tuple[str, datetime, int | None, str | None, bool, float, dict]]:
        return await self._fetch_session_events(store_id, from_time, to_time)

    async def fetch_all(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
    ) -> tuple[
        bool,
        list[tuple[str, datetime, int | None, str | None, bool, float, dict]],
        list[tuple[str, float, int, float]],
        list[tuple[str, int, datetime, float]],
        int,
    ]:
        # TODO(scaling): collapse to one SQL round-trip or read from metric_buckets
        has_activity = await self._has_visitor_activity(store_id, from_time, to_time)
        if not has_activity:
            return False, [], [], [], 0

        session_rows = await self._fetch_session_events(store_id, from_time, to_time)
        zone_rows = await self._aggregate_zone_dwell(store_id, from_time, to_time)
        queue_rows = await self._fetch_latest_queue_depths(store_id, to_time)
        staff_count = await self._count_staff_events(store_id, from_time, to_time)
        return True, session_rows, zone_rows, queue_rows, staff_count

    async def _has_visitor_activity(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
    ) -> bool:
        stmt = (
            select(func.count())
            .select_from(RawEvent)
            .where(RawEvent.store_id == store_id)
            .where(RawEvent.occurred_at >= from_time)
            .where(RawEvent.occurred_at <= to_time)
            .where(RawEvent.is_staff.is_(False))
        )
        result = await self._session.execute(stmt)
        return (result.scalar_one() or 0) > 0

    async def _fetch_session_events(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
    ) -> list[tuple[str, datetime, int | None, str | None, bool, float, dict]]:
        stmt = (
            select(
                RawEvent.event_type,
                RawEvent.occurred_at,
                RawEvent.track_id,
                RawEvent.global_person_id,
                RawEvent.is_staff,
                RawEvent.confidence,
                RawEvent.payload,
            )
            .where(RawEvent.store_id == store_id)
            .where(RawEvent.occurred_at >= from_time)
            .where(RawEvent.occurred_at <= to_time)
            .where(RawEvent.event_type.in_(_SESSION_EVENT_TYPES))
            .order_by(RawEvent.occurred_at.asc())
        )
        result = await self._session.execute(stmt)
        return [
            (
                row.event_type,
                row.occurred_at,
                row.track_id,
                row.global_person_id,
                row.is_staff,
                row.confidence,
                row.payload or {},
            )
            for row in result.all()
        ]

    async def _aggregate_zone_dwell(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
    ) -> list[tuple[str, float, int, float]]:
        zone_id_expr = RawEvent.payload["zone_id"].astext
        dwell_expr = cast(RawEvent.payload["dwell_seconds"].astext, Float)
        stmt = (
            select(
                zone_id_expr.label("zone_id"),
                func.avg(dwell_expr).label("avg_dwell"),
                func.count().label("sample_count"),
                func.avg(RawEvent.confidence).label("avg_confidence"),
            )
            .where(RawEvent.store_id == store_id)
            .where(RawEvent.occurred_at >= from_time)
            .where(RawEvent.occurred_at <= to_time)
            .where(RawEvent.is_staff.is_(False))
            .where(RawEvent.event_type.in_((EventType.ZONE_DWELL.value, EventType.DWELL.value)))
            .where(zone_id_expr.isnot(None))
            .where(dwell_expr.isnot(None))
            .group_by(zone_id_expr)
        )
        result = await self._session.execute(stmt)
        rows: list[tuple[str, float, int, float]] = []
        for row in result.all():
            if not row.zone_id:
                continue
            rows.append(
                (
                    row.zone_id,
                    float(row.avg_dwell or 0.0),
                    int(row.sample_count or 0),
                    float(row.avg_confidence or 0.0),
                )
            )
        return rows

    async def _fetch_latest_queue_depths(
        self,
        store_id: str,
        to_time: datetime,
    ) -> list[tuple[str, int, datetime, float]]:
        """PostgreSQL DISTINCT ON — latest queue_depth per queue_id."""
        sql = text(
            """
            SELECT DISTINCT ON (payload->>'queue_id')
                payload->>'queue_id' AS queue_id,
                COALESCE(
                    (payload->>'depth')::int,
                    (payload->>'queue_depth')::int,
                    0
                ) AS depth,
                occurred_at,
                confidence
            FROM raw_events
            WHERE store_id = :store_id
              AND event_type = :event_type
              AND is_staff = false
              AND occurred_at <= :to_time
              AND payload->>'queue_id' IS NOT NULL
            ORDER BY payload->>'queue_id', occurred_at DESC
            """
        )
        result = await self._session.execute(
            sql,
            {
                "store_id": store_id,
                "event_type": EventType.QUEUE_DEPTH.value,
                "to_time": to_time,
            },
        )
        return [
            (
                str(row.queue_id),
                int(row.depth or 0),
                row.occurred_at,
                float(row.confidence or 0.0),
            )
            for row in result.all()
            if row.queue_id
        ]

    async def _count_staff_events(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(RawEvent)
            .where(RawEvent.store_id == store_id)
            .where(RawEvent.occurred_at >= from_time)
            .where(RawEvent.occurred_at <= to_time)
            .where(RawEvent.is_staff.is_(True))
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)


class AnomalyDataRepository:
    """Signal queries for live anomaly detection."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fetch_metric_buckets(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
    ) -> list[MetricBucket]:
        stmt = (
            select(MetricBucket)
            .where(MetricBucket.store_id == store_id)
            .where(MetricBucket.bucket_start >= from_time)
            .where(MetricBucket.bucket_start <= to_time)
            .order_by(MetricBucket.bucket_start.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def fetch_zone_visit_counts(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
    ) -> dict[str, int]:
        zone_id_expr = RawEvent.payload["zone_id"].astext
        stmt = (
            select(zone_id_expr.label("zone_id"), func.count().label("visits"))
            .where(RawEvent.store_id == store_id)
            .where(RawEvent.occurred_at >= from_time)
            .where(RawEvent.occurred_at <= to_time)
            .where(RawEvent.is_staff.is_(False))
            .where(
                RawEvent.event_type.in_(
                    (
                        EventType.ZONE_ENTER.value,
                        EventType.ZONE_DWELL.value,
                        EventType.DWELL.value,
                    )
                )
            )
            .where(zone_id_expr.isnot(None))
            .group_by(zone_id_expr)
        )
        result = await self._session.execute(stmt)
        return {str(row.zone_id): int(row.visits or 0) for row in result.all() if row.zone_id}

    async def fetch_queue_depth_samples(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
    ) -> list[tuple[str, int, datetime]]:
        sql = text(
            """
            SELECT
                payload->>'queue_id' AS queue_id,
                COALESCE(
                    (payload->>'depth')::int,
                    (payload->>'queue_depth')::int,
                    0
                ) AS depth,
                occurred_at
            FROM raw_events
            WHERE store_id = :store_id
              AND event_type = :event_type
              AND is_staff = false
              AND occurred_at >= :from_time
              AND occurred_at <= :to_time
              AND payload->>'queue_id' IS NOT NULL
            ORDER BY occurred_at ASC
            """
        )
        result = await self._session.execute(
            sql,
            {
                "store_id": store_id,
                "event_type": EventType.QUEUE_DEPTH.value,
                "from_time": from_time,
                "to_time": to_time,
            },
        )
        return [
            (str(row.queue_id), int(row.depth or 0), row.occurred_at)
            for row in result.all()
            if row.queue_id
        ]

    async def fetch_latest_event_per_camera(
        self,
        store_id: str,
    ) -> list[tuple[str, datetime]]:
        stmt = (
            select(
                RawEvent.camera_id,
                func.max(RawEvent.occurred_at).label("last_at"),
            )
            .where(RawEvent.store_id == store_id)
            .group_by(RawEvent.camera_id)
        )
        result = await self._session.execute(stmt)
        return [(str(row.camera_id), row.last_at) for row in result.all()]

    async def fetch_latest_store_event(self, store_id: str) -> datetime | None:
        stmt = select(func.max(RawEvent.occurred_at)).where(RawEvent.store_id == store_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class HealthDataRepository:
    """Aggregated signals for production health checks."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fetch_global_timestamps(
        self,
    ) -> tuple[datetime | None, datetime | None]:
        occurred_stmt = select(func.max(RawEvent.occurred_at))
        ingested_stmt = select(func.max(RawEvent.ingested_at))
        occurred = (await self._session.execute(occurred_stmt)).scalar_one_or_none()
        ingested = (await self._session.execute(ingested_stmt)).scalar_one_or_none()
        return occurred, ingested

    async def fetch_latest_ingest_batch_time(self) -> datetime | None:
        stmt = select(func.max(IngestBatch.received_at))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_ingest_batches_since(self, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(IngestBatch)
            .where(IngestBatch.received_at >= since)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def list_store_ids(self) -> list[str]:
        stmt = select(RawEvent.store_id).distinct().order_by(RawEvent.store_id.asc())
        result = await self._session.execute(stmt)
        return [str(row) for row in result.scalars().all()]

    async def fetch_per_store_timestamps(
        self,
    ) -> list[tuple[str, datetime | None, datetime | None]]:
        stmt = (
            select(
                RawEvent.store_id,
                func.max(RawEvent.occurred_at).label("last_occurred"),
                func.max(RawEvent.ingested_at).label("last_ingested"),
            )
            .group_by(RawEvent.store_id)
            .order_by(RawEvent.store_id.asc())
        )
        result = await self._session.execute(stmt)
        return [
            (str(row.store_id), row.last_occurred, row.last_ingested) for row in result.all()
        ]

    async def fetch_camera_last_events(
        self,
    ) -> list[tuple[str, str, datetime]]:
        stmt = (
            select(
                RawEvent.store_id,
                RawEvent.camera_id,
                func.max(RawEvent.occurred_at).label("last_at"),
            )
            .group_by(RawEvent.store_id, RawEvent.camera_id)
            .order_by(RawEvent.store_id.asc(), RawEvent.camera_id.asc())
        )
        result = await self._session.execute(stmt)
        return [
            (str(row.store_id), str(row.camera_id), row.last_at) for row in result.all()
        ]


class AnomalyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_recent(self, store_id: str, limit: int = 20) -> list[Anomaly]:
        stmt = (
            select(Anomaly)
            .where(Anomaly.store_id == store_id)
            .order_by(Anomaly.detected_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def insert(self, anomaly: Anomaly) -> Anomaly:
        self._session.add(anomaly)
        await self._session.commit()
        await self._session.refresh(anomaly)
        return anomaly


class HeatmapRepository:
    """Read path for heatmap cell aggregation from raw_events."""

    _HEATMAP_TYPES = (
        EventType.POSITION_SNAPSHOT.value,
        EventType.ZONE_ENTER.value,
        EventType.ZONE_DWELL.value,
        EventType.DWELL.value,
    )

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fetch_spatial_samples(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
    ) -> list[tuple[float, float, float]]:
        """
        Return (x, y, dwell_seconds) pixel coordinates for binning.

        Uses payload cell_x/cell_y, normalized_x/y, or bbox centroid.
        """
        stmt = (
            select(RawEvent.payload)
            .where(RawEvent.store_id == store_id)
            .where(RawEvent.occurred_at >= from_time)
            .where(RawEvent.occurred_at <= to_time)
            .where(RawEvent.is_staff.is_(False))
            .where(RawEvent.event_type.in_(self._HEATMAP_TYPES))
        )
        result = await self._session.execute(stmt)
        samples: list[tuple[float, float, float]] = []
        for (payload,) in result.all():
            payload = payload or {}
            dwell = float(payload.get("dwell_seconds") or 0.0)
            if "cell_x" in payload and "cell_y" in payload:
                x = float(payload["cell_x"])
                y = float(payload["cell_y"])
            elif "normalized_x" in payload and "normalized_y" in payload:
                x = float(payload["normalized_x"])
                y = float(payload["normalized_y"])
            elif "bbox" in payload and isinstance(payload["bbox"], (list, tuple)):
                b = payload["bbox"]
                if len(b) >= 4:
                    x = (float(b[0]) + float(b[2])) / 2.0
                    y = (float(b[1]) + float(b[3])) / 2.0
                else:
                    continue
            else:
                continue
            samples.append((x, y, dwell))
        return samples
