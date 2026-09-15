"""Accesul la API-ul ENTSO-E, cu reincercari.

Semnaturile folosite mai jos au fost verificate pe pachetul python-entsoe 0.6.1:
    client.load.actual(start, end, country)
    client.prices.day_ahead(start, end, country)
    client.generation.actual(start, end, country, psr_type=None)
Daca actualizezi pachetul, reverifica-le inainte sa te bazezi pe ele.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Callable

import pandas as pd

from .config import config

logger = logging.getLogger(__name__)

METRICS = ("load_actual", "price_day_ahead", "generation_actual")

# Unitatea reala vine din raspunsul API (vezi transform.unit_from_frame);
# valorile de aici sunt doar plasa de siguranta daca lipseste din raspuns.
UNITS = {
    "load_actual": "MW",
    "price_day_ahead": "EUR/MWh",
    "generation_actual": "MW",
}

# Preturile day-ahead sunt publicate in avans (pentru ziua urmatoare), deci
# ultimul moment stocat ajunge in viitor. Pentru metricile de aici nu pornim
# de la watermark — vezi pipeline.resolve_window.
FORWARD_LOOKING = frozenset({"price_day_ahead"})


def get_client():
    """Creeaza clientul ENTSO-E. Importul e local ca testele sa nu ceara tokenul."""
    from entsoe import Client

    if not config.api_key:
        raise RuntimeError(
            "ENTSOE_API_KEY lipseste. Inregistreaza-te pe transparency.entsoe.eu "
            "si cere token pe email la transparency@entsoe.eu."
        )
    return Client(api_key=config.api_key)


def with_retry(fn: Callable, *args, **kwargs):
    """Reincearca la erori de retea, cu backoff exponential."""
    last_error = None
    for attempt in range(1, config.max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # API-ul poate da 4xx/5xx sau timeout
            last_error = exc
            wait = config.retry_backoff_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Incercarea %d/%d a esuat (%s). Reincerc in %.1fs",
                attempt,
                config.max_retries,
                exc,
                wait,
            )
            if attempt < config.max_retries:
                time.sleep(wait)
    raise RuntimeError(f"Apel esuat dupa {config.max_retries} incercari") from last_error


def fetch_metric(
    client, metric: str, country: str, start: dt.datetime, end: dt.datetime
) -> pd.DataFrame:
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)

    if metric == "load_actual":
        call = client.load.actual
    elif metric == "price_day_ahead":
        call = client.prices.day_ahead
    elif metric == "generation_actual":
        call = client.generation.actual
    else:
        raise ValueError(f"Metrica necunoscuta: {metric}")

    logger.info("Descarc %s pentru %s: %s -> %s", metric, country, start_ts, end_ts)
    return with_retry(call, start_ts, end_ts, country=country)
