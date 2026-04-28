"""Tests for src/hse.py — incident trends, rolling average, overdue actions."""
import unittest
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SCHEMAS  = os.path.join(os.path.dirname(__file__), "..", "schemas")

from src.ingest import load_csv
from src.hse import compute_hse_report


class TestHSETrends(unittest.TestCase):

    def setUp(self):
        df = load_csv(os.path.join(FIXTURES, "hse_good.csv"))
        self.report = compute_hse_report(df, SCHEMAS, as_of=date(2026, 4, 16))

    def test_returns_expected_keys(self):
        for key in ("this_week_counts", "rolling_avg", "overdue_actions",
                    "total_this_week", "overdue_count", "weeks_analyzed"):
            self.assertIn(key, self.report)

    def test_this_week_counts_by_type(self):
        # Week of 2026-04-14: MTC=2 (Cebu, Davao), FAC=1 (Laguna), NM=1 (Arayat)
        counts = self.report["this_week_counts"]
        self.assertEqual(counts.get("MTC", 0), 2)
        self.assertEqual(counts.get("FAC", 0), 1)
        self.assertEqual(counts.get("NM", 0), 1)

    def test_total_this_week(self):
        self.assertEqual(self.report["total_this_week"], 4)

    def test_rolling_avg_present_for_each_type(self):
        for t in ("MTC", "FAC", "NM"):
            self.assertIn(t, self.report["rolling_avg"])

    def test_rolling_avg_is_float(self):
        for val in self.report["rolling_avg"].values():
            self.assertIsInstance(val, float)

    def test_weeks_analyzed_correct(self):
        # Fixture covers week_dates: 2026-03-24, 2026-03-31, 2026-04-07, 2026-04-14 = 4 weeks
        self.assertEqual(self.report["weeks_analyzed"], 4)


class TestHSEOverdueActions(unittest.TestCase):

    def setUp(self):
        df = load_csv(os.path.join(FIXTURES, "hse_good.csv"))
        self.report = compute_hse_report(df, SCHEMAS, as_of=date(2026, 4, 16))

    def test_overdue_actions_returned(self):
        self.assertIsInstance(self.report["overdue_actions"], list)

    def test_overdue_count_correct(self):
        # Status='Overdue' rows: Cebu NM 2026-03-25 (due 2026-04-08, overdue)
        # Also check: Open rows where due_date + grace_days < as_of
        # Laguna MTC 2026-03-30 due 2026-04-13 → as_of 2026-04-16, grace=7 → 2026-04-20 > 2026-04-16 → NOT overdue by grace
        # Cebu FAC 2026-03-31 due 2026-04-14 → grace → 2026-04-21 > as_of → not overdue
        # Davao NM 2026-04-01 due 2026-04-08 → CLOSED, skip
        # Arayat MTC 2026-04-06 due 2026-04-20 → not overdue
        # Laguna NM 2026-04-07 due 2026-04-14 → grace → 2026-04-21 > as_of → not overdue
        # Cebu MTC 2026-04-07 due 2026-04-21 → not overdue
        # Davao FAC 2026-04-08 due 2026-04-15 → grace → 2026-04-22 > as_of → not overdue
        # Arayat NM 2026-04-14 due 2026-04-16 → grace → 2026-04-23 > as_of → not overdue
        # Davao MTC 2026-04-15 due 2026-04-22 → not overdue
        # Arayat NM 2026-04-10 due 2026-04-12 → CLOSED, skip
        # Cebu HSE 2026-04-13 due 2026-04-20 → not overdue
        # Laguna FAC 2026-04-14 due 2026-04-21 → not overdue
        # Overdue: Cebu NM (status=Overdue, due 2026-04-08 — past grace too)
        self.assertGreaterEqual(self.report["overdue_count"], 1)

    def test_closed_actions_excluded_from_overdue(self):
        overdue_descs = [a["description"] for a in self.report["overdue_actions"]]
        # "Electrical socket sparking" → Closed → should NOT appear
        self.assertNotIn("Electrical socket sparking near conveyor", overdue_descs)

    def test_overdue_includes_status_overdue_row(self):
        overdue_descs = [a["description"] for a in self.report["overdue_actions"]]
        self.assertIn("Forklift near-miss in sorting area", overdue_descs)

    def test_overdue_actions_have_required_keys(self):
        for action in self.report["overdue_actions"]:
            for key in ("hub", "incident_type", "description", "due_date", "days_overdue"):
                self.assertIn(key, action)

    def test_days_overdue_positive(self):
        for action in self.report["overdue_actions"]:
            self.assertGreater(action["days_overdue"], 0)


if __name__ == "__main__":
    unittest.main()
