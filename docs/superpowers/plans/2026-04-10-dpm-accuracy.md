# DPM Accuracy Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix DPM underreporting by using max-across-series instead of result[0], increase the default lookback window from 5m to 10m, make it configurable, and add per-series detail to JSON/text output.

**Architecture:** All changes are in `dpm-finder.py`. The `lookback` parameter is threaded through `main()` -> `get_metric_rates()` -> `process_metric_chunk()` (and the exporter path). The DPM result handling switches from picking `result[0]` to iterating all results, taking the max, and collecting per-series detail. Output formatters are updated to include the detail where appropriate.

**Tech Stack:** Python 3, Prometheus HTTP API, argparse

---

### Task 1: Add `--lookback` CLI flag and thread it through the call chain

**Files:**
- Modify: `dpm-finder.py:750-755` (add argparse argument after `--timeout`)
- Modify: `dpm-finder.py:784` (add validation)
- Modify: `dpm-finder.py:800` (add to startup log)
- Modify: `dpm-finder.py:258` (`get_metric_rates` signature)
- Modify: `dpm-finder.py:334` (pass to `process_metric_chunk`)
- Modify: `dpm-finder.py:176` (`process_metric_chunk` signature)
- Modify: `dpm-finder.py:527` (`run_metrics_updater` signature)
- Modify: `dpm-finder.py:578` (`run_exporter` signature)
- Modify: `dpm-finder.py:814-843` (pass `lookback` in all call sites in `main()`)

- [ ] **Step 1: Add the argparse argument**

In `main()`, after the `--timeout` argument block (line 749), add:

```python
    parser.add_argument(
        '-l', '--lookback',
        type=int,
        default=10,
        help='Lookback window in minutes for DPM calculation (default: 10)'
    )
```

- [ ] **Step 2: Add validation for lookback**

After the timeout validation block (line 784), add:

```python
    if args.lookback < 1:
        logger.error(f"Invalid lookback {args.lookback}, must be at least 1 minute")
        sys.exit(1)
```

- [ ] **Step 3: Add lookback to startup log**

After the timeout log line (line 800), add:

```python
        logger.info(f"- Lookback window: {args.lookback}m")
```

- [ ] **Step 4: Thread `lookback` through `process_metric_chunk`**

Change the signature at line 176 from:

```python
def process_metric_chunk(chunk, metric_value_url, username, api_key, results_queue, quiet=False, timeout=60):
```

to:

```python
def process_metric_chunk(chunk, metric_value_url, username, api_key, results_queue, quiet=False, timeout=60, lookback=10):
```

- [ ] **Step 5: Thread `lookback` through `get_metric_rates`**

Change the signature at line 258 from:

```python
def get_metric_rates(metric_value_url, username, api_key, metric_names, metric_aggregations, output_format='csv', min_dpm=1, quiet=False, thread_count=10, exporter_mode=False, timeout=60, cost_per_1000_series=None):
```

to:

```python
def get_metric_rates(metric_value_url, username, api_key, metric_names, metric_aggregations, output_format='csv', min_dpm=1, quiet=False, thread_count=10, exporter_mode=False, timeout=60, cost_per_1000_series=None, lookback=10):
```

- [ ] **Step 6: Pass `lookback` from `get_metric_rates` to `process_metric_chunk`**

Change the `executor.submit` call at line 333-335 from:

```python
        futures = [
            executor.submit(process_metric_chunk, chunk, metric_value_url, username, api_key, results_queue, quiet, timeout)
            for chunk in metric_chunks
        ]
```

to:

```python
        futures = [
            executor.submit(process_metric_chunk, chunk, metric_value_url, username, api_key, results_queue, quiet, timeout, lookback)
            for chunk in metric_chunks
        ]
```

- [ ] **Step 7: Thread `lookback` through `run_metrics_updater`**

Change the signature at line 527 from:

```python
def run_metrics_updater(metric_value_url, metric_name_url, metric_aggregation_url, username, api_key, 
                       min_dpm, thread_count, update_interval, quiet, timeout=60):
```

