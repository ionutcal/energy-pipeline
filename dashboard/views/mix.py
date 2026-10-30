"""Generation mix: by month, by hour of day per season, and over the period."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis import complete_months, energy_mix, monthly_mix, seasonal_hourly_mix

from .. import cache
from ..charts import fmt_share, hourly_mix_figure, monthly_mix_figure
from . import MONTHLY_NOTE, Selection, chart, no_generation


def render(selection: Selection) -> None:
    gen = cache.frame("generation_actual", selection.country, selection.start, selection.end)
    if gen.empty:
        no_generation(selection)
        return

    st.subheader("Generation mix by month")
    if complete_months(pd.DatetimeIndex(gen.index.unique())).empty:
        st.info(MONTHLY_NOTE)
    else:
        chart(monthly_mix_figure(monthly_mix(gen)))

    st.subheader("A typical day, by season")
    profiles = seasonal_hourly_mix(gen)
    season = st.radio("Season", list(profiles), horizontal=True, label_visibility="collapsed")
    chart(hourly_mix_figure(profiles[season]))
    st.caption("Average generation for each hour of the day, over the days of the selected season.")

    st.subheader("Share of the energy generated")
    mix = energy_mix(gen).sort_values(ascending=False)
    st.dataframe(
        pd.DataFrame({"Source": mix.index, "Share": [fmt_share(v) for v in mix]}),
        hide_index=True,
        width="content",
    )
