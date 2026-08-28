import json
import queue
import subprocess
import unittest
from unittest.mock import patch

from dpm_test_module import dpm_finder


def completed(payload, stderr=""):
    return subprocess.CompletedProcess([], 0, json.dumps(payload), stderr)


class GCXClientTests(unittest.TestCase):
    def test_builds_portable_argv_with_context_and_datasource(self):
        client = dpm_finder.GCXClient(
            binary="/opt/GCX Tools/gcx", context="robk", datasource="prom-uid", timeout=17
        )

        with patch.object(subprocess, "run", return_value=completed({"status": "success"})) as run:
            response = client.query('sum(rate(requests_total[5m]))')

        self.assertEqual(response.json(), {"status": "success"})
        run.assert_called_once_with(
            [
                "/opt/GCX Tools/gcx",
                "metrics",
                "query",
                'sum(rate(requests_total[5m]))',
                "--context",
                "robk",
                "--datasource",
                "prom-uid",
                "-o",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=17,
            check=False,
        )

    def test_lists_every_metric_name(self):
        client = dpm_finder.GCXClient(context="robk")
        payload = {"data": ["first_metric", "second_metric"]}

        with patch.object(subprocess, "run", return_value=completed(payload)) as run:
            self.assertEqual(client.list_metric_names(), payload)

        self.assertIn("--limit", run.call_args.args[0])
        self.assertIn("0", run.call_args.args[0])

    def test_lists_every_adaptive_metrics_rule(self):
        client = dpm_finder.GCXClient(context="robk")
        rules = [{"metric": "requests_total", "match_type": "exact"}]

        with patch.object(subprocess, "run", return_value=completed(rules)) as run:
            self.assertEqual(client.list_adaptive_metrics_rules(), rules)

        self.assertIn("--limit", run.call_args.args[0])
        self.assertIn("0", run.call_args.args[0])

    def test_missing_binary_returns_actionable_error(self):
        client = dpm_finder.GCXClient(binary="missing-gcx")

        with patch.object(subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(dpm_finder.GCXCommandError, "missing-gcx.*PATH"):
                client.list_metric_names()

    def test_nonzero_exit_preserves_error_without_exposing_an_argv_shell(self):
        client = dpm_finder.GCXClient()
        result = subprocess.CompletedProcess([], 1, "", "Error: authentication expired")

        with patch.object(subprocess, "run", return_value=result):
            with self.assertRaisesRegex(dpm_finder.GCXCommandError, "authentication expired"):
                client.query("up")

    def test_malformed_json_is_reported(self):
        client = dpm_finder.GCXClient()
        result = subprocess.CompletedProcess([], 0, "not-json", "")

        with patch.object(subprocess, "run", return_value=result):
            with self.assertRaisesRegex(dpm_finder.GCXCommandError, "invalid JSON"):
                client.query("up")

    def test_gcx_adaptive_error_retries_stored_series(self):
        client = unittest.mock.Mock()
        client.query.side_effect = [
            dpm_finder.GCXCommandError(
                "Can't query aggregated metric scrape_samples_scraped without aggregation"
            ),
            dpm_finder.JSONResponse(
                {"data": {"result": [{"metric": {}, "value": [1, "2.0"]}]}}
            ),
            dpm_finder.JSONResponse(
                {"data": {"result": [{"metric": {}, "value": [1, "4"]}]}}
            ),
        ]
        results = queue.Queue()

        dpm_finder.process_metric_chunk(
            ["scrape_samples_scraped"],
            None,
            None,
            None,
            results,
            gcx_client=client,
        )

        self.assertNotIn('__aggregation__!="none"', client.query.call_args_list[0].args[0])
        self.assertIn('__aggregation__!="none"', client.query.call_args_list[1].args[0])
        self.assertIn("scrape_samples_scraped", results.get_nowait()[0])


if __name__ == "__main__":
    unittest.main()
