#!/usr/bin/env python3

"""
main.py - calculate the DPM for a given prometheus cluster
and return the results
"""
import os
import time
import argparse
import requests
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

def get_metric_json(url, username, api_key, quiet=False, timeout=60):
    """
    Get the metric names from the Prometheus API
    Returns:
        On success: Dictionary containing metric names
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
            logger.error(f"Error retrieving metric names: {str(response)}")
        return None
    
    try:
        return response.json()
    except Exception as e:
        if not quiet:
            logger.error(f"Error parsing metric names response: {str(e)}")
        return None

def process_metric_chunk(chunk, metric_value_url, username, api_key, results_queue, quiet=False, timeout=60):
    """
    Process a chunk of metrics and put results in the queue
    """
    chunk_results = {}
    chunk_times = []
    
    for metric in chunk:
        metric_start_time = time.time()
        if not quiet:
            logger.debug(f"Processing metric: {metric}")
        
        query = 'count_over_time(%s{__ignore_usage__=""}[5m])/5'%(metric)
        response = make_request_with_retry(
            metric_value_url,
            auth=HTTPBasicAuth(username, api_key),
            params={"query": query},
            quiet=quiet,
            timeout=timeout
        )
        
        if isinstance(response, Exception):
            if not quiet:
                logger.error(f"Error processing metric {metric}: {str(response)}")
            chunk_times.append(time.time() - metric_start_time)
            continue
            
        try:
            query_data = response.json().get("data", {}).get("result", [])
            if query_data and len(query_data) > 0 and len(query_data[0].get('value', [])) > 1:
                chunk_results[metric] = query_data[0]['value'][1]
        except Exception as e:
            if not quiet:
                logger.error(f"Error parsing response for metric {metric}: {str(e)}")
        
        chunk_times.append(time.time() - metric_start_time)
    
    results_queue.put((chunk_results, chunk_times))

def get_metric_rates(metric_value_url, username, api_key, metric_names, metric_aggregations, output_format='csv', min_dpm=1, quiet=False, thread_count=10, exporter_mode=False, timeout=60):
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

    # Create set of metrics that have aggregation rules
    aggregated_metrics = set()
    if metric_aggregations is not None:
        try:
            # Extract metric names from the aggregation rules
            for rule in metric_aggregations:
                if isinstance(rule, dict) and 'metric' in rule:
                    aggregated_metrics.add(rule['metric'])
            if not quiet:
                logger.info(f"Found {len(aggregated_metrics)} metrics with aggregation rules")

        except Exception as e:
            if not quiet:
                logger.warning(f"Error processing aggregation rules: {str(e)}")
    
    # Filter metrics that don't end with _count, _bucket, _sum, don't begin with grafana_, and are not in aggregation rules
    filtered_metrics = [
        metric for metric in metric_names['data']
        if not any(metric.endswith(suffix) for suffix in ['_count', '_bucket', '_sum'])
        and not metric.startswith('grafana_')
        and metric not in aggregated_metrics
    ]
    
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
    
    # Create thread pool with the specified number of threads
    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        # Submit tasks to the thread pool
        futures = [
            executor.submit(process_metric_chunk, chunk, metric_value_url, username, api_key, results_queue, quiet, timeout)
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
    # Sort items by DPM value in descending order
    sorted_dpm = sorted(dpm_data.items(), key=lambda x: float(x[1]), reverse=True)
    
    # Filter metrics above threshold
    filtered_dpm = {metric_name: dpm for metric_name, dpm in sorted_dpm if float(dpm) > min_dpm}
    metrics_above_threshold = len(filtered_dpm)
    
    if exporter_mode:
        # Update Prometheus metrics for exporter mode
        performance_data = {
            'total_time': total_time,
            'avg_metric_time': avg_metric_time,
            'total_metrics': len(filtered_metrics),
            'processing_rate': len(filtered_metrics)/total_time if total_time > 0 else 0,
            'last_update': time.time()
        }
        update_prometheus_metrics(filtered_dpm, performance_data)
        if not quiet:
            logger.info(f"Updated exporter metrics: {metrics_above_threshold} metrics above threshold")
        return True
    
    if output_format == 'csv':
        with open("metric_rates.csv", "w", encoding="utf-8") as f:
            # Write CSV header
            f.write("metric_name,dpm\n")
            for metric_name, dpm in filtered_dpm.items():
                if not quiet:
                    print(f"{metric_name},{dpm}")
                f.write(f"{metric_name},{dpm}\n")
    elif output_format == 'json':
        import json
        output_data = {
            "metrics": [
                {"metric_name": metric_name, "dpm": float(dpm)}
                for metric_name, dpm in filtered_dpm.items()
            ],
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
            for metric_name, dpm in filtered_dpm.items():
                # Escape special characters in metric names as per Prometheus format
                safe_metric_name = metric_name.replace('-', '_').replace('.', '_').replace(':', '_')
                output_line = f'metric_dpm_rate{{metric_name="{safe_metric_name}"}} {dpm}\n'
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
                print("\nMetrics and their DPM values:")
            f.write("Metrics and their DPM values:\n")
            for metric_name, dpm in filtered_dpm.items():
                output_line = f"{metric_name}: {dpm}\n"
                if not quiet:
                    print(output_line, end='')
                f.write(output_line)
            
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
                       min_dpm, thread_count, update_interval, quiet, timeout=60):
    """
    Run periodic metrics updates for exporter mode
    """
    logger.info(f"Starting metrics updater with {update_interval}s interval")
    
    while not shutdown_event.is_set():
        def collect_and_update_metrics():
            logger.debug("Fetching metrics for update...")
            
            # Get fresh metric data
            metric_names = get_metric_json(metric_name_url, username, api_key, quiet=True, timeout=timeout)
            metric_aggregations = get_metric_json(metric_aggregation_url, username, api_key, quiet=True, timeout=timeout)
            
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
                    timeout=timeout
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
                min_dpm, thread_count, update_interval, quiet, timeout=60):
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
        'thread_count': str(thread_count)
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
        metric_names = get_metric_json(metric_name_url, username, api_key, quiet=quiet, timeout=timeout)
        metric_aggregations = get_metric_json(metric_aggregation_url, username, api_key, quiet=quiet, timeout=timeout)
        
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
                timeout=timeout
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
              min_dpm, thread_count, update_interval, quiet, timeout),
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

    if not args.quiet:
        if args.exporter:
            logger.info("Running in exporter mode:")
            logger.info(f"- Port: {args.port}")
            logger.info(f"- Update interval: {args.update_interval}s")
        else:
            logger.info("Running in one-time mode:")
            logger.info(f"- Output format: {args.format}")
        logger.info(f"- Minimum DPM threshold: {args.min_dpm}")
        logger.info(f"- Quiet mode: {args.quiet}")
        logger.info(f"- Verbose mode: {args.verbose}")
        logger.info(f"- Thread count: {args.threads}")
        logger.info(f"- Request timeout: {args.timeout}s")

    load_dotenv()
    prometheus_endpoint=os.getenv("PROMETHEUS_ENDPOINT")
    username=os.getenv("PROMETHEUS_USERNAME")
    api_key=os.getenv("PROMETHEUS_API_KEY")

    metric_value_url=f"{prometheus_endpoint}/api/prom/api/v1/query"
    metric_name_url=f"{prometheus_endpoint}/api/prom/api/v1/label/__name__/values"
    metric_aggregation_url=f"{prometheus_endpoint}/aggregations/rules"

    metric_names = get_metric_json(metric_name_url, username, api_key, quiet=args.quiet, timeout=args.timeout)
    metric_aggregations = get_metric_json(metric_aggregation_url, username, api_key, quiet=args.quiet, timeout=args.timeout)

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
            timeout=args.timeout
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
            timeout=args.timeout
        )

if __name__ == "__main__":
    main()
