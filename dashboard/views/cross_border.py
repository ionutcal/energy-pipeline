"""Cross-border: day-ahead prices of every country in the extract, against Romania."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis import daily_price_by_country, local_tz, price_comparison

from .. import cache
from ..charts import daily_price_by_country_figure, fmt_price, fmt_share
from ..findings import COUNTRY_NAMES
from . import Selection, chart

REFERENCE = "RO"


def render(selection: Selection) -> None:
    countries = cache.metadata()["countries"]
    reference = REFERENCE if REFERENCE in countries else countries[0]
    order = [reference] + [c for c in countries if c != reference]
    # One time zone for every country, so they are compared over the same instants.
    tz = local_tz(reference)
    prices = {c: cache.frame("price_day_ahead", c, selection.start, selection.end, tz) for c in order}
    prices = {c: df for c, df in prices.items() if not df.empty}

    st.caption(f"Every country in the data, compared with {COUNTRY_NAMES.get(reference, reference)}. "
               f"Days are read in {tz} time; the country filter doesn't apply here.")
    if len(prices) < 2 or reference not in prices:
        st.info("The comparison needs prices for at least two countries in the selected period.")
        return

    st.subheader("Daily average day-ahead price")
    chart(daily_price_by_country_figure(daily_price_by_country(prices, tz)))

    st.subheader(f"Compared with {COUNTRY_NAMES.get(reference, reference)}")
    table = price_comparison(prices, reference)
    st.dataframe(
        pd.DataFrame(
            {
                "Country": [COUNTRY_NAMES.get(c, c) for c in table.index],
                "Average price": [fmt_price(v) for v in table["mean_price"]],
                "Average difference": [fmt_price(v) for v in table["mean_abs_spread"]],
                "Same price": [fmt_share(v) for v in table["same_price_share"]],
            }
        ),
        hide_index=True,
        width="content",
    )
    st.caption("Average difference is the mean absolute gap to the reference price. "
               "Same price counts intervals where both prices match to the cent.")
