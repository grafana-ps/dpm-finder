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
from queue import Queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

def make_request_with_retry(url, auth, params=None, max_retries=3, retry_delay=1, quiet=False):
    """
    Make HTTP request with retry logic for ConnectionResetError
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
                timeout=15,
            )
            response.raise_for_status()
            return response
        except ConnectionResetError as e:
            if attempt < max_retries - 1:  # Don't sleep on the last attempt
                if not quiet:
                    print(f"\nConnection reset, retrying in {retry_delay} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                return e  # Return the exception if we've exhausted all retries
        except requests.exceptions.RequestException as e:
            return e  # Return any other request exception

def get_metric_json(url, username, api_key, quiet=False):
    """
    Get the metric names from the Prometheus API
    Returns:
        On success: Dictionary containing metric names
        On failure: None
    """
    response = make_request_with_retry(
        url,
        auth=HTTPBasicAuth(username, api_key),
        quiet=quiet
    )
    
    if isinstance(response, Exception):
        if not quiet:
            print(f"Error retrieving metric names: {str(response)}")
        return None
    
    try:
        return response.json()
    except Exception as e:
        if not quiet:
            print(f"Error parsing metric names response: {str(e)}")
        return None

def process_metric_chunk(chunk, metric_value_url, username, api_key, results_queue, quiet=False):
    """
    Process a chunk of metrics and put results in the queue
    """
    chunk_results = {}
    chunk_times = []
    
    for metric in chunk:
        metric_start_time = time.time()
        if not quiet:
            print(".", end="", flush=True)
        
        query = 'count_over_time(%s{__ignore_usage__=""}[5m])/5'%(metric)
        response = make_request_with_retry(
            metric_value_url,
            auth=HTTPBasicAuth(username, api_key),
            params={"query": query},
            quiet=quiet
        )
        
        if isinstance(response, Exception):
            if not quiet:
                print(f"\nError processing metric {metric}: {str(response)}")
            chunk_times.append(time.time() - metric_start_time)
            continue
            
        try:
            query_data = response.json().get("data", {}).get("result", [])
            if query_data and len(query_data) > 0 and len(query_data[0].get('value', [])) > 1:
                chunk_results[metric] = query_data[0]['value'][1]
        except Exception as e:
            if not quiet:
                print(f"\nError parsing response for metric {metric}: {str(e)}")
        
        chunk_times.append(time.time() - metric_start_time)
    
    results_queue.put((chunk_results, chunk_times))

def get_metric_rates(metric_value_url, username, api_key, metric_names, metric_aggregations, output_format='csv', min_dpm=1, quiet=False, thread_count=10):
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
    Returns:
        True if processing was successful, False otherwise
    """
    # Ensure thread count is at least 1
    thread_count = max(1, thread_count)
    
    start_time = time.time()
    dpm_data = {}
    
    if metric_names is None:
        if not quiet:
            print("Error: Failed to retrieve metric names")
        return False
    else:
        if not quiet:
            print(f"Found {len(metric_names['data'])} metrics")

    # Create set of metrics that have aggregation rules
    aggregated_metrics = set()
    if metric_aggregations is not None:
        try:
            # Extract metric names from the aggregation rules
            for rule in metric_aggregations:
                if isinstance(rule, dict) and 'metric' in rule:
                    aggregated_metrics.add(rule['metric'])
            if not quiet:
                print(f"Found {len(aggregated_metrics)} metrics with aggregation rules")

        except Exception as e:
            if not quiet:
                print(f"Warning: Error processing aggregation rules: {str(e)}")
    
    # Filter metrics that don't end with _count, _bucket, _sum and are not in aggregation rules
    filtered_metrics = [
        metric for metric in metric_names['data']
        if not any(metric.endswith(suffix) for suffix in ['_count', '_bucket', '_sum'])
        and metric not in aggregated_metrics
    ]
    
    if not quiet:
        print(f"\nFiltered to {len(filtered_metrics)} metrics - checking for DPM")
    
    # Create a queue for results
    results_queue = Queue()
    processing_times = []
    
    # Calculate chunk size based on number of metrics and threads
    total_metrics = len(filtered_metrics)
    chunk_size = max(1, total_metrics // thread_count)  # Ensure at least 1 metric per chunk
    
    # Split metrics into chunks for parallel processing
    metric_chunks = [filtered_metrics[i:i + chunk_size] for i in range(0, total_metrics, chunk_size)]
    
    if not quiet:
        print(f"Processing {total_metrics} metrics in {len(metric_chunks)} chunks using {thread_count} threads")
    
    # Create thread pool with the specified number of threads
    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        # Submit tasks to the thread pool
        futures = [
            executor.submit(process_metric_chunk, chunk, metric_value_url, username, api_key, results_queue, quiet)
            for chunk in metric_chunks
        ]
        
        # Wait for all tasks to complete
        for future in as_completed(futures):
            try:
                future.result()  # This will raise any exceptions that occurred in the thread
            except Exception as e:
                if not quiet:
                    print(f"\nError in thread: {str(e)}")
    
    # Collect results from queue
    while not results_queue.empty():
        chunk_results, chunk_times = results_queue.get()
        dpm_data.update(chunk_results)
        processing_times.extend(chunk_times)

    total_time = time.time() - start_time
    avg_metric_time = sum(processing_times) / len(processing_times) if processing_times else 0
    
    if not quiet:
        print(f"\nTiming Statistics:")
        print(f"Total runtime: {total_time:.2f} seconds")
        print(f"Average time per metric: {avg_metric_time:.3f} seconds")
        print(f"Total metrics processed: {len(filtered_metrics)}")
        print(f"Metrics processing rate: {len(filtered_metrics)/total_time:.1f} metrics/second")
        print(f"Effective threads used: {min(thread_count, len(metric_chunks))}\n")

    metrics_above_threshold = 0
    # Sort items by DPM value in descending order
    sorted_dpm = sorted(dpm_data.items(), key=lambda x: float(x[1]), reverse=True)
    
    if output_format == 'csv':
        with open("metric_rates.csv", "w", encoding="utf-8") as f:
            # Write CSV header
            f.write("metric_name,dpm\n")
            for metric_name, dpm in sorted_dpm:
                if float(dpm) > min_dpm:
                    if not quiet:
                        print(f"{metric_name},{dpm}")
                    f.write(f"{metric_name},{dpm}\n")
                    metrics_above_threshold += 1
    elif output_format == 'json':
        import json
        output_data = {
            "metrics": [
                {"metric_name": metric_name, "dpm": float(dpm)}
                for metric_name, dpm in sorted_dpm
                if float(dpm) > min_dpm
            ],
            "total_metrics_above_threshold": sum(1 for _, dpm in sorted_dpm if float(dpm) > min_dpm),
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
        metrics_above_threshold = len(output_data["metrics"])
    elif output_format == 'prom':
        output_filename = "metric_rates.prom"
        with open(output_filename, "w", encoding="utf-8") as f:
            # Add HELP and TYPE metadata for DPM metrics
            f.write("# HELP metric_dpm_rate Data points per minute for each metric\n")
            f.write("# TYPE metric_dpm_rate gauge\n")
            for metric_name, dpm in sorted_dpm:
                if float(dpm) > min_dpm:
                    # Escape special characters in metric names as per Prometheus format
                    safe_metric_name = metric_name.replace('-', '_').replace('.', '_').replace(':', '_')
                    output_line = f'metric_dpm_rate{{metric_name="{safe_metric_name}"}} {dpm}\n'
                    if not quiet:
                        print(output_line, end='')
                    f.write(output_line)
                    metrics_above_threshold += 1
            
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
            for metric_name, dpm in sorted_dpm:
                if float(dpm) > min_dpm:
                    output_line = f"{metric_name}: {dpm}\n"
                    if not quiet:
                        print(output_line, end='')
                    f.write(output_line)
                    metrics_above_threshold += 1
            
            # Add timing information to the text output
            f.write("\nPerformance Metrics:\n")
            f.write(f"Total runtime: {total_time:.2f} seconds\n")
            f.write(f"Average time per metric: {avg_metric_time:.3f} seconds\n")
            f.write(f"Total metrics processed: {len(filtered_metrics)}\n")
            f.write(f"Metrics processing rate: {len(filtered_metrics)/total_time:.1f} metrics/second\n")
    
    if not quiet:
        print(f"\nTotal number of metrics with DPM > {min_dpm}: {metrics_above_threshold}")

    return True

def main(): 
    parser = argparse.ArgumentParser(
        description="""
        DPM Finder - A tool to calculate Data Points per Minute (DPM) for Prometheus metrics.
        This script connects to a Prometheus instance, retrieves all metric names,
        calculates their DPM, and outputs the results either in CSV or text format.
        Results are filtered to show only metrics above a specified DPM threshold.
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
        '-t', '--threads',
        type=int,
        default=10,
        help='Number of concurrent threads for processing metrics (minimum: 1, default: 10)'
    )
    args = parser.parse_args()

    # Validate thread count
    if args.threads < 1:
        if not args.quiet:
            print(f"Warning: Thread count {args.threads} is less than 1, setting to 1")
        args.threads = 1

    if not args.quiet:
        print(f"\nRunning with options:")
        print(f"- Output format: {args.format}")
        print(f"- Minimum DPM threshold: {args.min_dpm}")
        print(f"- Quiet mode: {args.quiet}")
        print(f"- Thread count: {args.threads}\n")

    load_dotenv()
    prometheus_endpoint=os.getenv("PROMETHEUS_ENDPOINT")
    username=os.getenv("PROMETHEUS_USERNAME")
    api_key=os.getenv("PROMETHEUS_API_KEY")
    metric_value_url=f"{prometheus_endpoint}/api/prom/api/v1/query"
    metric_name_url=f"{prometheus_endpoint}/api/prom/api/v1/label/__name__/values"
    metric_aggregation_url=f"{prometheus_endpoint}/aggregations/rules"


    metric_names = get_metric_json(metric_name_url,username,api_key)
    metric_aggregations = get_metric_json(metric_aggregation_url,username,api_key)




    get_metric_rates(
        metric_value_url,
        username,
        api_key,
        metric_names,
        metric_aggregations,
        output_format=args.format,
        min_dpm=args.min_dpm,
        quiet=args.quiet,
        thread_count=args.threads
    )

if __name__ == "__main__":
    main()
