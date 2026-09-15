"""Database layer: schema + idempotent upsert.

The schema uses a "long" format (one observation per row) rather than one
column per metric. Reason: metrics have different granularities and
dimensions (generation has a fuel type, price has a currency), and adding a
new metric doesn't require a table migration.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    String,
    UniqueConstraint,
    create_engine,
    func,
    or_,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .config import config

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Observation(Base):
    """A single measurement: (country, metric, timestamp, type) -> value."""

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(8), nullable=False)
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    # psr_type = resource type (e.g. wind, solar, gas). Empty for load/price.
    # 64 characters: the longest name returned by the API is
    # "Hydro Run-of-river and poundage" (31), so we leave headroom.
    psr_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )

    __table_args__ = (
        # Natural key: running twice over the same interval doesn't duplicate data.
        UniqueConstraint("country", "metric", "psr_type", "ts", name="uq_observation"),
        Index("ix_obs_lookup", "country", "metric", "ts"),
    )


def get_engine():
    return create_engine(config.database_url, future=True)


def init_db(engine=None) -> None:
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    logger.info("Database schema checked/created")


def last_timestamp(session: Session, country: str, metric: str) -> dt.datetime | None:
    """The last stored timestamp — used as a watermark so we don't re-fetch everything.

    Always timezone-aware. PostgreSQL already returns UTC, but SQLite (used
    for the demo and tests) doesn't keep the timezone, and a naive datetime
    can't be compared with `now(timezone.utc)` in the pipeline.
    """
    stmt = select(func.max(Observation.ts)).where(
        Observation.country == country, Observation.metric == metric
    )
    value = session.execute(stmt).scalar_one_or_none()
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value


# PostgreSQL accepts at most 65535 bound parameters in a single statement,
# and each row uses 7 (including `ingested_at`). A 30-day backfill at
# 15 minutes across ~10 resource types is ~29,000 rows — far above the
# limit — so we insert in chunks.
CHUNK_SIZE = 1000


def _upsert_chunk(session: Session, rows: list[dict]) -> int:
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f"Unsupported dialect for upsert: {dialect}")

    table = Observation.__table__
    stmt = insert(table).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["country", "metric", "psr_type", "ts"],
        set_={
            "value": stmt.excluded.value,
            "unit": stmt.excluded.unit,
            "ingested_at": stmt.excluded.ingested_at,
        },
        # Only rewrite what changed, so the count means something:
        # a repeated run over the same data reports 0.
        where=or_(table.c.value != stmt.excluded.value, table.c.unit != stmt.excluded.unit),
    )
    return session.execute(stmt).rowcount or 0


def upsert_observations(session: Session, rows: list[dict]) -> int:
    """Insert new rows and update values that changed.

    We don't just ignore duplicates: ENTSO-E revises already published data,
    and filling A03 curves (transform.expand_block_curve) can put a
    provisional value at the tail of a series. The next run corrects it.

    Returns the number of rows written (new or modified). All chunks run in
    the same transaction: either all are written, or none.
    """
    if not rows:
        return 0

    written = 0
    for start in range(0, len(rows), CHUNK_SIZE):
        written += _upsert_chunk(session, rows[start : start + CHUNK_SIZE])
    session.commit()
    return written