to:

```python
def run_metrics_updater(metric_value_url, metric_name_url, metric_aggregation_url, username, api_key, 
                       min_dpm, thread_count, update_interval, quiet, timeout=60, lookback=10):
```

And pass it to `get_metric_rates` inside `collect_and_update_metrics()` (line 544). Change:

```python
                success = get_metric_rates(
                    metric_value_url,
                    username,
                    api_key,
                    metric_names,
                    metric_aggregations,
                    min_dpm=min_dpm,
                    quiet=True,  # Always quiet for background updates
                    thread_count=thread_count,
                    exporter_mode=True,
                    timeout=timeout
                )
```

to:

```python
                success = get_metric_rates(
                    metric_value_url,
                    username,
                    api_key,
                    metric_names,
                    metric_aggregations,
                    min_dpm=min_dpm,
                    quiet=True,  # Always quiet for background updates
                    thread_count=thread_count,
                    exporter_mode=True,
                    timeout=timeout,
                    lookback=lookback
                )
```

- [ ] **Step 8: Thread `lookback` through `run_exporter`**

Change the signature at line 578 from:

```python
def run_exporter(port, metric_value_url, metric_name_url, metric_aggregation_url, username, api_key,
                min_dpm, thread_count, update_interval, quiet, timeout=60):
```

to:

```python
def run_exporter(port, metric_value_url, metric_name_url, metric_aggregation_url, username, api_key,
                min_dpm, thread_count, update_interval, quiet, timeout=60, lookback=10):
```

Pass it to `get_metric_rates` inside `initial_metrics_collection()` (line 619). Change:

```python
            success = get_metric_rates(
                metric_value_url,
                username,
                api_key,
                metric_names,
                metric_aggregations,
                min_dpm=min_dpm,
                quiet=quiet,
                thread_count=thread_count,
                exporter_mode=True,
                timeout=timeout
            )
```

to:

```python
            success = get_metric_rates(
                metric_value_url,
                username,
                api_key,
                metric_names,
                metric_aggregations,
                min_dpm=min_dpm,
                quiet=quiet,
                thread_count=thread_count,
                exporter_mode=True,
                timeout=timeout,
                lookback=lookback
            )
```

Pass it to `run_metrics_updater` in the thread args (line 653). Change:

```python
        args=(metric_value_url, metric_name_url, metric_aggregation_url, username, api_key,
              min_dpm, thread_count, update_interval, quiet, timeout),
```

to:

```python
        args=(metric_value_url, metric_name_url, metric_aggregation_url, username, api_key,
              min_dpm, thread_count, update_interval, quiet, timeout, lookback),
```

- [ ] **Step 9: Pass `lookback` from `main()` to all call sites**

In the exporter call (line 816), add `lookback=args.lookback`:

```python
        run_exporter(
            port=args.port,
            metric_value_url=metric_value_url,
            metric_name_url=metric_name_url,
            metric_aggregation_url=metric_aggregation_url,
            username=username,
            api_key=api_key,
            min_dpm=args.min_dpm,
            thread_count=args.threads,
            update_interval=args.update_interval,
            quiet=args.quiet,
            timeout=args.timeout,
            lookback=args.lookback
        )
```

In the one-time execution call (line 831), add `lookback=args.lookback`:

```python
        get_metric_rates(
            metric_value_url,
            username,
            api_key,
            metric_names,
            metric_aggregations,
            output_format=args.format,
            min_dpm=args.min_dpm,
            quiet=args.quiet,
            thread_count=args.threads,
            timeout=args.timeout,
            cost_per_1000_series=args.cost_per_1000_series,
            lookback=args.lookback
        )
```

- [ ] **Step 10: Commit**

```bash
git add dpm-finder.py
git commit -m "feat: add --lookback flag and thread through call chain

Default lookback window increased from 5m to 10m to better capture
bursty push-based metrics. Configurable via -l/--lookback (minutes)."
```

