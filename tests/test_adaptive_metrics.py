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

    def test_non_list_aggregation_payload_is_ignored_without_crashing(self):
        metric_names = ["exact_metric"]

        with self.assertLogs(dpm_finder.logger, level="WARNING") as logs:
            for malformed_payload in (True, 42, "unexpected", {"metric": "exact_metric"}):
                self.assertEqual(
                    dpm_finder.get_adaptive_metric_names(metric_names, malformed_payload),
                    set(),
                )

        self.assertIn("expected a list", "\n".join(logs.output))

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

    def test_keeps_ambiguous_summary_like_siblings(self):
        metrics = ["rpc_duration_seconds", "rpc_duration_seconds_count", "rpc_duration_seconds_sum"]

        self.assertEqual(
            dpm_finder.filter_metric_names(metrics, include_histograms=False),
            metrics,
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
        self.assertTrue(queries[0].startswith("count_over_time("))
        self.assertIn('__aggregation__!="none"', queries[0])
        self.assertIn('__ignore_usage__=""', queries[0])
        self.assertIn('__aggregation__!="none"', queries[1])

        payload, _ = results.get_nowait()
        self.assertEqual(payload["scrape_samples_scraped"]["dpm"], 2.0)
        self.assertEqual(
            payload["scrape_samples_scraped"]["series_detail"][0]["labels"],
            {"__aggregation__": "sum", "cluster": "prod"},
        )

    def test_no_series_detail_reduces_dpm_on_the_server(self):
        responses = [
            FakeResponse({"data": {"result": [{"metric": {}, "value": [1, "2.0"]}]}}),
            FakeResponse({"data": {"result": [{"value": [1, "2393"]}]}}),
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
                collect_series_detail=False,
                adaptive_metrics={"scrape_samples_scraped"},
            )

        queries = [call.kwargs["params"]["query"] for call in request.call_args_list]
        self.assertTrue(queries[0].startswith("max(count_over_time("))
        self.assertEqual(results.get_nowait()[0]["scrape_samples_scraped"]["dpm"], 2.0)

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


class LabelMatcherTests(unittest.TestCase):
    def test_builds_one_matcher_per_label_value_pair(self):
        matchers = dpm_finder.build_label_matchers(["cluster=acme-corp-prod-01"])

        self.assertEqual(matchers, ['cluster="acme-corp-prod-01"'])

    def test_escapes_quotes_and_backslashes_in_the_value(self):
        matchers = dpm_finder.build_label_matchers(['job=weird"value\\thing'])

        self.assertEqual(matchers, ['job="weird\\"value\\\\thing"'])

    def test_rejects_a_pair_without_an_equals_sign(self):
        with self.assertRaises(ValueError):
            dpm_finder.build_label_matchers(["clustervalue"])

    def test_rejects_an_invalid_label_name(self):
        with self.assertRaises(ValueError):
            dpm_finder.build_label_matchers(["1bad=oops"])

    def test_none_input_builds_no_matchers(self):
        self.assertEqual(dpm_finder.build_label_matchers(None), [])

    def test_selector_includes_extra_matchers_alongside_the_default_ones(self):
        selector = dpm_finder.metric_selector(
            "up", extra_matchers=['cluster="acme-corp-prod-01"']
        )

        self.assertIn('__ignore_usage__=""', selector)
        self.assertIn('cluster="acme-corp-prod-01"', selector)

    def test_process_metric_chunk_scopes_both_queries_to_the_label(self):
        responses = [
            FakeResponse({"data": {"result": [{"metric": {}, "value": [1, "2.0"]}]}}),
            FakeResponse({"data": {"result": [{"value": [1, "5"]}]}}),
        ]
        results = queue.Queue()

        with patch.object(dpm_finder, "make_request_with_retry", side_effect=responses) as request:
            dpm_finder.process_metric_chunk(
                ["up"],
                "https://prometheus.example.net/api/prom/api/v1/query",
                "123",
                "token",
                results,
                adaptive_metrics=set(),
                extra_matchers=['cluster="acme-corp-prod-01"'],
            )

        queries = [call.kwargs["params"]["query"] for call in request.call_args_list]
        self.assertIn('cluster="acme-corp-prod-01"', queries[0])
        self.assertIn('cluster="acme-corp-prod-01"', queries[1])

        payload, _ = results.get_nowait()
        self.assertEqual(payload["up"]["series_count"], "5")

    def test_metric_name_lookup_is_prefiltered_by_the_label_when_matchers_are_given(self):
        names_response = FakeResponse({"data": ["up", "cluster_scoped_metric"]})
        rules_response = FakeResponse([])

        with patch.object(
            dpm_finder, "make_request_with_retry", side_effect=[names_response, rules_response]
        ) as request:
            metric_names, _ = dpm_finder.collect_metric_inventory(
                "https://prometheus.example.net/api/prom/api/v1/label/__name__/values",
                "https://prometheus.example.net/aggregations/rules",
                "123",
                "token",
                extra_matchers=['cluster="acme-corp-prod-01"'],
            )

        self.assertEqual(metric_names, {"data": ["up", "cluster_scoped_metric"]})
        name_lookup_params = request.call_args_list[0].kwargs["params"]
        self.assertEqual(name_lookup_params, {"match[]": '{cluster="acme-corp-prod-01"}'})
        # The Adaptive Metrics rules lookup is not a series query and stays unfiltered.
        self.assertIsNone(request.call_args_list[1].kwargs.get("params"))

    def test_metric_name_lookup_is_unfiltered_without_matchers(self):
        names_response = FakeResponse({"data": ["up"]})
        rules_response = FakeResponse([])

        with patch.object(
            dpm_finder, "make_request_with_retry", side_effect=[names_response, rules_response]
        ) as request:
            dpm_finder.collect_metric_inventory(
                "https://prometheus.example.net/api/prom/api/v1/label/__name__/values",
                "https://prometheus.example.net/aggregations/rules",
                "123",
                "token",
            )

        self.assertIsNone(request.call_args_list[0].kwargs.get("params"))


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
