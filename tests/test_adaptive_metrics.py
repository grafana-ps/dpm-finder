import json
import queue
import unittest
from unittest.mock import patch

import requests

from dpm_test_module import dpm_finder


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def http_error(status, payload):
    response = requests.Response()
    response.status_code = status
    response.url = "https://prometheus.example.net/api/prom/api/v1/query"
    response._content = json.dumps(payload).encode()
    return requests.HTTPError(f"{status} response", response=response)


class AdaptiveMetricRuleTests(unittest.TestCase):
    def test_matches_exact_prefix_and_suffix_rules(self):
        metric_names = [
            "exact_metric",
            "http_requests_total",
            "worker_duration_seconds",
            "unmatched_metric",
        ]
        rules = [
            {"metric": "exact_metric"},
            {"metric": "http_", "match_type": "prefix"},
            {"metric": "_seconds", "match_type": "suffix"},
        ]

        self.assertEqual(
            dpm_finder.get_adaptive_metric_names(metric_names, rules),
            {"exact_metric", "http_requests_total", "worker_duration_seconds"},
        )

    def test_discovers_stored_adaptive_metric_names_in_one_query(self):
        response = FakeResponse(
            {
                "data": {
                    "result": [
                        {"metric": {"__name__": "first_metric"}, "value": [1, "1"]},
                        {"metric": {"__name__": "second_metric"}, "value": [1, "1"]},
                    ]
                }
            }
        )

        with patch.object(dpm_finder, "make_request_with_retry", return_value=response) as request:
            names = dpm_finder.get_stored_adaptive_metric_names(
                "https://prometheus.example.net/api/prom/api/v1/query",
                "123",
                "token",
            )

        self.assertEqual(names, {"first_metric", "second_metric"})
        query = request.call_args.kwargs["params"]["query"]
        self.assertEqual(
            query,
            (
                'group by (__name__) ({__aggregation__=~".+",'
                '__aggregation__!="none",__ignore_usage__=""})'
            ),
        )


class MetricFilteringTests(unittest.TestCase):
    def test_excludes_classic_histogram_components_but_keeps_native_base_metric(self):
        metrics = [
            "http_server_request_duration_seconds",
            "http_server_request_duration_seconds_bucket",
            "http_server_request_duration_seconds_count",
            "http_server_request_duration_seconds_sum",
            "ordinary_metric",
            "grafana_internal_metric",
        ]

        self.assertEqual(
            dpm_finder.filter_metric_names(metrics, include_histograms=False),
            ["http_server_request_duration_seconds", "ordinary_metric"],
        )

    def test_include_histograms_keeps_classic_components(self):
        metrics = ["request_duration_bucket", "request_duration_count", "request_duration_sum"]

        self.assertEqual(
            dpm_finder.filter_metric_names(metrics, include_histograms=True),
            metrics,
        )

    def test_keeps_standalone_metrics_with_component_like_suffixes(self):
        metrics = ["worker_count", "invoice_sum", "storage_bucket"]

        self.assertEqual(
            dpm_finder.filter_metric_names(metrics, include_histograms=False),
            metrics,
        )

    def test_excludes_summary_count_and_sum_only_with_family_siblings(self):
        metrics = ["rpc_duration_seconds", "rpc_duration_seconds_count", "rpc_duration_seconds_sum"]

        self.assertEqual(
            dpm_finder.filter_metric_names(metrics, include_histograms=False),
            ["rpc_duration_seconds"],
        )


class SeriesDetailTests(unittest.TestCase):
    def test_no_series_detail_disables_collection_for_json(self):
        self.assertFalse(
            dpm_finder.should_collect_series_detail(
                output_format="json", exporter_mode=False, no_series_detail=True
            )
        )

    def test_json_collects_detail_by_default_but_csv_and_exporter_do_not(self):
        self.assertTrue(
            dpm_finder.should_collect_series_detail(
                output_format="json", exporter_mode=False, no_series_detail=False
            )
        )
        self.assertFalse(
            dpm_finder.should_collect_series_detail(
                output_format="csv", exporter_mode=False, no_series_detail=False
            )
        )
        self.assertFalse(
            dpm_finder.should_collect_series_detail(
                output_format="json", exporter_mode=True, no_series_detail=False
            )
        )


