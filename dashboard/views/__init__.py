"""One module per dashboard tab. Views lay out components; they don't calculate."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import plotly.graph_objects as go
import streamlit as st

from ..charts import country_name

MONTHLY_NOTE = "Monthly figures need at least one complete month in the selected period."


@dataclass(frozen=True)
class Selection:
    country: str
    start: dt.date
    end: dt.date

    @property
    def name(self) -> str:
        return country_name(self.country)


def no_data(selection: Selection) -> None:
    st.info(f"No data for {selection.name} in the selected period.")


def no_generation(selection: Selection) -> None:
    st.info(f"No generation data for {selection.name} in the selected period.")


def chart(fig: go.Figure) -> None:
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
