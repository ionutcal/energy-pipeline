"""Cached access to the extract for the running app.

The data directory is part of every cache key, so pointing the app at another
extract (as the tests do with DASHBOARD_DATA_DIR) never serves stale results.
The cache is shared by every visitor, so it is bounded: a full year of
generation for one country is about 10 MB in memory, and the free host has
about 1 GB.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from src.analysis import local_tz

from . import data

MAX_ENTRIES = 64


def data_dir() -> Path:
    return Path(os.environ.get("DASHBOARD_DATA_DIR", data.DEFAULT_DATA_DIR))


@st.cache_data(show_spinner=False)
def _metadata(directory: str) -> dict:
    return data.load_metadata(Path(directory))


@st.cache_data(show_spinner=False, max_entries=MAX_ENTRIES)
def _frame(directory: str, metric: str, country: str, start: dt.date, end: dt.date, tz: str) -> pd.DataFrame:
    return data.load_metric(Path(directory), metric, country, start, end, tz)


def metadata() -> dict:
    return _metadata(str(data_dir()))


def frame(metric: str, country: str, start: dt.date, end: dt.date, tz: str | None = None) -> pd.DataFrame:
    # Resolve the default zone first, so one read never occupies two entries.
    return _frame(str(data_dir()), metric, country, start, end, tz or local_tz(country))
