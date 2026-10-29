"""Cached access to the extract for the running app.

The data directory is part of every cache key, so pointing the app at another
extract (as the tests do with DASHBOARD_DATA_DIR) never serves stale results.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from . import data


def data_dir() -> Path:
    return Path(os.environ.get("DASHBOARD_DATA_DIR", data.DEFAULT_DATA_DIR))


@st.cache_data(show_spinner=False)
def _metadata(directory: str) -> dict:
    return data.load_metadata(Path(directory))


@st.cache_data(show_spinner=False)
def _frame(directory: str, metric: str, country: str, start: dt.date, end: dt.date, tz: str | None) -> pd.DataFrame:
    return data.load_metric(Path(directory), metric, country, start, end, tz)


def metadata() -> dict:
    return _metadata(str(data_dir()))


def frame(metric: str, country: str, start: dt.date, end: dt.date, tz: str | None = None) -> pd.DataFrame:
    return _frame(str(data_dir()), metric, country, start, end, tz)
