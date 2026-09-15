"""Access to the ENTSO-E API, with retries.

The signatures used below were verified against python-entsoe 0.6.1:
    client.load.actual(start, end, country)
    client.prices.day_ahead(start, end, country)
    client.generation.actual(start, end, country, psr_type=None)
If you upgrade the package, re-check them before relying on them.
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

# The real unit comes from the API response (see transform.unit_from_frame);
# these values are only a fallback for when the response lacks one.
UNITS = {
    "load_actual": "MW",
    "price_day_ahead": "EUR/MWh",
    "generation_actual": "MW",
}

# Day-ahead prices are published in advance (for the next day), so the last
# stored timestamp ends up in the future. Metrics listed here don't start
# from the watermark — see pipeline.resolve_window.
FORWARD_LOOKING = frozenset({"price_day_ahead"})


def get_client():
    """Create the ENTSO-E client. The import is local so tests don't need a token."""
    from entsoe import Client

    if not config.api_key:
        raise RuntimeError(
            "ENTSOE_API_KEY is missing. Register at transparency.entsoe.eu "
            "and request a token by emailing transparency@entsoe.eu."
        )
    return Client(api_key=config.api_key)


def with_retry(fn: Callable, *args, **kwargs):
    """Retry on network errors, with exponential backoff."""
    last_error = None
    for attempt in range(1, config.max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # the API can return 4xx/5xx or time out
            last_error = exc
            wait = config.retry_backoff_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Attempt %d/%d failed (%s). Retrying in %.1fs",
                attempt,
                config.max_retries,
                exc,
                wait,
            )
            if attempt < config.max_retries:
                time.sleep(wait)
    raise RuntimeError(f"Call failed after {config.max_retries} attempts") from last_error


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
        raise ValueError(f"Unknown metric: {metric}")

    logger.info("Fetching %s for %s: %s -> %s", metric, country, start_ts, end_ts)
    return with_retry(call, start_ts, end_ts, country=country)
