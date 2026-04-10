# DPM Accuracy Improvements - Results

## Test Metric: `openwebui_users_active`

Tested against live Prometheus at `prometheus-prod-65-prod-eu-west-2.grafana.net` on 2026-04-10. This metric has 4 environments (sbx, dev, tst, prd) pushed via OTel gateway with irregular intervals.

## Before (old behavior)

Query: `count_over_time(openwebui_users_active{__ignore_usage__=""}[5m])/5`

The script took `result[0]` from the Prometheus response, which was an arbitrary series:

```
Script picks result[0] -> dpm_value = 1
Script picks environment: sbx
```

| Environment | Actual DPM | Reported by script |
|-------------|------------|-------------------|
| sbx         | 1.0        | 1.0 (picked as result[0]) |
| dev         | 1.0        | ignored |
| tst         | 3.2-5.0    | ignored |
| prd         | 2.6-3.0    | ignored |

Total DPM across all series: ~9.6. Script reported: 1.0.

### Problems identified

1. **`result[0]` bug**: Only the first series in the response was used. For `openwebui_users_active`, this was `sbx` (the lowest DPM environment), causing severe underreporting.

2. **5-minute lookback too narrow**: For bursty push-based metrics via OTel gateway, a 5-minute window risks missing samples entirely depending on query timing. The `tst` environment sends ~3 samples per minute in irregular bursts (~5s, ~20s, ~34s gaps).

## After (new behavior)

Query: `count_over_time(openwebui_users_active{__ignore_usage__=""}[10m])/10`

The script now iterates all series, takes the max as headline DPM, and collects per-series detail:

```
Headline DPM (max): 3.0
Series: 8
  tst: 3.0
  prd: 3.0
  sbx: 1.0
  dev: 1.0
  dev: 0.4
  tst: 0.3
  tst: 0.2
  tst: 0.1
```

| Change | Before | After |
|--------|--------|-------|
| Headline DPM | 1.0 (arbitrary result[0]) | 3.0 (max across series) |
| Lookback window | 5m (hardcoded) | 10m (default, configurable via `--lookback`) |
| Per-series detail | Not available | Available in JSON and text output |
| Series visibility | 1 of 8 | 8 of 8 |

## Changes made

1. **`--lookback` / `-l` flag** (default: 10 minutes, minimum: 1) - configurable lookback window for DPM calculation
2. **Max-across-series** - headline DPM is now the maximum of any single series, not an arbitrary first result
3. **Per-series breakdown** - JSON output includes `series_detail` array with labels and DPM per series; text output shows indented per-series lines below each metric
4. **CSV and prom formats unchanged** - backward compatible

## Commits

- `a1aa79d` feat: add --lookback flag and thread through call chain
- `edfee5c` fix: use max-across-series for DPM instead of result[0]
- `04273ff` feat: add per-series DPM breakdown to JSON and text output
