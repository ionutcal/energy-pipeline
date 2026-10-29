"""Colours shared by the report's PNG charts and the dashboard.

The categorical colours are the reference palette, in its order validated for
colour vision deficiency on adjacent pairs; the order follows
`analysis.GROUP_ORDER`, which is also the stacking order in charts.
"""

from .analysis import GROUP_ORDER

GROUP_COLORS = dict(
    zip(GROUP_ORDER, ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"))
)
COLOR_MAIN = "#2a78d6"
COLOR_SURPLUS, COLOR_DEFICIT = "#2a78d6", "#e34948"
INK_MUTED, GRID = "#898781", "#e1e0d9"
# One color per country in the comparison chart, in config order. Beyond this
# many countries the lines stop being distinguishable, so the chart is capped.
COUNTRY_COLORS = ("#2a78d6", "#eb6834", "#1baf7a")