---

### Task 2: Update DPM query to use configurable lookback and fix result handling

**Files:**
- Modify: `dpm-finder.py:188-217` (DPM query and result extraction in `process_metric_chunk`)
- Modify: `dpm-finder.py:247-252` (result storage)

- [ ] **Step 1: Update the PromQL query to use `lookback`**

In `process_metric_chunk`, change lines 188-189 from:

```python
        # DPM over last 5 minutes, per minute
        query_dpm = 'count_over_time(%s{__ignore_usage__=""}[5m])/5' % (metric)
```

to:

```python
        # DPM over lookback window, per minute
        query_dpm = 'count_over_time(%s{__ignore_usage__=""}[%dm])/%d' % (metric, lookback, lookback)
```

- [ ] **Step 2: Replace `result[0]` with max-across-series and per-series collection**

Replace lines 208-217 (the result extraction block) with:

```python
        try:
            query_data_dpm = response_dpm.json().get("data", {}).get("result", [])
            dpm_value = None
            series_detail = []
            for series in query_data_dpm:
                if len(series.get('value', [])) > 1:
                    try:
                        s_dpm = float(series['value'][1])
                    except (ValueError, TypeError):
                        continue
                    labels = {k: v for k, v in series.get('metric', {}).items()
                              if k != '__name__' and k != '__ignore_usage__'}
                    series_detail.append({'labels': labels, 'dpm': s_dpm})
                    if dpm_value is None or s_dpm > dpm_value:
                        dpm_value = s_dpm
        except Exception as e:
            if not quiet:
                logger.error(f"Error parsing response for metric {metric}: {str(e)}")
            dpm_value = None
            series_detail = []
```

- [ ] **Step 3: Update result storage to include `series_detail`**

Replace lines 247-252 (the result storage block) with:

```python
        # Only store metrics we could compute a DPM for
        if dpm_value is not None:
            chunk_results[metric] = {
                'dpm': dpm_value,
                'series_count': series_count_value if series_count_value is not None else "0",
                'series_detail': series_detail
            }
```

- [ ] **Step 4: Verify against live Prometheus**

Run a quick manual test against the live instance:

```bash
python3 -c "
import os, requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

load_dotenv()
endpoint = os.getenv('PROMETHEUS_ENDPOINT')
username = os.getenv('PROMETHEUS_USERNAME')
api_key = os.getenv('PROMETHEUS_API_KEY')
url = f'{endpoint}/api/prom/api/v1/query'
auth = HTTPBasicAuth(username, api_key)

metric = 'openwebui_users_active'
lookback = 10

query = 'count_over_time(%s{__ignore_usage__=\"\"}[%dm])/%d' % (metric, lookback, lookback)
response = requests.get(url, auth=auth, params={'query': query}, timeout=60)
result = response.json().get('data', {}).get('result', [])

dpm_value = None
series_detail = []
for series in result:
    if len(series.get('value', [])) > 1:
        s_dpm = float(series['value'][1])
        labels = {k: v for k, v in series.get('metric', {}).items()
                  if k != '__name__' and k != '__ignore_usage__'}
        series_detail.append({'labels': labels, 'dpm': s_dpm})
        if dpm_value is None or s_dpm > dpm_value:
            dpm_value = s_dpm

print(f'Headline DPM (max): {dpm_value}')
print(f'Series count: {len(series_detail)}')
for s in series_detail:
    env = s['labels'].get('environment', '?')
    print(f'  {env}: {s[\"dpm\"]}')
"
```

Expected: headline DPM should be the tst or prd value (~3-5), not sbx (1). All 4 environments listed.

- [ ] **Step 5: Commit**

```bash
git add dpm-finder.py
git commit -m "fix: use max-across-series for DPM instead of result[0]

Previously took the first arbitrary series from the query result,
which could severely underreport DPM. Now iterates all series,
takes the max as headline DPM, and collects per-series detail."
```

---

### Task 3: Thread `series_detail` through enrichment and update output formatters

