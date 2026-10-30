# Dashboard — design

Status: implemented · 2026-10-29 · live at <https://energy-pipeline.streamlit.app/>

## Goal

An interactive dashboard that presents the project's findings to someone who
won't read the code or run anything: typically a recruiter or interviewer
spending two to five minutes on it. It is published at a public link that can
go on a CV, and can also be run locally for a live demo.

Success means:

- the link opens in a browser with no setup, and the first screen answers
  "what did this project find?";
- every figure matches what `python -m src.report` produces on the same data,
  because both use the same code;
- the dashboard reads as a sober analysis tool, consistent with the rest of
  the repository, not as a generated demo.

## Scope

In scope: the four views below, filtered by country and period; a data
extract that can be committed and deployed; deployment to Streamlit Community
Cloud.

Out of scope: forecasting, live or scheduled data refresh inside the app,
user accounts, data download or export. The extract is refreshed by hand.

## Design principles

**Visual.** No emoji, gradients, animations or promotional copy. Plain,
report-style titles. The same colour per energy source as the README charts
(`src/style.py`). Every axis carries its unit; numbers use one format
throughout (`121.3 EUR/MWh`, `50.1%`). Figures that are easy to misread carry
a short method note next to them: small samples in a bucket, correlation
versus cause, load and generation not covering the same installations. The
data source, the period covered and the extract date are always visible.

**Code.** Small modules with one job each; views only lay out components.
Calculations are never reimplemented in the dashboard. Dependencies have
version bounds. New code is tested like the rest of the project.

## Views

A sidebar holds two filters used everywhere: **country** (RO, HU, BG; default
RO) and **period** (a date range in the country's local time, inclusive). The
default period runs from the first to the last day with generation data, so
that every figure covers the same days: day-ahead prices extend one day
further, into tomorrow, and would otherwise be averaged over a longer span.
The sidebar also links to the GitHub repository and states the data source.

### 1. Overview

- Four figures for the selected country and period:
  - *Average day-ahead price* — mean of the 15-minute prices, EUR/MWh.
  - *Renewable share* — renewable generation divided by total generation,
    summed over the period (energy-weighted, not a mean of shares).
  - *Time in deficit* — share of intervals where generation is below load.
  - *Negative-price hours* — number of 15-minute intervals with a price
    below zero, divided by four.
- Renewable share and average price per month, as two stacked panels sharing
  the month axis (no second y-axis).
- A few sentences of findings (up to five), **generated from the computed values**
  for the current selection, so the text never contradicts the numbers when
  the filters change.

### 2. Generation mix

- Share of each source per month, as 100% stacked bars.
- Average hourly generation profile, stacked by source, with a selector for
  the season (only seasons present in the selection are offered).
- Table: each source's share of the energy generated over the period.

### 3. Prices and renewables

- Average price per renewable-share bucket. Hovering shows the number of
  intervals; a note points out that the outer buckets are small samples.
- Table per season: mean price, mean renewable share, correlation, number of
  intervals. A note states that correlation is not causation.
- Negative-price hours per month.

### 4. Cross-border

Ignores the country filter and always shows every country in the extract,
with Romania as the reference. The period is read in Romanian local time, so
all countries are compared over the same instants.

- Daily average price, one line per country.
- Table: mean price, mean absolute difference from Romania, share of
  intervals at exactly the same price.

## Data

```
energy_1y.db ──> python -m src.export ──> dashboard/data/observations.parquet
                                          dashboard/data/metadata.json
                                                    │
                                                    ▼
                                   DuckDB: SQL over Parquet, filtered
                                                    │
                                                    ▼
                                   src/analysis.py ──> Plotly ──> Streamlit
```

**Extract.** `python -m src.export` reads the database configured as for the
pipeline (`DATABASE_URL`) and writes one Parquet file with the same long
format as the `observations` table: `country`, `metric`, `psr_type`, `ts`
(UTC), `value`, `unit`. It keeps the original 15-minute resolution, so
correlations and negative-price counts match the report exactly.

Size budget: about 1.1 million rows, expected at 5–10 MB with dictionary
encoding and zstd compression. If the file exceeds 25 MB, generation is
aggregated to hourly means while load and prices stay at 15 minutes; the
README then states this difference.

`metadata.json` records the countries, the first and last timestamp per
metric, the row count and the export time. The dashboard shows the period and
export date from it.

**Reading.** `dashboard/data.py` queries the Parquet file with DuckDB, reading
only the selected country and period, and caches each result
(`st.cache_data`). It returns DataFrames in the shape `src/analysis.py`
already expects: indexed by local time, with `value` and `psr_type` columns.

## Code structure

```
src/
  analysis.py      calculations moved from report.py, plus the new figures
  style.py         source groups' order and colours, shared by report and dashboard
  export.py        database -> Parquet + metadata.json
  report.py        console report and PNG charts only
dashboard/
  app.py           entry point: page setup, sidebar filters, tab navigation
  data.py          DuckDB queries, caching, conversion to analysis frames
  charts.py        Plotly figure builders and the shared chart theme
  views/           one module per tab: overview, mix, prices, cross_border
  data/            observations.parquet, metadata.json
.streamlit/config.toml   neutral theme
```

`report.py` keeps importing the moved functions, so it and its existing tests
behave exactly as before. New figures (period-level renewable share,
negative-price hours, monthly negative-price hours, time in deficit) are added
to `src/analysis.py` with tests.

New dependencies: `streamlit`, `plotly`, `duckdb`, `pyarrow`, added to
`requirements.txt` with version bounds.

## Errors and edge cases

- **Extract missing:** the app shows how to create it (`python -m
  src.export`) instead of a stack trace.
- **Period with no data** for the selected country: an explanatory note
  instead of empty charts.
- **Period shorter than a complete month:** monthly charts are replaced by a
  note asking for at least one full month; other charts still render.
- **Season with no data** in the selection: not offered in the selector.

## Testing

- **Refactor:** the existing tests must pass unchanged after the move to
  `src/analysis.py`.
- **New analysis figures:** unit tests on small synthetic frames, as for the
  existing analyses.
- **Export:** a small SQLite database is exported and read back; rows, UTC
  timestamps and metadata are checked.
- **Data layer:** filtering by country and period returns exactly the
  expected rows, in the expected shape.
- **Charts:** each builder returns a figure with the expected series and
  axis units.
- **App smoke test:** Streamlit's `AppTest` runs the app on a small fixture
  extract and checks that every tab renders without an exception, including
  the empty-period case.
- **Visual check:** the app is run locally and every tab is reviewed in a
  browser before the pull request.

CI runs the new tests along with the existing ones; none of them needs a
database server.

## Deployment

Streamlit Community Cloud, from the GitHub repository, main file
`dashboard/app.py`, Python 3.12. The repository owner signs in with GitHub and
creates the app; the public URL is then added to the top of the README. Every
push to `main` redeploys it.

## Delivery

On a branch, through a pull request, merged only on the owner's approval.

## Risks

- **Streamlit Community Cloud limits** (about 1 GB of memory): the full
  extract is roughly 1.1 million rows; queries read only the selected
  country and period, which keeps memory well below the limit.
- **Idle apps sleep** on the free tier and take a few seconds to wake. This
  is acceptable for a portfolio link; the README notes it.
- **Repository size:** the extract is committed. Each refresh adds a new
  version of the file to git history, so refreshes should be occasional.
