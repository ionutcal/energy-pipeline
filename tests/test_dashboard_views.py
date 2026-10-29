"""Tests for the tables the views build from the analysis results."""

import pandas as pd

from dashboard.views.cross_border import comparison_table


def test_reference_country_is_not_compared_with_itself():
    table = pd.DataFrame(
        {"mean_price": [121.2, 115.7], "mean_abs_spread": [0.0, 5.8], "same_price_share": [1.0, 0.755]},
        index=["RO", "BG"],
    )
    rows = comparison_table(table, reference="RO")
    assert rows.to_dict("records") == [
        {"Country": "Romania", "Average price": "121.2 EUR/MWh", "Average difference": "reference", "Same price": "reference"},
        {"Country": "Bulgaria", "Average price": "115.7 EUR/MWh", "Average difference": "5.8 EUR/MWh", "Same price": "75.5%"},
    ]
