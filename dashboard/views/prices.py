"""Prices and renewables: price by renewable share, by season, and negative prices."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis import (
    negative_price_hours_by_month,
    price_by_renewable_share,
    renewable_share,
    seasonal_price_link,
)

from .. import cache
from ..charts import fmt_price, fmt_share, negative_hours_figure, price_by_share_figure
from ..findings import bucket_note
from . import MONTHLY_NOTE, Selection, chart, no_data


def _season_table(link: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Season": link.index,
            "Average price": [fmt_price(v) for v in link["mean_price"]],
            "Renewable share": [fmt_share(v) for v in link["mean_renewable_share"]],
            "Correlation": ["—" if pd.isna(v) else f"{v:+.2f}".replace("-", "−") for v in link["correlation"]],
            "Intervals": [f"{n:,}" for n in link["intervals"]],
        }
    )


def render(selection: Selection) -> None:
    price = cache.frame("price_day_ahead", selection.country, selection.start, selection.end)
    gen = cache.frame("generation_actual", selection.country, selection.start, selection.end)
    if price.empty or gen.empty:
        no_data(selection)
        return

    st.subheader("Average price by share of renewables")
    table, _ = price_by_renewable_share(price, renewable_share(gen))
    chart(price_by_share_figure(table))
    st.caption(bucket_note(table))

    st.subheader("Price and renewables by season")
    st.dataframe(_season_table(seasonal_price_link(price, gen)), hide_index=True, width="content")
    st.caption("Correlation describes how the two move together, not what causes what.")

    st.subheader("Hours with a negative price, by month")
    hours = negative_price_hours_by_month(price)
    if hours.empty:
        st.info(MONTHLY_NOTE)
    else:
        chart(negative_hours_figure(hours))
