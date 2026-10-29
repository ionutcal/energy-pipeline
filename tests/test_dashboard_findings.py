"""Tests for the overview figures and the findings sentences generated from them."""

import math

import pandas as pd
import pytest

from dashboard.charts import fmt_hours, fmt_price, fmt_share
from dashboard.findings import Kpis, bucket_note, compute_kpis, findings

TZ = "Europe/Bucharest"
MIX = pd.Series({"Hydro": 0.274, "Gas": 0.205, "Solar": 0.086})


def _frame(values, psr_type="", start="2026-07-01 00:00"):
    ts = pd.date_range(start, periods=len(values), freq="15min", tz=TZ, name="ts")
    return pd.DataFrame({"value": values, "psr_type": psr_type}, index=ts)


def _empty():
    return pd.DataFrame(
        {"value": pd.Series(dtype=float), "psr_type": pd.Series(dtype=str)},
        index=pd.DatetimeIndex([], tz=TZ, name="ts"),
    )


def _link(correlations: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"correlation": correlations})


KPIS = Kpis(mean_price=121.3, renewable_share=0.501, deficit_share=0.68, negative_price_hours=173.25)


def test_compute_kpis_from_frames():
    gen = pd.concat([_frame([300.0, 300.0], "B16"), _frame([700.0, 900.0], "B04")])
    price = _frame([100.0, -20.0])
    load = _frame([900.0, 1300.0])
    kpis = compute_kpis(load, price, gen)
    assert kpis.mean_price == pytest.approx(40.0)
    assert kpis.renewable_share == pytest.approx(600 / 2200)
    assert kpis.deficit_share == pytest.approx(0.5)
    assert kpis.negative_price_hours == 0.25


def test_findings_use_the_computed_numbers():
    text = findings("RO", KPIS, MIX, _link({"Summer": -0.41}))
    assert text[0] == f"Renewables supplied {fmt_share(0.501)} of the electricity generated in Romania over this period."
    assert text[1] == f"The largest source was Hydro, at {fmt_share(0.274)}."
    assert text[2] == f"The day-ahead price averaged {fmt_price(121.3)} and was negative for {fmt_hours(173.25)}."
    assert text[3] == f"Generation was below load {fmt_share(0.68)} of the time."


def test_findings_say_never_negative_when_no_negative_hours():
    kpis = Kpis(mean_price=121.3, renewable_share=0.5, deficit_share=0.6, negative_price_hours=0.0)
    assert any(s.endswith("and never went negative.") for s in findings("RO", kpis, MIX, _link({})))


def test_missing_load_omits_the_deficit_sentence():
    gen = _frame([500.0, 500.0], "B04")
    kpis = compute_kpis(_empty(), _frame([100.0, 90.0]), gen)
    assert math.isnan(kpis.deficit_share)
    assert not any(s.startswith("Generation was below load") for s in findings("RO", kpis, MIX, _link({})))


def test_missing_prices_give_no_price_figures():
    kpis = compute_kpis(_frame([900.0]), _empty(), _frame([1000.0], "B04"))
    assert math.isnan(kpis.mean_price) and math.isnan(kpis.negative_price_hours)
    assert not any(s.startswith("The day-ahead price") for s in findings("RO", kpis, MIX, _link({})))


def test_seasonal_sentence_needs_two_seasons():
    one = findings("RO", KPIS, MIX, _link({"Summer": -0.41}))
    assert not any("varies by season" in s for s in one)

    two = findings("RO", KPIS, MIX, _link({"Summer": -0.41, "Autumn": 0.09, "Winter": math.nan}))
    assert two[-1] == "The link between renewables and price varies by season: from −0.41 in summer to +0.09 in autumn."


def test_unknown_country_is_named_by_its_code():
    assert "generated in XX over" in findings("XX", KPIS, MIX, _link({}))[0]


def test_no_finding_claims_a_cause():
    text = " ".join(findings("RO", KPIS, MIX, _link({"Summer": -0.41, "Autumn": 0.09}))).lower()
    for word in ("because", "caused", "due to", "drives"):
        assert word not in text


def _buckets(counts: dict[str, int]) -> pd.DataFrame:
    return pd.DataFrame({"mean_price": 100.0, "intervals": list(counts.values())}, index=list(counts))


def test_bucket_note_names_the_small_buckets():
    # Romania over a year: the top two buckets are tiny.
    table = _buckets({"40–50%": 11580, "50–60%": 9463, "80–90%": 292, "90–100%": 12})
    assert bucket_note(table) == (
        "Hover a bar for the number of intervals behind it. "
        "The 80–90% and 90–100% buckets each hold under 2% of them."
    )


def test_bucket_note_names_a_single_small_bucket():
    table = _buckets({"0–10%": 13029, "10–20%": 8733, "80–90%": 100})
    assert bucket_note(table).endswith("The 80–90% bucket holds under 2% of them.")


def test_bucket_note_without_small_buckets_only_explains_the_hover():
    # Hungary's lowest bucket is its largest: no claim about the ends.
    table = _buckets({"0–10%": 13029, "10–20%": 8733, "20–30%": 2389})
    assert bucket_note(table) == "Hover a bar for the number of intervals behind it."
