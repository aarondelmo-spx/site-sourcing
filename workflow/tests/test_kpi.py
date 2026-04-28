"""Tests for src/kpi.py — delta calculation, threshold flagging, top-miss ranking."""
import unittest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SCHEMAS  = os.path.join(os.path.dirname(__file__), "..", "schemas")

from src.ingest import load_csv
from src.kpi import compute_kpi_report


class TestKPIDelta(unittest.TestCase):

    def setUp(self):
        df = load_csv(os.path.join(FIXTURES, "kpi_good.csv"))
        self.report = compute_kpi_report(df, SCHEMAS)

    def test_returns_rows_list(self):
        self.assertIn("rows", self.report)
        self.assertIsInstance(self.report["rows"], list)

    def test_delta_calculated_correctly(self):
        # Cebu SLA Rate: this=79.3, last=88.5 → delta = -9.2
        cebu_sla = next(r for r in self.report["rows"]
                        if r["hub"] == "Cebu" and r["metric"] == "SLA Rate")
        self.assertAlmostEqual(cebu_sla["delta"], -9.2, places=1)

    def test_delta_pct_calculated(self):
        # Cebu SLA Rate: pct = (79.3 - 88.5) / 88.5 * 100 = -10.4%
        cebu_sla = next(r for r in self.report["rows"]
                        if r["hub"] == "Cebu" and r["metric"] == "SLA Rate")
        self.assertAlmostEqual(cebu_sla["delta_pct"], -10.4, places=0)

    def test_positive_delta_no_flag_for_higher_is_better(self):
        davao_sla = next(r for r in self.report["rows"]
                         if r["hub"] == "Davao" and r["metric"] == "SLA Rate")
        self.assertFalse(davao_sla["flagged"])

    def test_negative_delta_flags_higher_is_better_metric(self):
        # Cebu SLA Rate dropped 10.4% — must be flagged
        cebu_sla = next(r for r in self.report["rows"]
                        if r["hub"] == "Cebu" and r["metric"] == "SLA Rate")
        self.assertTrue(cebu_sla["flagged"])

    def test_exception_rate_increase_flags_lower_is_better_metric(self):
        # Cebu Exception Rate: this=5.8, last=4.1 — worse, should flag
        cebu_exc = next(r for r in self.report["rows"]
                        if r["hub"] == "Cebu" and r["metric"] == "Exception Rate")
        self.assertTrue(cebu_exc["flagged"])

    def test_exception_rate_decrease_not_flagged(self):
        # Laguna Exception Rate: this=2.1, last=2.3 — improved
        lag_exc = next(r for r in self.report["rows"]
                       if r["hub"] == "Laguna" and r["metric"] == "Exception Rate")
        self.assertFalse(lag_exc["flagged"])

    def test_rag_red_below_target_threshold(self):
        # Cebu SLA Rate 79.3 vs target 95.0 — RED
        cebu_sla = next(r for r in self.report["rows"]
                        if r["hub"] == "Cebu" and r["metric"] == "SLA Rate")
        self.assertEqual(cebu_sla["rag"], "RED")

    def test_rag_green_above_target(self):
        davao_sla = next(r for r in self.report["rows"]
                         if r["hub"] == "Davao" and r["metric"] == "SLA Rate")
        self.assertEqual(davao_sla["rag"], "GREEN")


class TestTopMisses(unittest.TestCase):

    def setUp(self):
        df = load_csv(os.path.join(FIXTURES, "kpi_good.csv"))
        self.report = compute_kpi_report(df, SCHEMAS)

    def test_top_misses_present(self):
        self.assertIn("top_misses", self.report)

    def test_top_misses_max_5(self):
        self.assertLessEqual(len(self.report["top_misses"]), 5)

    def test_top_misses_are_flagged(self):
        for miss in self.report["top_misses"]:
            self.assertTrue(miss["flagged"])

    def test_top_misses_sorted_by_severity(self):
        misses = self.report["top_misses"]
        if len(misses) > 1:
            severities = [abs(m["delta_pct"]) for m in misses]
            self.assertEqual(severities, sorted(severities, reverse=True))

    def test_cebu_sla_is_top_miss(self):
        top_ids = [(m["hub"], m["metric"]) for m in self.report["top_misses"]]
        self.assertIn(("Cebu", "SLA Rate"), top_ids)

    def test_summary_flag_count(self):
        self.assertIn("flagged_count", self.report)
        self.assertGreater(self.report["flagged_count"], 0)


if __name__ == "__main__":
    unittest.main()
