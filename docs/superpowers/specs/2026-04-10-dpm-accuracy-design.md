# DPM Accuracy Improvements

## Problem

The script has two bugs that cause it to miss or underreport metrics, especially push-based OTel metrics with irregular intervals:

1. **`result[0]` bug**: The DPM query (`count_over_time(metric[5m])/5`) returns one result per series (unique label combination), but the script only takes `result[0]`. This picks an arbitrary series whose DPM may be much lower than the busiest series, or even 0 if that series has no samples in the window. Metrics can be filtered out entirely by `min_dpm`.

2. **5-minute lookback too narrow**: For bursty push metrics (e.g., OTel gateway), a 5-minute window may catch zero samples depending on when the query runs, causing the metric to be missed.

## Changes

### 1. Configurable lookback window

- New CLI flag: `--lookback` / `-l` (integer, minutes, default: 10)
- Default increases from 5 to 10 minutes
- Minimum: 1 minute
- Flows into PromQL as both the range vector and divisor:
  ```promql
  count_over_time(metric{__ignore_usage__=""}[10m])/10
  ```
- Passed through `get_metric_rates` -> `process_metric_chunk`

### 2. Fix result handling: use max across series

The DPM query returns a vector with one entry per series. Instead of taking `result[0]`, iterate all results and:

- **Headline DPM** = max DPM across all series
- **Collect per-series detail**: each series' labels and individual DPM value

The series count query (`count(metric{__ignore_usage__=""})`) is unchanged.

### 3. Per-series breakdown in output

Per-series data is collected during `process_metric_chunk` (no extra queries needed). Each metric's result includes a `series_detail` list.

Data structure per metric:
```python
{
    'dpm': 5.0,                   # max across series
    'series_count': '4',
    'series_detail': [
        {'labels': {'environment': 'tst', ...}, 'dpm': 5.0},
        {'labels': {'environment': 'prd', ...}, 'dpm': 2.6},
        ...
    ]
}
```

Output format behavior:

| Format | Behavior |
|--------|----------|
| JSON | `series_detail` array nested inside each metric entry with full labels + DPM |
| CSV | One row per metric (headline = max DPM), columns unchanged |
| Text | Headline row unchanged, per-series listed indented below each metric |
| Prom | No change, one gauge per metric name using headline max DPM |
| Exporter | No change, `update_prometheus_metrics` uses headline max DPM |

## Scope

- `dpm-finder.py`: query changes, result handling, CLI flag, output formatting
- No new files or dependencies
- Backward-compatible CSV output (same columns, same shape)
