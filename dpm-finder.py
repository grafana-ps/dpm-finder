#!/usr/bin/env python3

"""
main.py - calculate the DPM for a given prometheus cluster
and return the results
"""
import os
import time
import argparse
import json
import subprocess
import requests
from requests import HTTPError
import threading
import logging
import signal
import sys
from queue import Queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
from prometheus_client import Gauge, Counter, Info, start_http_server, CollectorRegistry, REGISTRY

# Set up module-level logger
logger = logging.getLogger(__name__)


class JSONResponse:
    """Small response adapter shared by direct HTTP and GCX transports."""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class GCXCommandError(Exception):
    """A safe, actionable failure returned by the GCX subprocess transport."""


class GCXClient:
    """Run Prometheus and Adaptive Metrics reads through GCX's authenticated context."""

    def __init__(self, binary="gcx", context=None, datasource=None, timeout=60):
        self.binary = binary
        self.context = context
        self.datasource = datasource
        self.timeout = timeout

    def _run(self, command, include_datasource=False):
        argv = [self.binary, *command]
        if self.context:
            argv.extend(["--context", self.context])
        if include_datasource and self.datasource:
            argv.extend(["--datasource", self.datasource])
        argv.extend(["-o", "json"])

        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as error:
            raise GCXCommandError(
                f"GCX binary '{self.binary}' was not found; install gcx or provide a path "
                "with --gcx-binary and ensure it is on PATH"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise GCXCommandError(
                f"GCX command exceeded the {self.timeout}s timeout"
            ) from error
        except OSError as error:
            raise GCXCommandError(f"Unable to start GCX: {error}") from error

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown GCX error").strip()
            raise GCXCommandError(detail)

        try:
            return json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise GCXCommandError("GCX returned invalid JSON") from error

    def query(self, expression):
        payload = self._run(
            ["metrics", "query", expression], include_datasource=True
        )
        if not isinstance(payload, dict):
            raise GCXCommandError("GCX metrics query returned an unexpected JSON shape")
        return JSONResponse(payload)

    def list_metric_names(self):
        payload = self._run(
            ["metrics", "list-names", "--limit", "0"], include_datasource=True
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise GCXCommandError("GCX metric-name query returned an unexpected JSON shape")
        return payload

    def list_adaptive_metrics_rules(self):
        payload = self._run(
            ["metrics", "adaptive", "rules", "list", "--limit", "0"]
        )
        if not isinstance(payload, list):
            raise GCXCommandError("GCX Adaptive Metrics rules returned an unexpected JSON shape")
        return payload


def query_metric(metric_value_url, username, api_key, query, quiet=False, timeout=60,
                 gcx_client=None):
    """Execute one PromQL instant query through direct HTTP or GCX."""
    if gcx_client is not None:
        try:
            return gcx_client.query(query)
        except GCXCommandError as error:
            if not quiet:
                logger.warning(f"GCX metrics query failed: {error}")
            return error

    return make_request_with_retry(
        metric_value_url,
        auth=HTTPBasicAuth(username, api_key),
        params={"query": query},
        quiet=quiet,
        timeout=timeout,
    )

# Global variables for exporter mode
shutdown_event = threading.Event()

# Prometheus metrics
dpm_metric = Gauge('metric_dpm_rate', 'Data points per minute for each metric', ['metric_name'])
runtime_metric = Gauge('dpm_finder_runtime_seconds', 'Total runtime of the last DPM calculation')
avg_processing_time_metric = Gauge('dpm_finder_avg_metric_process_seconds', 'Average time to process each metric')
metrics_processed_metric = Counter('dpm_finder_metrics_processed_total', 'Total number of metrics processed')
processing_rate_metric = Gauge('dpm_finder_processing_rate_metrics_per_second', 'Rate of metric processing')
last_update_metric = Gauge('dpm_finder_last_update_timestamp', 'Unix timestamp of last metrics update')
exporter_info = Info('dpm_finder_exporter', 'Information about the DPM finder exporter')

def update_prometheus_metrics(filtered_dpm, performance_data):
    """Update Prometheus metrics with latest DPM data"""
    # Clear existing DPM metrics
    dpm_metric.clear()

    # Update DPM metrics for each metric
    for metric_name, dpm_value in filtered_dpm.items():
        # Create safe metric name for label
        safe_metric_name = metric_name.replace('-', '_').replace('.', '_').replace(':', '_')
        dpm_metric.labels(metric_name=safe_metric_name).set(float(dpm_value))
    
    # Update performance metrics
    runtime_metric.set(performance_data['total_time'])
    avg_processing_time_metric.set(performance_data['avg_metric_time'])
    metrics_processed_metric._value._value = performance_data['total_metrics']  # Reset counter to current value
    processing_rate_metric.set(performance_data['processing_rate'])
    last_update_metric.set(performance_data['last_update'])

def make_request_with_retry(url, auth, params=None, max_retries=10, retry_delay=2, quiet=False, timeout=60):
    """
    Make HTTP request with retry logic for any error with exponential backoff
    Returns:
        On success: requests.Response object
        On failure: Exception object
    """
    for attempt in range(max_retries):
        try:
            response = requests.get(
                url,
                auth=auth,
                params=params,
                timeout=timeout,
            )
            response.raise_for_status()
            return response
        except Exception as e:
            # If it's an HTTPError and status is a client error (4xx) except 429, don't retry
            try:
                from requests import HTTPError
                if isinstance(e, HTTPError) and hasattr(e, 'response') and e.response is not None:
                    status = e.response.status_code
                    if 400 <= status < 500 and status != 429:
                        if not quiet:
                            # Extract detailed error from response where possible
                            err_detail = None
                            try:
                                err_json = e.response.json()
                                err_text = err_json.get("error")
                                err_type = err_json.get("errorType")
                                if err_text and err_type:
                                    err_detail = f"{err_type}: {err_text}"
                                elif err_text:
                                    err_detail = err_text
                            except Exception:
                                pass
                            if err_detail is None:
                                try:
                                    err_detail = (e.response.text or "").strip()
                                except Exception:
                                    err_detail = str(e)
                            query_snippet = ""
                            try:
                                if isinstance(params, dict) and "query" in params and params["query"]:
                                    # Limit to avoid overly long logs
                                    q = str(params["query"])
                                    query_snippet = f" query='{q[:200]}'"
                            except Exception:
                                pass
                            # Preserve the server detail so callers can classify a 422 precisely.
                            if status == 422:
                                logger.warning(f"Prometheus query rejected with HTTP 422: {err_detail}.{query_snippet}")
                            else:
                                logger.warning(f"Request rejected with HTTP {status}: {err_detail}.{query_snippet}")
                        return e
            except Exception:
                # If any issue determining status, fall back to retry path below
                pass
            if attempt < max_retries - 1:  # Don't sleep on the last attempt
                if not quiet:
                    logger.warning(f"Request failed ({type(e).__name__}: {str(e)}), retrying in {retry_delay} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                if not quiet:
                    logger.error(f"Request failed after {max_retries} attempts: {str(e)}")
                return e  # Return the exception if we've exhausted all retries

def retry_with_backoff(operation, operation_name, max_retries=3, retry_delay=2, quiet=False):
    """
    Generic retry function with exponential backoff for any operation
    Args:
        operation: Function to execute (should return a value or raise an exception)
        operation_name: String description of the operation for logging
        max_retries: Maximum number of retry attempts
        retry_delay: Initial delay between retries in seconds
        quiet: If True, suppress retry logging
    Returns:
        Result of operation on success, None on failure
    """
    for attempt in range(max_retries):
        try:
            return operation()
        except Exception as e:
            if attempt < max_retries - 1:  # Don't sleep on the last attempt
                if not quiet:
                    logger.warning(f"{operation_name} failed ({type(e).__name__}: {str(e)}), retrying in {retry_delay} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                if not quiet:
                    logger.error(f"{operation_name} failed after {max_retries} attempts: {str(e)}")
                return None

def get_metric_json(url, username, api_key, quiet=False, timeout=60,
                    resource_name="metric data", required_scope=None):
    """
    Get JSON data from a Prometheus or Adaptive Metrics API endpoint.
    Returns:
        On success: Decoded JSON data
        On failure: None
    """
    response = make_request_with_retry(
        url,
        auth=HTTPBasicAuth(username, api_key),
        quiet=quiet,
        timeout=timeout
    )
    
    if isinstance(response, Exception):
        if not quiet:
            status = None
            if isinstance(response, HTTPError) and response.response is not None:
                status = response.response.status_code
            if required_scope and status in (401, 403):
                logger.error(
                    f"Unable to retrieve {resource_name}: HTTP {status}. The token must include "
                    f"the '{required_scope}' scope; continuing with 422 fallback detection."
                )
            else:
                logger.error(f"Unable to retrieve {resource_name}: {str(response)}")
        return None
    
    try:
        return response.json()
    except Exception as e:
        if not quiet:
            logger.error(f"Unable to parse {resource_name} response: {str(e)}")
        return None


def get_adaptive_metrics_rules(url, username, api_key, quiet=False, timeout=60):
    """Fetch applied Adaptive Metrics rules with an actionable permission error."""
    return get_metric_json(
        url,
        username,
        api_key,
        quiet=quiet,
        timeout=timeout,
        resource_name="Adaptive Metrics aggregation rules",
        required_scope="adaptive-metrics-rules:read",
    )


def collect_metric_inventory(metric_name_url, metric_aggregation_url, username, api_key,
                             quiet=False, timeout=60, gcx_client=None):
    """Fetch metric names and best-effort Adaptive Metrics rules through one transport."""
    if gcx_client is None:
        return (
            get_metric_json(metric_name_url, username, api_key, quiet=quiet, timeout=timeout),
            get_adaptive_metrics_rules(
                metric_aggregation_url, username, api_key, quiet=quiet, timeout=timeout
            ),
        )

    try:
        metric_names = gcx_client.list_metric_names()
    except GCXCommandError as error:
        if not quiet:
            logger.error(f"Unable to retrieve metric names through GCX: {error}")
        return None, None

    try:
        metric_aggregations = gcx_client.list_adaptive_metrics_rules()
    except GCXCommandError as error:
        metric_aggregations = None
        if not quiet:
            logger.warning(
                "Adaptive Metrics rules are unavailable through GCX; continuing with stored-series "
                f"and 422 detection: {error}"
            )

    return metric_names, metric_aggregations


def get_stored_adaptive_metric_names(metric_value_url, username, api_key, quiet=False, timeout=60,
                                     gcx_client=None):
    """Discover metric names with currently stored Adaptive Metrics series."""
    query = (
        'group by (__name__) ({__aggregation__=~".+",'
        '__aggregation__!="none",__ignore_usage__=""})'
    )
    response = query_metric(
        metric_value_url, username, api_key, query,
        quiet=quiet, timeout=timeout, gcx_client=gcx_client,
    )
    if isinstance(response, Exception):
        if not quiet:
            logger.warning(
                "Unable to inventory stored Adaptive Metrics series; continuing with rule and "
                "422 fallback detection"
            )
        return set()

    try:
        result = response.json().get('data', {}).get('result', [])
        return {
            item['metric']['__name__']
            for item in result
            if isinstance(item, dict)
            and isinstance(item.get('metric'), dict)
            and isinstance(item['metric'].get('__name__'), str)
        }
    except Exception as e:
        if not quiet:
            logger.warning(
                f"Unable to parse stored Adaptive Metrics inventory: {str(e)}; continuing with "
                "rule and 422 fallback detection"
            )
        return set()


def get_adaptive_metric_names(metric_names, metric_aggregations):
    """Return metric names covered by exact, prefix, or suffix Adaptive Metrics rules."""
    if not metric_aggregations:
        return set()

    adaptive_metrics = set()
    for rule in metric_aggregations:
        if not isinstance(rule, dict) or not isinstance(rule.get('metric'), str):
            continue

        rule_metric = rule['metric']
        match_type = rule.get('match_type', 'exact')
        if match_type == 'exact':
            adaptive_metrics.update(metric for metric in metric_names if metric == rule_metric)
        elif match_type == 'prefix':
            adaptive_metrics.update(metric for metric in metric_names if metric.startswith(rule_metric))
        elif match_type == 'suffix':
            adaptive_metrics.update(metric for metric in metric_names if metric.endswith(rule_metric))
        else:
            logger.warning(
                f"Ignoring Adaptive Metrics rule for '{rule_metric}' with unsupported match_type "
                f"'{match_type}'"
            )

    return adaptive_metrics


def is_adaptive_metrics_query_error(error):
    """Return True only for the explicit Adaptive Metrics aggregated-query 422."""
    if isinstance(error, GCXCommandError):
        detail = str(error).lower()
        return "can't query aggregated metric" in detail and "without aggregation" in detail
    if not isinstance(error, HTTPError) or error.response is None:
        return False
    if error.response.status_code != 422:
        return False

    try:
        payload = error.response.json()
    except Exception:
        detail = error.response.text or ''
    else:
        if not isinstance(payload, dict):
            return False
        detail = payload.get('error', '')
        if not isinstance(detail, str):
            return False
    detail = detail.lower()
    return "can't query aggregated metric" in detail and "without aggregation" in detail


def metric_selector(metric, adaptive_metric=False):
    """Build a selector that does not affect recommendations and can inspect AM storage."""
    matchers = ['__ignore_usage__=""']
    if adaptive_metric:
        # This disables Adaptive Metrics query mapping and exposes stored aggregated series.
        matchers.extend([
            '__aggregation__=~".+"',
            '__aggregation__!="none"',
        ])
    return f"{metric}{{{','.join(matchers)}}}"


def filter_metric_names(metric_names, include_histograms=False):
    """Filter internal and, by default, classic histogram/summary component series.

    Native histograms keep their base metric name and therefore remain included. Their
    histogram samples are reduced to numeric sample counts by count_over_time in the DPM query.
    """
    component_metrics = set()
    if not include_histograms:
        names = set(metric_names)

        # A bucket series identifies a classic histogram family. Only remove count/sum
        # components after verifying the complete bucket/count/sum family.
        for metric in names:
            if metric.endswith('_bucket'):
                base = metric[:-len('_bucket')]
                count_metric = f'{base}_count'
                sum_metric = f'{base}_sum'
                if count_metric in names and sum_metric in names:
                    component_metrics.update((metric, count_metric, sum_metric))

        # A classic summary has a base quantile series plus count and sum siblings.
        # Requiring all three preserves unrelated standalone metrics with these suffixes.
        for metric in names:
            if metric.endswith('_count'):
                base = metric[:-len('_count')]
                sum_metric = f'{base}_sum'
                if base in names and sum_metric in names:
                    component_metrics.update((metric, sum_metric))

    return [
        metric
        for metric in metric_names
        if not metric.startswith('grafana_')
        and metric not in component_metrics
    ]


def should_collect_series_detail(output_format, exporter_mode=False, no_series_detail=False):
    """Return whether per-series labels should be retained in memory and output."""
    return (
        not exporter_mode
        and not no_series_detail
        and output_format in ('json', 'text', 'txt')
    )


def process_metric_chunk(chunk, metric_value_url, username, api_key, results_queue, quiet=False,
                         timeout=60, lookback=10, collect_series_detail=False,
                         adaptive_metrics=None, gcx_client=None):
    """
    Process a chunk of metrics and put results in the queue
    """
    chunk_results = {}
    chunk_times = []
    
    adaptive_metrics = adaptive_metrics or set()

    for metric in chunk:
        metric_start_time = time.time()
        if not quiet:
            logger.debug(f"Processing metric: {metric}")
        
        # DPM over lookback window, per minute
        adaptive_metric = metric in adaptive_metrics
        selector = metric_selector(metric, adaptive_metric)
        query_dpm = 'count_over_time(%s[%dm])/%d' % (selector, lookback, lookback)
        response_dpm = query_metric(
            metric_value_url, username, api_key, query_dpm,
            quiet=quiet, timeout=timeout, gcx_client=gcx_client,
        )

        if isinstance(response_dpm, Exception) and not adaptive_metric and is_adaptive_metrics_query_error(response_dpm):
            adaptive_metric = True
            selector = metric_selector(metric, adaptive_metric=True)
            query_dpm = 'count_over_time(%s[%dm])/%d' % (selector, lookback, lookback)
            if not quiet:
                logger.info(
                    f"Adaptive Metrics aggregation detected for {metric}; retrying against the "
                    "stored aggregated series"
                )
            response_dpm = query_metric(
                metric_value_url, username, api_key, query_dpm,
                quiet=quiet, timeout=timeout, gcx_client=gcx_client,
            )
        
        if isinstance(response_dpm, Exception):
            if is_adaptive_metrics_query_error(response_dpm):
                if not quiet:
                    logger.warning(
                        f"Unable to query Adaptive Metrics aggregated series for {metric}; skipping metric"
                    )
            elif isinstance(response_dpm, HTTPError) and response_dpm.response is not None and response_dpm.response.status_code == 422:
                if not quiet:
                    logger.warning(f"Skipping metric due to unrelated Prometheus 422: {metric}")
            else:
                if not quiet:
                    logger.error(f"Error processing metric {metric}: {str(response_dpm)}")
            chunk_times.append(time.time() - metric_start_time)
            continue
            
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
                    if collect_series_detail:
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
        
        # Series cardinality (active series count at evaluation time)
        # Keep the same selector pattern for consistency with DPM query
        query_series = 'count(%s)' % selector
        response_series = query_metric(
            metric_value_url, username, api_key, query_series,
            quiet=quiet, timeout=timeout, gcx_client=gcx_client,
        )
        
        series_count_value = None
        if isinstance(response_series, Exception):
            if isinstance(response_series, HTTPError) and response_series.response is not None and response_series.response.status_code == 422:
                if not quiet:
                    logger.warning(f"Skipping series count due to Prometheus 422: {metric}")
            else:
                if not quiet:
                    logger.error(f"Error processing series count for metric {metric}: {str(response_series)}")
        else:
            try:
                query_data_series = response_series.json().get("data", {}).get("result", [])
                if query_data_series and len(query_data_series) > 0 and len(query_data_series[0].get('value', [])) > 1:
                    series_count_value = query_data_series[0]['value'][1]
            except Exception as e:
                if not quiet:
                    logger.error(f"Error parsing series count for metric {metric}: {str(e)}")
        
        # Only store metrics we could compute a DPM for
        if dpm_value is not None:
            chunk_results[metric] = {
                'dpm': dpm_value,
                'series_count': series_count_value if series_count_value is not None else "0",
                'series_detail': series_detail
            }
        
        chunk_times.append(time.time() - metric_start_time)
    
    results_queue.put((chunk_results, chunk_times))

def get_metric_rates(metric_value_url, username, api_key, metric_names, metric_aggregations,
                     output_format='csv', min_dpm=1, quiet=False, thread_count=10,
                     exporter_mode=False, timeout=60, cost_per_1000_series=None, lookback=10,
                     include_histograms=False, no_series_detail=False, gcx_client=None):
    """ 
    Calculate the metric rates
    Args:
        metric_value_url: URL for querying metric values
        username: Prometheus username
        api_key: Prometheus API key
        metric_names: List of metric names to process
        metric_aggregations: list of dictionaries of metric aggregation rules
        output_format: Format to output results ('csv', 'text'/'txt', 'json', or 'prom')
        min_dpm: Minimum DPM threshold to show metrics
        quiet: If True, suppress progress output
        thread_count: Number of threads to use for processing (minimum: 1)
        exporter_mode: If True, calculate metrics for exporter mode
        cost_per_1000_series: Optional float; if provided, compute and sort by estimated cost
        include_histograms: Include classic _bucket, _count, and _sum component series
        no_series_detail: Do not retain per-series labels for JSON/text output
    Returns:
        True if processing was successful, False otherwise
    """
    # Ensure thread count is at least 1
    thread_count = max(1, thread_count)
    
    start_time = time.time()
    dpm_data = {}
    
    if metric_names is None:
        if not quiet:
            logger.error("Failed to retrieve metric names")
        return False
    else:
        if not quiet:
            logger.info(f"Found {len(metric_names['data'])} metrics")

    rule_adaptive_metrics = get_adaptive_metric_names(metric_names['data'], metric_aggregations)
    stored_adaptive_metrics = get_stored_adaptive_metric_names(
        metric_value_url, username, api_key, quiet=quiet, timeout=timeout,
        gcx_client=gcx_client,
    )
    adaptive_metrics = rule_adaptive_metrics | stored_adaptive_metrics
    if not quiet:
        if metric_aggregations is None:
            logger.warning(
                "Adaptive Metrics rules are unavailable; aggregated metrics will be detected from "
                "stored series and explicit Prometheus 422 responses"
            )
        logger.info(
            f"Adaptive Metrics discovery found {len(rule_adaptive_metrics)} metrics from current "
            f"rules and {len(stored_adaptive_metrics)} from stored aggregated series "
            f"({len(adaptive_metrics)} unique); analyzing them directly"
        )

    # Adaptive Metrics metrics and native histogram base series remain in scope. Classic
    # histogram/summary components are opt-in because they multiply otherwise similar results.
    filtered_metrics = filter_metric_names(
        metric_names['data'], include_histograms=include_histograms
    )
    
    if not quiet:
        logger.info(f"Filtered to {len(filtered_metrics)} metrics - checking for DPM")
    
    # Create a queue for results
    results_queue = Queue()
    processing_times = []
    
    # Calculate chunk size based on number of metrics and threads
    total_metrics = len(filtered_metrics)
    chunk_size = max(1, total_metrics // thread_count)  # Ensure at least 1 metric per chunk
    
    # Split metrics into chunks for parallel processing
    metric_chunks = [filtered_metrics[i:i + chunk_size] for i in range(0, total_metrics, chunk_size)]
    
    if not quiet:
        logger.info(f"Processing {total_metrics} metrics in {len(metric_chunks)} chunks using {thread_count} threads")
    
    # Per-series labels are the dominant memory cost on large JSON/text runs.
    collect_series_detail = should_collect_series_detail(
        output_format, exporter_mode=exporter_mode, no_series_detail=no_series_detail
    )

    # Create thread pool with the specified number of threads
    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        # Submit tasks to the thread pool
        futures = [
            executor.submit(
                process_metric_chunk, chunk, metric_value_url, username, api_key, results_queue,
                quiet, timeout, lookback, collect_series_detail, adaptive_metrics, gcx_client
            )
            for chunk in metric_chunks
        ]
        
        # Wait for all tasks to complete
        for future in as_completed(futures):
            try:
                future.result()  # This will raise any exceptions that occurred in the thread
            except Exception as e:
                if not quiet:
                    logger.error(f"Error in thread: {str(e)}")
    
    # Collect results from queue
    while not results_queue.empty():
        chunk_results, chunk_times = results_queue.get()
        dpm_data.update(chunk_results)
        processing_times.extend(chunk_times)

    total_time = time.time() - start_time
    avg_metric_time = sum(processing_times) / len(processing_times) if processing_times else 0
    
    if not quiet:
        logger.info("Timing Statistics:")
        logger.info(f"Total runtime: {total_time:.2f} seconds")
        logger.info(f"Average time per metric: {avg_metric_time:.3f} seconds")
        logger.info(f"Total metrics processed: {len(filtered_metrics)}")
        logger.info(f"Metrics processing rate: {len(filtered_metrics)/total_time:.1f} metrics/second")
        logger.info(f"Effective threads used: {min(thread_count, len(metric_chunks))}")

    metrics_above_threshold = 0
    # Prepare enriched entries with computed numeric fields
    enriched = []
    for metric_name, payload in dpm_data.items():
        try:
            dpm_val = float(payload.get('dpm', 0))
        except Exception:
            dpm_val = 0.0
        try:
            series_val = float(payload.get('series_count', 0))
        except Exception:
            series_val = 0.0
        estimated_cost = None
        if cost_per_1000_series is not None:
            try:
                # cost = (series / 1000) * cost_per_1000_series * DPM
                estimated_cost = int(round((series_val / 1000.0) * float(cost_per_1000_series) * dpm_val))
            except Exception:
                estimated_cost = None
        enriched.append({
            'metric_name': metric_name,
            'dpm': dpm_val,
            'series_count': int(series_val),
            'estimated_cost': estimated_cost,
            'series_detail': sorted(payload.get('series_detail', []), key=lambda x: x['dpm'], reverse=True)
        })
    
    # Filter metrics above DPM threshold
    enriched = [m for m in enriched if m['dpm'] > float(min_dpm)]
    metrics_above_threshold = len(enriched)
    
    # Sort by estimated cost if provided; otherwise by DPM
    if cost_per_1000_series is not None:
        enriched.sort(key=lambda m: (m['estimated_cost'] if m['estimated_cost'] is not None else -1), reverse=True)
    else:
        enriched.sort(key=lambda m: m['dpm'], reverse=True)
    
    # Build dpm-only mapping for exporter compatibility
    dpm_only = {m['metric_name']: str(m['dpm']) for m in enriched}
    
    if exporter_mode:
        # Update Prometheus metrics for exporter mode
        performance_data = {
            'total_time': total_time,
            'avg_metric_time': avg_metric_time,
            'total_metrics': len(filtered_metrics),
            'processing_rate': len(filtered_metrics)/total_time if total_time > 0 else 0,
            'last_update': time.time()
        }
        update_prometheus_metrics(dpm_only, performance_data)
        if not quiet:
            logger.info(f"Updated exporter metrics: {metrics_above_threshold} metrics above threshold")
        return True
    
    if output_format == 'csv':
        with open("metric_rates.csv", "w", encoding="utf-8") as f:
            # Write CSV header
            if cost_per_1000_series is not None:
                f.write("metric_name,dpm,series_count,estimated_cost\n")
            else:
                f.write("metric_name,dpm,series_count\n")
            for item in enriched:
                metric_name = item['metric_name']
                dpm = item['dpm']
                series_count = item['series_count']
                estimated_cost = item['estimated_cost']
                if not quiet:
                    if cost_per_1000_series is not None and estimated_cost is not None:
                        print(f"{metric_name},{dpm},{series_count},{estimated_cost}")
                    else:
                        print(f"{metric_name},{dpm},{series_count}")
                if cost_per_1000_series is not None and estimated_cost is not None:
                    f.write(f"{metric_name},{dpm},{series_count},{estimated_cost}\n")
                else:
                    f.write(f"{metric_name},{dpm},{series_count}\n")
    elif output_format == 'json':
        import json
        output_data = {
            "metrics": enriched,
            "total_metrics_above_threshold": metrics_above_threshold,
            "performance_metrics": {
                "total_runtime_seconds": round(total_time, 2),
                "average_metric_processing_seconds": round(avg_metric_time, 3),
                "total_metrics_processed": len(filtered_metrics),
                "metrics_per_second": round(len(filtered_metrics)/total_time, 1)
            }
        }
        with open("metric_rates.json", "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)
            if not quiet:
                print(json.dumps(output_data, indent=2))
    elif output_format == 'prom':
        output_filename = "metric_rates.prom"
        with open(output_filename, "w", encoding="utf-8") as f:
            # Add HELP and TYPE metadata for DPM metrics
            f.write("# HELP metric_dpm_rate Data points per minute for each metric\n")
            f.write("# TYPE metric_dpm_rate gauge\n")
            for item in enriched:
                metric_name = item['metric_name']
                dpm = item['dpm']
                # Escape special characters in metric names as per Prometheus format
                safe_metric_name = metric_name.replace('-', '_').replace('.', '_').replace(':', '_')
                output_line = f'metric_dpm_rate{{metric_name="{safe_metric_name}"}} {dpm}\n'
                if not quiet:
                    print(output_line, end='')
                f.write(output_line)
            
            # Add series count metric as well
            f.write("\n# HELP metric_series_count Active series count for each metric\n")
            f.write("# TYPE metric_series_count gauge\n")
            for item in enriched:
                metric_name = item['metric_name']
                series_count = item['series_count']
                safe_metric_name = metric_name.replace('-', '_').replace('.', '_').replace(':', '_')
                output_line = f'metric_series_count{{metric_name="{safe_metric_name}"}} {series_count}\n'
                if not quiet:
                    print(output_line, end='')
                f.write(output_line)
            
            # Add performance metrics
            f.write("\n# HELP dpm_finder_runtime_seconds Total runtime of the DPM finder script\n")
            f.write("# TYPE dpm_finder_runtime_seconds gauge\n")
            f.write(f"dpm_finder_runtime_seconds {total_time}\n")
            
            f.write("\n# HELP dpm_finder_avg_metric_process_seconds Average time to process each metric\n")
            f.write("# TYPE dpm_finder_avg_metric_process_seconds gauge\n")
            f.write(f"dpm_finder_avg_metric_process_seconds {avg_metric_time}\n")
            
            f.write("\n# HELP dpm_finder_metrics_processed_total Total number of metrics processed\n")
            f.write("# TYPE dpm_finder_metrics_processed_total counter\n")
            f.write(f"dpm_finder_metrics_processed_total {len(filtered_metrics)}\n")
            
            f.write("\n# HELP dpm_finder_processing_rate_metrics_per_second Rate of metric processing\n")
            f.write("# TYPE dpm_finder_processing_rate_metrics_per_second gauge\n")
            f.write(f"dpm_finder_processing_rate_metrics_per_second {len(filtered_metrics)/total_time}\n")
    else:  # text/txt format
        output_filename = "metric_rates.txt"
        with open(output_filename, "w", encoding="utf-8") as f:
            if not quiet:
                print("\nMetrics: DPM and cardinality (series count):")
            f.write("Metrics: DPM and cardinality (series count):\n")
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
                # Per-series breakdown (pre-sorted by DPM descending)
                for s in item.get('series_detail', []):
                    label_str = ', '.join(f'{k}={v}' for k, v in s['labels'].items()) or '(no labels)'
                    detail_line = f"  {label_str}: dpm={s['dpm']}\n"
                    if not quiet:
                        print(detail_line, end='')
                    f.write(detail_line)

            # Add timing information to the text output
            f.write("\nPerformance Metrics:\n")
            f.write(f"Total runtime: {total_time:.2f} seconds\n")
            f.write(f"Average time per metric: {avg_metric_time:.3f} seconds\n")
            f.write(f"Total metrics processed: {len(filtered_metrics)}\n")
            f.write(f"Metrics processing rate: {len(filtered_metrics)/total_time:.1f} metrics/second\n")
    
    if not quiet:
        logger.info(f"Total number of metrics with DPM > {min_dpm}: {metrics_above_threshold}")

    return True

def run_metrics_updater(metric_value_url, metric_name_url, metric_aggregation_url, username, api_key,
                       min_dpm, thread_count, update_interval, quiet, timeout=60, lookback=10,
                       include_histograms=False, gcx_client=None):
    """
    Run periodic metrics updates for exporter mode
    """
    logger.info(f"Starting metrics updater with {update_interval}s interval")
    
    while not shutdown_event.is_set():
        def collect_and_update_metrics():
            logger.debug("Fetching metrics for update...")
            
            # Get fresh metric data
            metric_names, metric_aggregations = collect_metric_inventory(
                metric_name_url, metric_aggregation_url, username, api_key,
                quiet=True, timeout=timeout, gcx_client=gcx_client,
            )
            
            if metric_names is not None:
                # Calculate metrics in exporter mode
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
                    lookback=lookback,
                    include_histograms=include_histograms,
                    gcx_client=gcx_client,
                )
                if success:
                    logger.debug("Metrics updated successfully")
                    return True
                else:
                    raise Exception("Failed to calculate metric rates")
            else:
                raise Exception("Failed to fetch metric names")
        
        # Use retry logic with exponential backoff for metrics collection
        retry_with_backoff(
            collect_and_update_metrics,
            "Periodic metrics collection",
            max_retries=3,
            quiet=True  # Keep background updates quiet unless they completely fail
        )
        
        # Wait for next update or shutdown
        if shutdown_event.wait(timeout=update_interval):
            break
    
    logger.info("Metrics updater stopped")

def run_exporter(port, metric_value_url, metric_name_url, metric_aggregation_url, username, api_key,
                min_dpm, thread_count, update_interval, quiet, timeout=60, lookback=10,
                include_histograms=False, gcx_client=None):
    """
    Run the Prometheus exporter server
    """
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        shutdown_event.set()
        sys.exit(0)
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Set exporter info
    exporter_info.info({
        'version': '1.0.0',
        'min_dpm_threshold': str(min_dpm),
        'update_interval_seconds': str(update_interval),
        'thread_count': str(thread_count),
        'include_classic_histograms': str(include_histograms).lower()
    })
    
    # Start HTTP server immediately using prometheus_client
    logger.info(f"Starting DPM finder exporter on port {port}")
    logger.info(f"Metrics available at: http://localhost:{port}/metrics")
    
    try:
        start_http_server(port)
        logger.info("Exporter server started successfully")
    except Exception as e:
        logger.error(f"Error starting exporter server: {e}")
        sys.exit(1)
    
    # Get initial metrics after server is running
    logger.info("Performing initial metrics collection...")
    
    def initial_metrics_collection():
        metric_names, metric_aggregations = collect_metric_inventory(
            metric_name_url, metric_aggregation_url, username, api_key,
            quiet=quiet, timeout=timeout, gcx_client=gcx_client,
        )
        
        if metric_names is not None:
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
                lookback=lookback,
                include_histograms=include_histograms,
                gcx_client=gcx_client,
            )
            if success:
                logger.info("Initial metrics collection completed")
                return True
            else:
                raise Exception("Failed to calculate initial metric rates")
        else:
            raise Exception("Failed to fetch metric names for initial collection")
    
    # Use retry logic with exponential backoff for initial collection
    initial_success = retry_with_backoff(
        initial_metrics_collection,
        "Initial metrics collection",
        max_retries=5,  # More retries for initial collection since it's critical
        quiet=quiet
    )
    
    if not initial_success and not quiet:
        logger.warning("Initial metrics collection failed, continuing with empty metrics until next update cycle")
    
    # Start metrics updater thread for periodic updates
    updater_thread = threading.Thread(
        target=run_metrics_updater,
        args=(metric_value_url, metric_name_url, metric_aggregation_url, username, api_key,
              min_dpm, thread_count, update_interval, quiet, timeout, lookback,
              include_histograms, gcx_client),
        daemon=True
    )
    updater_thread.start()
    
    try:
        # Keep the main thread alive
        while not shutdown_event.is_set():
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
    finally:
        shutdown_event.set()

def main(): 
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logger = logging.getLogger(__name__)
    
    parser = argparse.ArgumentParser(
        description="""
        DPM Finder - A tool to calculate Data Points per Minute (DPM) for Prometheus metrics.
        This script connects to a Prometheus instance, retrieves all metric names,
        calculates their DPM, and outputs the results either in CSV or text format.
        Results are filtered to show only metrics above a specified DPM threshold.
        
        This script is not intended to be run frequently.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False  # Disable default help to add our own
    )
    
    # Add custom help option
    parser.add_argument(
        '-h', '--help',
        action='help',
        default=argparse.SUPPRESS,
        help='Show this help message and exit'
    )

    parser.add_argument(
        '-f', '--format', 
        choices=['csv', 'text', 'txt', 'json', 'prom'],
        default='csv',
        help='Output format (default: csv). Note: "text" and "txt" are synonyms'
    )
    parser.add_argument(
        '-m', '--min-dpm',
        type=float,
        default=1.0,
        help='Minimum DPM threshold to show metrics (default: 1.0)'
    )
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Suppress progress output and only write results to file in CSV mode'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable debug logging for detailed output'
    )
    parser.add_argument(
        '-t', '--threads',
        type=int,
        default=10,
        help='Number of concurrent threads for processing metrics (minimum: 1, default: 10)'
    )
    parser.add_argument(
        '-e', '--exporter',
        action='store_true',
        help='Run as a Prometheus exporter server instead of one-time execution'
    )
    parser.add_argument(
        '-p', '--port',
        type=int,
        default=9966,
        help='Port to run the exporter server on (default: 9966)'
    )
    parser.add_argument(
        '-u', '--update-interval',
        type=int,
        default=86400,
        help='How often to update metrics in exporter mode, in seconds (default: 86400 or 1 day)'
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=60,
        help='Request timeout in seconds for Prometheus API calls (default: 60)'
    )
    parser.add_argument(
        '-l', '--lookback',
        type=int,
        default=10,
        help='Lookback window in minutes for DPM calculation (default: 10)'
    )
    parser.add_argument(
        '--cost-per-1000-series',
        type=float,
        default=None,
        help='Optional: Dollar cost per 1000 active series. If provided, output includes estimated_cost and is sorted by highest cost.'
    )
    parser.add_argument(
        '--include-histograms',
        action='store_true',
        help=(
            'Include classic histogram/summary component series (_bucket, _count, _sum). '
            'Native histogram base metrics are always included.'
        )
    )
    parser.add_argument(
        '--no-series-detail',
        action='store_true',
        help='Do not retain per-series labels in JSON/text output, reducing memory usage on large stacks.'
    )
    parser.add_argument(
        '--gcx',
        action='store_true',
        help='Run all metric queries through GCX and reuse its authenticated context.'
    )
    parser.add_argument(
        '--gcx-context',
        default=None,
        help='GCX context name (default: the current GCX context). Requires --gcx.'
    )
    parser.add_argument(
        '--gcx-datasource',
        default=None,
        help='Prometheus datasource UID (default: the datasource configured in GCX). Requires --gcx.'
    )
    parser.add_argument(
        '--gcx-binary',
        default='gcx',
        help='GCX executable name or path (default: gcx). Requires --gcx.'
    )
    args = parser.parse_args()

    # Set logging level based on arguments
    if args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
    elif args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)

    # Validate thread count
    if args.threads < 1:
        if not args.quiet:
            logger.warning(f"Thread count {args.threads} is less than 1, setting to 1")
        args.threads = 1
    
    # Validate update interval for exporter mode
    if args.exporter and args.update_interval < 30:
        logger.warning(f"Update interval {args.update_interval}s is very short, consider using 30s or more")
    
    # Validate port
    if args.exporter and (args.port < 1 or args.port > 65535):
        logger.error(f"Invalid port {args.port}, must be between 1 and 65535")
        sys.exit(1)
    
    # Validate timeout
    if args.timeout < 1:
        logger.error(f"Invalid timeout {args.timeout}, must be at least 1 second")
        sys.exit(1)

    if args.lookback < 1:
        logger.error(f"Invalid lookback {args.lookback}, must be at least 1 minute")
        sys.exit(1)

    if not args.gcx and (args.gcx_context or args.gcx_datasource or args.gcx_binary != 'gcx'):
        parser.error("--gcx-context, --gcx-datasource, and --gcx-binary require --gcx")

    if not args.quiet:
        if args.exporter:
            logger.info("Running in exporter mode:")
            logger.info(f"- Port: {args.port}")
            logger.info(f"- Update interval: {args.update_interval}s")
        else:
            logger.info("Running in one-time mode:")
            logger.info(f"- Output format: {args.format}")
        logger.info(f"- Minimum DPM threshold: {args.min_dpm}")
        if args.cost_per_1000_series is not None:
            logger.info(f"- Cost per 1000 series: {args.cost_per_1000_series}")
        logger.info(f"- Quiet mode: {args.quiet}")
        logger.info(f"- Verbose mode: {args.verbose}")
        logger.info(f"- Thread count: {args.threads}")
        logger.info(f"- Request timeout: {args.timeout}s")
        logger.info(f"- Lookback window: {args.lookback}m")
        logger.info(f"- Include classic histogram components: {args.include_histograms}")
        if not args.exporter and args.format in ('json', 'text', 'txt'):
            logger.info(f"- Collect per-series detail: {not args.no_series_detail}")
        if args.gcx:
            logger.info(f"- Query transport: GCX (context: {args.gcx_context or 'current'})")
        else:
            logger.info("- Query transport: direct Prometheus API")

    load_dotenv()
    gcx_client = None
    if args.gcx:
        prometheus_endpoint = None
        username = None
        api_key = None
        gcx_client = GCXClient(
            binary=args.gcx_binary,
            context=args.gcx_context,
            datasource=args.gcx_datasource,
            timeout=args.timeout,
        )
    else:
        prometheus_endpoint = os.getenv("PROMETHEUS_ENDPOINT")
        username = os.getenv("PROMETHEUS_USERNAME")
        api_key = os.getenv("PROMETHEUS_API_KEY")
        missing = [
            name for name, value in (
                ("PROMETHEUS_ENDPOINT", prometheus_endpoint),
                ("PROMETHEUS_USERNAME", username),
                ("PROMETHEUS_API_KEY", api_key),
            ) if not value
        ]
        if missing:
            logger.error(f"Missing required environment variables: {', '.join(missing)}")
            sys.exit(1)

    metric_value_url = (
        f"{prometheus_endpoint}/api/prom/api/v1/query" if prometheus_endpoint else None
    )
    metric_name_url = (
        f"{prometheus_endpoint}/api/prom/api/v1/label/__name__/values"
        if prometheus_endpoint else None
    )
    metric_aggregation_url = (
        f"{prometheus_endpoint}/aggregations/rules" if prometheus_endpoint else None
    )

    metric_names, metric_aggregations = collect_metric_inventory(
        metric_name_url, metric_aggregation_url, username, api_key,
        quiet=args.quiet, timeout=args.timeout, gcx_client=gcx_client,
    )

    if args.exporter:
        # Run as Prometheus exporter
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
            lookback=args.lookback,
            include_histograms=args.include_histograms,
            gcx_client=gcx_client,
        )
    else:
        # Run one-time execution
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
            lookback=args.lookback,
            include_histograms=args.include_histograms,
            no_series_detail=args.no_series_detail,
            gcx_client=gcx_client,
        )

if __name__ == "__main__":
    main()
