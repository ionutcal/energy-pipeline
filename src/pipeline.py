"""Pipeline orchestration: determine the window, fetch, clean, load.

Run:  python -m src.pipeline
"""

from __future__ import annotations

import datetime as dt
import logging
import sys

from sqlalchemy.orm import Session

from .config import config
from .db import get_engine, init_db, last_timestamp, upsert_observations
from .fetch import FORWARD_LOOKING, METRICS, UNITS, fetch_metric, get_client
from .transform import normalize, quality_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("pipeline")


# How far back from the last stored timestamp we re-request. ENTSO-E
# publishes some series late and revises published data; the upsert
# overwrites them, so re-requesting doesn't duplicate anything.
LOOKBACK = dt.timedelta(days=1)


def resolve_window(
    session: Session, country: str, metric: str
) -> tuple[dt.datetime, dt.datetime]:
    """Where to fetch from and to.

    If we already have data, start one day before the last stored timestamp
    (incremental, with overlap). Otherwise, backfill BACKFILL_DAYS.

    Day-ahead prices are published for the next day, so for them we
    explicitly request tomorrow too, and their watermark is already in the
    future — that's why we cap it at `now`; otherwise the window would come
    out negative and the metric would be skipped on every run.
    """
    now = dt.datetime.now(dt.timezone.utc)
    end = now + dt.timedelta(days=1) if metric in FORWARD_LOOKING else now

    watermark = last_timestamp(session, country, metric)
    if watermark is None:
        start = now - dt.timedelta(days=config.backfill_days)
        logger.info(
            "%s/%s: empty table, backfilling %d days", country, metric, config.backfill_days
        )
    else:
        start = min(watermark, now) - LOOKBACK
        logger.info("%s/%s: incremental run %s -> %s", country, metric, start, end)
    return start, end


def run() -> int:
    engine = get_engine()
    init_db(engine)

    client = get_client()
    total_written = 0
    failures = 0

    with Session(engine) as session:
        for country in config.countries:
            for metric in METRICS:
                try:
                    start, end = resolve_window(session, country, metric)
                    df = fetch_metric(client, metric, country, start, end)
                    rows = normalize(
                        df, country=country, metric=metric, unit=UNITS[metric]
                    )
                    quality_report(rows)

                    written = upsert_observations(session, rows)
                    total_written += written
                    logger.info(
                        "%s/%s: %d new or modified rows (out of %d received)",
                        country,
                        metric,
                        written,
                        len(rows),
                    )
                except Exception:
                    # One failed metric doesn't stop the rest of the pipeline.
                    failures += 1
                    logger.exception("%s/%s failed", country, metric)

    logger.info("Done. Rows written: %d. Failed metrics: %d", total_written, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
