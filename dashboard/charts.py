"""Plotly figures for the dashboard, and the number formats used around them.

Each builder takes the output of one function in `src.analysis` and returns a
figure without a title: the view writes the title above it, so text and
charts share one typographic style. Colours come from `src.style`, the same
as the report's PNG charts.
"""

from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.style import COLOR_MAIN, COUNTRY_COLORS, GRID, GROUP_COLORS, INK_MUTED

FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
INK = "#0b0b0b"
MONTH_FORMAT = "%b %Y"


# --- number formats ----------------------------------------------------------


def _missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def fmt_day(day: dt.date) -> str:
    """A date as people write it: 4 May 2026."""
    return f"{day.day} {day:%b %Y}"


def fmt_number(value: float | None) -> str:
    """One decimal, for places where the unit is already in the label."""
    return "—" if _missing(value) else f"{value:,.1f}"


def fmt_price(value: float | None) -> str:
    return "—" if _missing(value) else f"{fmt_number(value)} EUR/MWh"


def fmt_share(value: float | None) -> str:
    """A share given as 0..1, shown as a percentage."""
    return "—" if _missing(value) else f"{value * 100:.1f}%"


def fmt_hours(value: float | None) -> str:
    """Whole hours from ten up; below that, quarter-hour precision matters."""
    if _missing(value):
        return "—"
    if value >= 10:
        return f"{value:,.0f} h"
    return f"{value:.2f}".rstrip("0").rstrip(".") + " h"


# --- theme -------------------------------------------------------------------


def _theme(fig: go.Figure, y_title: str, *, x_title: str = "", height: int = 380) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(family=FONT, size=13, color=INK),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title_text=""),
        hoverlabel=dict(font_family=FONT),
    )
    fig.update_xaxes(showgrid=False, linecolor=INK_MUTED, ticks="outside", tickcolor=INK_MUTED)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=INK_MUTED)
    fig.update_xaxes(title_text=x_title)
    fig.update_yaxes(title_text=y_title)
    return fig


# --- figures -----------------------------------------------------------------


def monthly_summary_figure(summary: pd.DataFrame) -> go.Figure:
    """Renewable share and average price per month, in two panels sharing the months.

    Two panels rather than a second y-axis: the scales are unrelated, and
    aligning them would suggest a relationship the chart can't show.
    """
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1)
    fig.add_trace(
        go.Scatter(
            x=summary.index, y=summary["renewable_share"] * 100, name="Renewable share",
            mode="lines+markers", line=dict(color=GROUP_COLORS["Wind"], width=2),
            hovertemplate="%{x|%b %Y}: %{y:.1f}%<extra></extra>",
        ),
        row=1, col=1,
    )
    if "mean_price" in summary:
        fig.add_trace(
            go.Scatter(
                x=summary.index, y=summary["mean_price"], name="Average price",
                mode="lines+markers", line=dict(color=COLOR_MAIN, width=2),
                hovertemplate="%{x|%b %Y}: %{y:.1f} EUR/MWh<extra></extra>",
            ),
            row=2, col=1,
        )
    _theme(fig, "Renewable share (%)", height=480)
    fig.update_yaxes(title_text="Day-ahead price (EUR/MWh)", row=2, col=1)
    fig.update_xaxes(tickformat=MONTH_FORMAT, dtick="M1")
    fig.update_layout(showlegend=False)
    return fig


def monthly_mix_figure(mix: pd.DataFrame) -> go.Figure:
    """Each source's share of the energy generated per month, as 100% stacked bars."""
    fig = go.Figure(
        [
            go.Bar(
                x=mix.index, y=mix[group] * 100, name=group,
                marker=dict(color=GROUP_COLORS[group], line=dict(color="white", width=1)),
                hovertemplate=f"{group}, %{{x|%b %Y}}: %{{y:.1f}}%<extra></extra>",
            )
            for group in mix.columns
        ]
    )
    fig.update_layout(barmode="stack", bargap=0.25)
    _theme(fig, "Share of generation (%)")
    fig.update_yaxes(range=[0, 100])
    fig.update_xaxes(tickformat=MONTH_FORMAT, dtick="M1")
    return fig


def hourly_mix_figure(profile: pd.DataFrame) -> go.Figure:
    """Average generation by hour of day, stacked by source."""
    fig = go.Figure(
        [
            go.Scatter(
                x=profile.index, y=profile[group], name=group, mode="lines", stackgroup="one",
                line=dict(color=GROUP_COLORS[group], width=0.5), fillcolor=GROUP_COLORS[group],
                hovertemplate=f"{group}, %{{x}}:00: %{{y:,.0f}} MW<extra></extra>",
            )
            for group in profile.columns
        ]
    )
    _theme(fig, "Generation (MW)", x_title="Hour (local time)", height=420)
    fig.update_xaxes(dtick=2, range=[0, 23])
    return fig


def price_by_share_figure(table: pd.DataFrame) -> go.Figure:
    """Average price per bucket of renewable share; the hover shows each bucket's size."""
    fig = go.Figure(
        go.Bar(
            x=[str(bucket) for bucket in table.index], y=table["mean_price"],
            customdata=[[int(n)] for n in table["intervals"]],
            marker=dict(color=COLOR_MAIN),
            hovertemplate="%{x}: %{y:.1f} EUR/MWh<br>%{customdata[0]:,} intervals<extra></extra>",
        )
    )
    _theme(fig, "Average day-ahead price (EUR/MWh)", x_title="Share of renewables in generation")
    return fig


def negative_hours_figure(hours: pd.Series) -> go.Figure:
    """Hours with a negative day-ahead price, per month."""
    fig = go.Figure(
        go.Bar(
            x=hours.index, y=hours.to_numpy(), marker=dict(color=COLOR_MAIN),
            customdata=[[fmt_hours(v)] for v in hours],
            hovertemplate="%{x|%b %Y}: %{customdata[0]}<extra></extra>",
        )
    )
    _theme(fig, "Negative-price hours", height=320)
    fig.update_xaxes(tickformat=MONTH_FORMAT, dtick="M1")
    return fig


def daily_price_by_country_figure(daily: pd.DataFrame) -> go.Figure:
    """Daily average day-ahead price, one line per country.

    The first column is the reference country. It is listed first but drawn
    last: coupled markets often clear at the same price, and the reference
    line would otherwise disappear under the others.
    """
    lines = [
        go.Scatter(
            x=daily.index, y=daily[country], name=country, mode="lines", legendrank=rank,
            line=dict(color=color, width=2.2 if rank == 1 else 1.4),
            hovertemplate=f"{country}, %{{x|%d %b %Y}}: %{{y:.1f}} EUR/MWh<extra></extra>",
        )
        for rank, (country, color) in enumerate(zip(daily.columns, COUNTRY_COLORS), start=1)
    ]
    fig = go.Figure(lines[1:] + lines[:1])
    _theme(fig, "Day-ahead price (EUR/MWh)", height=420)
    fig.update_layout(hovermode="x unified")
    return fig
