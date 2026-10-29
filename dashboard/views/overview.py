"""Overview: headline figures, generated findings, and the monthly trend."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis import complete_months, energy_mix, monthly_summary, seasonal_price_link

from .. import cache
from ..charts import fmt_hours, fmt_price, fmt_share, monthly_summary_figure
from ..findings import compute_kpis, findings
from . import MONTHLY_NOTE, Selection, chart, no_data


def render(selection: Selection) -> None:
    load, price, gen = (
        cache.frame(metric, selection.country, selection.start, selection.end)
        for metric in ("load_actual", "price_day_ahead", "generation_actual")
    )
    if load.empty and price.empty and gen.empty:
        no_data(selection)
        return

    kpis = compute_kpis(load, price, gen)
    columns = st.columns(4)
    columns[0].metric("Average day-ahead price", fmt_price(kpis.mean_price),
                      help="Mean of the 15-minute day-ahead prices in the period.")
    columns[1].metric("Renewable share", fmt_share(kpis.renewable_share),
                      help="Renewable generation divided by total generation, summed over the period.")
    columns[2].metric("Time in deficit", fmt_share(kpis.deficit_share),
                      help="Share of intervals in which generation was below load.")
    columns[3].metric("Negative-price hours", fmt_hours(kpis.negative_price_hours),
                      help="Quarter hours with a price below zero, divided by four.")
    st.caption("Reported load and generation don't cover exactly the same installations.")

    st.subheader("What the data shows")
    mix = energy_mix(gen) if not gen.empty else pd.Series(dtype=float)
    link = seasonal_price_link(price, gen) if not (price.empty or gen.empty) else pd.DataFrame()
    st.markdown(" ".join(findings(selection.country, kpis, mix, link)))

    st.subheader("Renewable share and price by month")
    if gen.empty or complete_months(pd.DatetimeIndex(gen.index.unique())).empty:
        st.info(MONTHLY_NOTE)
    else:
        chart(monthly_summary_figure(monthly_summary(gen, price)))
