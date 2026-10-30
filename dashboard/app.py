"""Interactive dashboard for the ENTSO-E data.

Run:  streamlit run dashboard/app.py

Reads the Parquet extract in dashboard/data (see `python -m src.export`);
DASHBOARD_DATA_DIR points it at another directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run` puts this file's directory on the path, not the repository's.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import datetime as dt  # noqa: E402

import streamlit as st  # noqa: E402

from dashboard import cache  # noqa: E402
from dashboard.charts import country_name, fmt_day  # noqa: E402
from dashboard.data import ExtractMissing, default_period  # noqa: E402
from dashboard.views import Selection, cross_border, mix, overview, prices  # noqa: E402

REPO_URL = "https://github.com/ionutcal/energy-pipeline"
TABS = {
    "Overview": overview,
    "Generation mix": mix,
    "Prices and renewables": prices,
    "Cross-border": cross_border,
}


def _selectable_days(meta: dict) -> tuple[dt.date, dt.date]:
    """The widest range of days covered by any country's default period."""
    days = [day for country in meta["coverage"] for day in default_period(meta, country)]
    return min(days), max(days)


def _sidebar(meta: dict) -> tuple[str, tuple]:
    countries = meta["countries"]
    country = st.selectbox(
        "Country",
        countries,
        index=countries.index("RO") if "RO" in countries else 0,
        format_func=country_name,
    )
    first, last = default_period(meta, country)
    low, high = _selectable_days(meta)
    period = st.date_input("Period", value=(first, last), min_value=low, max_value=high, format="YYYY-MM-DD")

    exported = dt.datetime.fromisoformat(meta["exported_at"]).date()
    st.caption(
        f"Data: {meta['source']}, {fmt_day(first)} – {fmt_day(last)} (extract of {fmt_day(exported)})"
    )
    st.markdown(f"[GitHub repository]({REPO_URL})")
    return country, period


def main() -> None:
    st.set_page_config(page_title="Energy Data Pipeline — ENTSO-E", layout="wide")
    try:
        meta = cache.metadata()
    except ExtractMissing as exc:
        st.error(str(exc))
        st.stop()

    st.title("Energy Data Pipeline")
    st.caption(
        "Electricity load, generation by source and day-ahead prices from the ENTSO-E "
        "Transparency Platform, at 15-minute resolution."
    )
    with st.sidebar:
        country, period = _sidebar(meta)
    if len(period) != 2:
        st.info("Select a start and an end date for the period.")
        st.stop()

    selection = Selection(country, *period)
    st.caption(f"{selection.name} · {fmt_day(selection.start)} – {fmt_day(selection.end)}")
    for tab, view in zip(st.tabs(list(TABS)), TABS.values()):
        with tab:
            view.render(selection)


main()