class AdaptiveMetricQueryTests(unittest.TestCase):
    def test_adaptive_selector_requires_a_present_non_none_aggregation(self):
        selector = dpm_finder.metric_selector("request_count", adaptive_metric=True)

        self.assertIn('__aggregation__=~".+"', selector)
        self.assertIn('__aggregation__!="none"', selector)
        self.assertNotIn('__aggregation__="none"', selector)

    def test_non_string_422_error_is_not_classified_as_adaptive_metrics(self):
        malformed_error = http_error(422, {"status": "error", "error": 123})

        self.assertFalse(dpm_finder.is_adaptive_metrics_query_error(malformed_error))

    def test_list_shaped_422_body_is_not_classified_as_adaptive_metrics(self):
        malformed_error = http_error(422, ["unexpected", "response"])

        self.assertFalse(dpm_finder.is_adaptive_metrics_query_error(malformed_error))

    def test_known_adaptive_metric_queries_stored_aggregated_series(self):
        responses = [
            FakeResponse(
                {
                    "data": {
                        "result": [
                            {
                                "metric": {
                                    "__name__": "scrape_samples_scraped",
                                    "__aggregation__": "sum",
                                    "cluster": "prod",
                                },
                                "value": [1, "2.0"],
                            }
                        ]
                    }
                }
            ),
            FakeResponse({"data": {"result": [{"value": [1, "4"]}]}}),
        ]
        results = queue.Queue()

        with patch.object(dpm_finder, "make_request_with_retry", side_effect=responses) as request:
            dpm_finder.process_metric_chunk(
                ["scrape_samples_scraped"],
                "https://prometheus.example.net/api/prom/api/v1/query",
                "123",
                "token",
                results,
                lookback=10,
                collect_series_detail=True,
                adaptive_metrics={"scrape_samples_scraped"},
            )

        queries = [call.kwargs["params"]["query"] for call in request.call_args_list]
        self.assertIn('__aggregation__!="none"', queries[0])
        self.assertIn('__ignore_usage__=""', queries[0])
        self.assertIn('__aggregation__!="none"', queries[1])

        payload, _ = results.get_nowait()
        self.assertEqual(payload["scrape_samples_scraped"]["dpm"], 2.0)
        self.assertEqual(
            payload["scrape_samples_scraped"]["series_detail"][0]["labels"],
            {"__aggregation__": "sum", "cluster": "prod"},
        )

    def test_retries_explicit_adaptive_metrics_422_against_stored_series(self):
        adaptive_error = http_error(
            422,
            {
                "status": "error",
                "errorType": "execution",
                "error": (
                    "Can't query aggregated metric scrape_samples_scraped without aggregation "
                    "because labels are aggregated"
                ),
            },
        )
        responses = [
            adaptive_error,
            FakeResponse({"data": {"result": [{"metric": {}, "value": [1, "1.0"]}]}}),
            FakeResponse({"data": {"result": [{"value": [1, "2"]}]}}),
        ]
        results = queue.Queue()

        response_iter = iter(responses)
        with self.assertLogs(dpm_finder.logger, level="INFO") as logs:
            with patch.object(
                dpm_finder, "make_request_with_retry", side_effect=lambda *args, **kwargs: next(response_iter)
            ) as request:
                dpm_finder.process_metric_chunk(
                    ["scrape_samples_scraped"],
                    "https://prometheus.example.net/api/prom/api/v1/query",
                    "123",
                    "token",
                    results,
                    adaptive_metrics=set(),
                )

        queries = [call.kwargs["params"]["query"] for call in request.call_args_list]
        self.assertNotIn('__aggregation__!="none"', queries[0])
        self.assertIn('__aggregation__!="none"', queries[1])
        self.assertIn('__aggregation__!="none"', queries[2])
        self.assertIn("Adaptive Metrics aggregation detected", "\n".join(logs.output))
        self.assertIn("scrape_samples_scraped", results.get_nowait()[0])

    def test_does_not_treat_an_unrelated_422_as_adaptive_metrics(self):
        unrelated_error = http_error(
            422,
            {"status": "error", "errorType": "execution", "error": "invalid parameter"},
        )
        results = queue.Queue()
        response_iter = iter([unrelated_error])

        with self.assertLogs(dpm_finder.logger, level="WARNING") as logs:
            with patch.object(
                dpm_finder, "make_request_with_retry", side_effect=lambda *args, **kwargs: next(response_iter)
            ) as request:
                dpm_finder.process_metric_chunk(
                    ["broken_metric"],
                    "https://prometheus.example.net/api/prom/api/v1/query",
                    "123",
                    "token",
                    results,
                    adaptive_metrics=set(),
                )

        self.assertEqual(request.call_count, 1)
        self.assertEqual(results.get_nowait()[0], {})
        self.assertIn("unrelated Prometheus 422", "\n".join(logs.output))


class AdaptiveMetricPermissionTests(unittest.TestCase):
    def test_missing_rules_permission_names_required_scope(self):
        permission_error = http_error(403, {"status": "error", "error": "access denied"})

        with self.assertLogs(dpm_finder.logger, level="ERROR") as logs:
            with patch.object(dpm_finder, "make_request_with_retry", return_value=permission_error):
                result = dpm_finder.get_adaptive_metrics_rules(
                    "https://prometheus.example.net/aggregations/rules",
                    "123",
                    "token",
                )

        self.assertIsNone(result)
        message = "\n".join(logs.output)
        self.assertIn("Adaptive Metrics aggregation rules", message)
        self.assertIn("adaptive-metrics-rules:read", message)
        self.assertIn("continuing with 422 fallback detection", message)


if __name__ == "__main__":
    unittest.main()
