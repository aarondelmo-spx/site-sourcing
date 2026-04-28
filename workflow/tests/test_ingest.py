"""Tests for src/ingest.py — CSV ingestion and schema validation."""
import unittest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SCHEMAS  = os.path.join(os.path.dirname(__file__), "..", "schemas")

from src.ingest import load_csv, validate_schema, IngestError


class TestLoadCSV(unittest.TestCase):

    def test_load_valid_kpi_csv(self):
        df = load_csv(os.path.join(FIXTURES, "kpi_good.csv"))
        self.assertEqual(len(df), 16)
        self.assertIn("hub", df.columns)

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_csv("/nonexistent/path/file.csv")

    def test_load_empty_path_raises(self):
        with self.assertRaises(ValueError):
            load_csv("")


class TestValidateSchema(unittest.TestCase):

    def test_valid_kpi_passes(self):
        df = load_csv(os.path.join(FIXTURES, "kpi_good.csv"))
        result = validate_schema(df, "kpi", SCHEMAS)
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])

    def test_missing_column_caught(self):
        df = load_csv(os.path.join(FIXTURES, "kpi_missing_col.csv"))
        result = validate_schema(df, "kpi", SCHEMAS)
        self.assertFalse(result["valid"])
        self.assertTrue(any("target" in e for e in result["errors"]))

    def test_bad_numeric_type_caught(self):
        df = load_csv(os.path.join(FIXTURES, "kpi_bad_types.csv"))
        result = validate_schema(df, "kpi", SCHEMAS)
        self.assertFalse(result["valid"])
        self.assertTrue(any("this_week" in e for e in result["errors"]))

    def test_bad_date_type_caught(self):
        df = load_csv(os.path.join(FIXTURES, "kpi_bad_types.csv"))
        result = validate_schema(df, "kpi", SCHEMAS)
        self.assertFalse(result["valid"])
        self.assertTrue(any("week_date" in e for e in result["errors"]))

    def test_valid_compliance_passes(self):
        df = load_csv(os.path.join(FIXTURES, "compliance_good.csv"))
        result = validate_schema(df, "compliance", SCHEMAS)
        self.assertTrue(result["valid"])

    def test_valid_hse_passes(self):
        df = load_csv(os.path.join(FIXTURES, "hse_good.csv"))
        result = validate_schema(df, "hse", SCHEMAS)
        self.assertTrue(result["valid"])

    def test_unknown_schema_raises(self):
        df = load_csv(os.path.join(FIXTURES, "kpi_good.csv"))
        with self.assertRaises(IngestError):
            validate_schema(df, "nonexistent_schema", SCHEMAS)

    def test_result_contains_row_count(self):
        df = load_csv(os.path.join(FIXTURES, "kpi_good.csv"))
        result = validate_schema(df, "kpi", SCHEMAS)
        self.assertEqual(result["row_count"], 16)


if __name__ == "__main__":
    unittest.main()
