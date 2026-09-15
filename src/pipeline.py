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


# Cat re-cerem din trecut la metricile publicate in avans. Upsert-ul face
# re-cererea gratuita: duplicatele sunt ignorate.
FORWARD_LOOKBACK = dt.timedelta(days=1)

# Sub acest prag consideram ca nu e nimic nou de cerut. ENTSO-E livreaza la
# 15 minute, deci pragul orar de dinainte ar fi sarit peste date valide.
MIN_WINDOW = dt.timedelta(minutes=15)


def resolve_window(
    session: Session, country: str, metric: str
) -> tuple[dt.datetime, dt.datetime]:
    """De unde pana unde descarcam.

    Daca avem deja date, pornim de la ultimul moment stocat (incremental).
    Daca nu, facem backfill pe BACKFILL_DAYS zile.

    Exceptie: preturile day-ahead sunt publicate pentru ziua urmatoare, deci
    watermark-ul lor e deja in viitor. Daca am porni de la el, fereastra ar
    iesi negativa si pipeline-ul ar sari peste metrica la fiecare rulare, fara
    sa mai preia vreodata publicarile noi. Pentru ele cerem explicit si ziua
    urmatoare, pornind dintr-un trecut apropiat.
    """
    now = dt.datetime.now(dt.timezone.utc)
    forward = metric in FORWARD_LOOKING
    end = now + dt.timedelta(days=1) if forward else now

    watermark = last_timestamp(session, country, metric)
    if watermark is None:
        start = now - dt.timedelta(days=config.backfill_days)
        logger.info(
            "%s/%s: tabela goala, backfill %d zile", country, metric, config.backfill_days
        )
    elif forward:
        start = min(watermark, now) - FORWARD_LOOKBACK
        logger.info(
            "%s/%s: metrica publicata in avans, re-cer de la %s pana la %s",
            country, metric, start, end,
        )
    else:
        start = watermark
        logger.info("%s/%s: rulare incrementala de la %s", country, metric, start)
    return start, end


def run() -> int:
    engine = get_engine()
    init_db(engine)

    client = get_client()
    total_inserted = 0
    failures = 0

    with Session(engine) as session:
        for country in config.countries:
            for metric in METRICS:
                try:
                    start, end = resolve_window(session, country, metric)
                    if end - start < MIN_WINDOW:
                        logger.info("%s/%s: date la zi, sar peste", country, metric)
                        continue

                    df = fetch_metric(client, metric, country, start, end)
                    rows = normalize(
                        df, country=country, metric=metric, unit=UNITS[metric]
                    )
                    quality_report(rows)

                    inserted = upsert_observations(session, rows)
                    total_inserted += inserted
                    logger.info(
                        "%s/%s: %d randuri noi (din %d primite)",
                        country,
                        metric,
                        inserted,
                        len(rows),
                    )
                except Exception:
                    # O metrica picata nu opreste restul pipeline-ului.
                    failures += 1
                    logger.exception("%s/%s a esuat", country, metric)

    logger.info("Gata. Randuri noi: %d. Metrici esuate: %d", total_inserted, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