**Files:**
- Modify: `dpm-finder.py:365-387` (enrichment loop)
- Modify: `dpm-finder.py:437-452` (JSON output)
- Modify: `dpm-finder.py:497-513` (text output)

CSV, prom, and exporter output are unchanged (headline DPM only, same columns).

- [ ] **Step 1: Pass `series_detail` through the enrichment loop**

In the enrichment loop (lines 365-387), change the `enriched.append` block from:

```python
        enriched.append({
            'metric_name': metric_name,
            'dpm': dpm_val,
            'series_count': int(series_val),
            'estimated_cost': estimated_cost
        })
```

to:

```python
        enriched.append({
            'metric_name': metric_name,
            'dpm': dpm_val,
            'series_count': int(series_val),
            'estimated_cost': estimated_cost,
            'series_detail': payload.get('series_detail', [])
        })
```

- [ ] **Step 2: Update JSON output to include `series_detail`**

No code change needed. The `enriched` list is already serialized directly into the JSON output at line 440:

```python
        output_data = {
            "metrics": enriched,
            ...
        }
```

Since `series_detail` is now in each enriched entry, it will be included automatically. Verify that the `series_detail` list of dicts (with `labels` dict and `dpm` float) is JSON-serializable — it is, since it contains only strings and floats.

- [ ] **Step 3: Update text output to show per-series breakdown**

In the text output block (lines 497-513), change the loop body from:

```python
            for item in enriched:
                metric_name = item['metric_name']
                dpm = item['dpm']
                series_count = item['series_count']
                if cost_per_1000_series is not None and item['estimated_cost'] is not None:
                    output_line = f"{metric_name}: dpm={dpm}, series={series_count}, estimated_cost={item['estimated_cost']}\n"
                else:
                    output_line = f"{metric_name}: dpm={dpm}, series={series_count}\n"
                if not quiet:
                    print(output_line, end='')
                f.write(output_line)
```

to:

```python
            for item in enriched:
                metric_name = item['metric_name']
                dpm = item['dpm']
                series_count = item['series_count']
                if cost_per_1000_series is not None and item['estimated_cost'] is not None:
                    output_line = f"{metric_name}: dpm={dpm}, series={series_count}, estimated_cost={item['estimated_cost']}\n"
                else:
                    output_line = f"{metric_name}: dpm={dpm}, series={series_count}\n"
                if not quiet:
                    print(output_line, end='')
                f.write(output_line)
                # Per-series breakdown
                for s in item.get('series_detail', []):
                    label_str = ', '.join(f'{k}={v}' for k, v in s['labels'].items())
                    detail_line = f"  {label_str}: dpm={s['dpm']}\n"
                    if not quiet:
                        print(detail_line, end='')
                    f.write(detail_line)
```

- [ ] **Step 4: End-to-end test against live Prometheus**

Run the script in JSON mode with a high `min_dpm` so it finishes quickly, and verify `openwebui_users_active` output:

```bash
python3 dpm-finder.py --format json --min-dpm 0.5 --lookback 10 --quiet 2>/dev/null && python3 -c "
import json
with open('metric_rates.json') as f:
    data = json.load(f)
for m in data['metrics']:
    if m['metric_name'] == 'openwebui_users_active':
        print(f\"DPM: {m['dpm']}\")
        print(f\"Series count: {m['series_count']}\")
        print(f\"Series detail entries: {len(m.get('series_detail', []))}\")
        for s in m.get('series_detail', []):
            print(f\"  {s['labels'].get('environment', '?')}: {s['dpm']}\")
        break
else:
    print('metric not found in output')
"
```

Expected: DPM is the max across environments (not 1.0 from sbx), series_detail has 4 entries.

- [ ] **Step 5: Commit**

```bash
git add dpm-finder.py
git commit -m "feat: add per-series DPM breakdown to JSON and text output

JSON output now includes series_detail array with labels and DPM per
series. Text output shows indented per-series lines below each metric.
CSV and prom formats unchanged."
```
