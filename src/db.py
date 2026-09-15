"""Stratul de baza de date: schema + upsert idempotent.

Schema foloseste format "long" (o observatie pe rand) in loc de o coloana
per metrica. Motivul: metricile au granularitati si dimensiuni diferite
(productia are tip de combustibil, pretul are moneda), iar adaugarea unei
metrici noi nu cere migrarea tabelei.
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
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .config import config

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Observation(Base):
    """O singura masuratoare: (tara, metrica, moment, tip) -> valoare."""

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(8), nullable=False)
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    # psr_type = tipul de resursa (ex. eolian, solar, gaz). Gol pentru consum/pret.
    # 64 caractere: cea mai lunga denumire returnata de API este
    # "Hydro Run-of-river and poundage" (31), deci lasam margine.
    psr_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )

    __table_args__ = (
        # Cheia naturala: rularea de doua ori pe acelasi interval nu duplica date.
        UniqueConstraint("country", "metric", "psr_type", "ts", name="uq_observation"),
        Index("ix_obs_lookup", "country", "metric", "ts"),
    )


def get_engine():
    return create_engine(config.database_url, future=True)


def init_db(engine=None) -> None:
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    logger.info("Schema bazei de date verificata/creata")


def last_timestamp(session: Session, country: str, metric: str) -> dt.datetime | None:
    """Ultimul moment stocat — folosit ca watermark, ca sa nu re-descarcam tot.

    Intotdeauna cu fus orar. PostgreSQL intoarce deja UTC, dar SQLite (folosit
    la demo si in teste) nu pastreaza fusul, iar un datetime naiv nu se poate
    compara cu `now(timezone.utc)` in pipeline.
    """
    stmt = select(func.max(Observation.ts)).where(
        Observation.country == country, Observation.metric == metric
    )
    value = session.execute(stmt).scalar_one_or_none()
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value


# PostgreSQL accepta cel mult 65535 parametri legati intr-un singur statement,
# iar fiecare rand consuma 7 (inclusiv `ingested_at`). Un backfill de 30 de zile la 15 minute, pe ~10
# tipuri de resursa, inseamna ~29.000 de randuri — mult peste limita — deci
# inseram pe transe.
CHUNK_SIZE = 1000


def _insert_ignoring_duplicates(session: Session, rows: list[dict]) -> int:
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(Observation).values(rows)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_observation")
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(Observation).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["country", "metric", "psr_type", "ts"]
        )
    else:
        raise RuntimeError(f"Dialect nesuportat pentru upsert: {dialect}")

    return session.execute(stmt).rowcount or 0


def upsert_observations(session: Session, rows: list[dict]) -> int:
    """Insereaza randurile, ignorand duplicatele pe cheia naturala.

    Returneaza numarul de randuri nou inserate. Toate transele intra in
    aceeasi tranzactie: ori se scriu toate, ori niciuna.
    """
    if not rows:
        return 0

    inserted = 0
    for start in range(0, len(rows), CHUNK_SIZE):
        inserted += _insert_ignoring_duplicates(session, rows[start : start + CHUNK_SIZE])
    session.commit()
    return inserted
