"""Tests for src/compliance.py — expiry calc, RAG bucketing, action list."""
import unittest
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SCHEMAS  = os.path.join(os.path.dirname(__file__), "..", "schemas")

from src.ingest import load_csv
from src.compliance import compute_compliance_report


class TestComplianceExpiry(unittest.TestCase):

    def setUp(self):
        df = load_csv(os.path.join(FIXTURES, "compliance_good.csv"))
        # Reference date: 2026-04-16
        self.report = compute_compliance_report(df, SCHEMAS, as_of=date(2026, 4, 16))

    def test_returns_rows(self):
        self.assertIn("rows", self.report)
        self.assertGreater(len(self.report["rows"]), 0)

    def test_days_to_expiry_calculated(self):
        # Cebu Business Permit expires 2026-04-10, as_of 2026-04-16 → -6 days (already expired)
        cebu_bp = next(r for r in self.report["rows"]
                       if r["hub"] == "Cebu" and r["permit_type"] == "Business Permit")
        self.assertEqual(cebu_bp["days_to_expiry"], -6)

    def test_arayat_bfp_expires_in_4_days(self):
        # Arayat BFP Certificate: 2026-04-20, as_of 2026-04-16 → 4 days
        ara_bfp = next(r for r in self.report["rows"]
                       if r["hub"] == "Arayat" and r["permit_type"] == "BFP Certificate")
        self.assertEqual(ara_bfp["days_to_expiry"], 4)

    def test_expired_is_red(self):
        cebu_bp = next(r for r in self.report["rows"]
                       if r["hub"] == "Cebu" and r["permit_type"] == "Business Permit")
        self.assertEqual(cebu_bp["rag"], "RED")

    def test_expiring_within_30_days_is_red(self):
        ara_bfp = next(r for r in self.report["rows"]
                       if r["hub"] == "Arayat" and r["permit_type"] == "BFP Certificate")
        self.assertEqual(ara_bfp["rag"], "RED")

    def test_expiring_within_60_days_is_amber(self):
        # Arayat Business Permit: 2026-05-01, as_of 2026-04-16 → 15 days — RED
        # Laguna Business Permit: 2026-06-30 → 75 days — GREEN
        lag_bp = next(r for r in self.report["rows"]
                      if r["hub"] == "Laguna" and r["permit_type"] == "Business Permit")
        self.assertEqual(lag_bp["rag"], "GREEN")

    def test_arayat_business_permit_amber(self):
        # Arayat Business Permit expires 2026-05-01 → 15 days → RED
        ara_bp = next(r for r in self.report["rows"]
                      if r["hub"] == "Arayat" and r["permit_type"] == "Business Permit")
        self.assertEqual(ara_bp["rag"], "RED")

    def test_far_expiry_is_green(self):
        cebu_bfp = next(r for r in self.report["rows"]
                        if r["hub"] == "Cebu" and r["permit_type"] == "BFP Certificate")
        self.assertEqual(cebu_bfp["rag"], "GREEN")


class TestComplianceSummary(unittest.TestCase):

    def setUp(self):
        df = load_csv(os.path.join(FIXTURES, "compliance_good.csv"))
        self.report = compute_compliance_report(df, SCHEMAS, as_of=date(2026, 4, 16))

    def test_rag_counts_present(self):
        self.assertIn("red_count", self.report)
        self.assertIn("amber_count", self.report)
        self.assertIn("green_count", self.report)

    def test_red_count_correct(self):
        # Expired/expiring <30d: Cebu BP (expired), Arayat BFP (4d), Arayat BP (15d)
        self.assertGreaterEqual(self.report["red_count"], 3)

    def test_action_list_sorted_by_urgency(self):
        actions = self.report["action_list"]
        if len(actions) > 1:
            days = [a["days_to_expiry"] for a in actions]
            self.assertEqual(days, sorted(days))

    def test_action_list_excludes_green(self):
        for action in self.report["action_list"]:
            self.assertNotEqual(action["rag"], "GREEN")

    def test_total_permits_count(self):
        self.assertEqual(self.report["total_permits"], 12)


if __name__ == "__main__":
    unittest.main()
