"""Orchestrarea pipeline-ului: determina intervalul, descarca, curata, incarca.

Rulare:  python -m src.pipeline
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


# Cat re-cerem inapoi de la ultimul moment stocat. ENTSO-E publica unele
# serii cu intarziere si revizuieste date deja publicate; upsert-ul le
# suprascrie, deci re-cererea nu duplica nimic.
LOOKBACK = dt.timedelta(days=1)


def resolve_window(
    session: Session, country: str, metric: str
) -> tuple[dt.datetime, dt.datetime]:
    """De unde pana unde descarcam.

    Daca avem deja date, pornim cu o zi inaintea ultimului moment stocat
    (incremental, cu suprapunere). Daca nu, facem backfill pe BACKFILL_DAYS.

    Preturile day-ahead sunt publicate pentru ziua urmatoare, deci pentru ele
    cerem explicit si ziua de maine, iar watermark-ul lor e deja in viitor —
    de aceea il limitam la `now`, altfel fereastra ar iesi negativa si
    metrica ar fi sarita la fiecare rulare.
    """
    now = dt.datetime.now(dt.timezone.utc)
    end = now + dt.timedelta(days=1) if metric in FORWARD_LOOKING else now

    watermark = last_timestamp(session, country, metric)
    if watermark is None:
        start = now - dt.timedelta(days=config.backfill_days)
        logger.info(
            "%s/%s: tabela goala, backfill %d zile", country, metric, config.backfill_days
        )
    else:
        start = min(watermark, now) - LOOKBACK
        logger.info("%s/%s: rulare incrementala %s -> %s", country, metric, start, end)
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
                        "%s/%s: %d randuri noi sau modificate (din %d primite)",
                        country,
                        metric,
                        written,
                        len(rows),
                    )
                except Exception:
                    # O metrica picata nu opreste restul pipeline-ului.
                    failures += 1
                    logger.exception("%s/%s a esuat", country, metric)

    logger.info("Gata. Randuri scrise: %d. Metrici esuate: %d", total_written, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
