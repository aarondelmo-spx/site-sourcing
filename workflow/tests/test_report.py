"""Tests for src/report.py — Word doc generation from processed data."""
import unittest
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SCHEMAS  = os.path.join(os.path.dirname(__file__), "..", "schemas")

from src.ingest import load_csv
from src.kpi import compute_kpi_report
from src.compliance import compute_compliance_report
from src.hse import compute_hse_report
from src.report import generate_report


class TestReportGeneration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        as_of = date(2026, 4, 16)
        kpi_df  = load_csv(os.path.join(FIXTURES, "kpi_good.csv"))
        comp_df = load_csv(os.path.join(FIXTURES, "compliance_good.csv"))
        hse_df  = load_csv(os.path.join(FIXTURES, "hse_good.csv"))

        cls.kpi_report  = compute_kpi_report(kpi_df, SCHEMAS)
        cls.comp_report = compute_compliance_report(comp_df, SCHEMAS, as_of=as_of)
        cls.hse_report  = compute_hse_report(hse_df, SCHEMAS, as_of=as_of)

        cls.tmp = tempfile.mktemp(suffix=".docx")
        generate_report(
            kpi_report=cls.kpi_report,
            compliance_report=cls.comp_report,
            hse_report=cls.hse_report,
            output_path=cls.tmp,
            as_of=as_of,
        )

    def test_output_file_created(self):
        self.assertTrue(os.path.exists(self.tmp))

    def test_output_file_nonzero(self):
        self.assertGreater(os.path.getsize(self.tmp), 5000)

    def test_output_is_valid_docx(self):
        import zipfile
        self.assertTrue(zipfile.is_zipfile(self.tmp))

    def test_docx_contains_word_document(self):
        import zipfile
        with zipfile.ZipFile(self.tmp) as z:
            names = z.namelist()
        self.assertIn("word/document.xml", names)

    def test_report_contains_kpi_section_text(self):
        import zipfile
        with zipfile.ZipFile(self.tmp) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        self.assertIn("KPI", xml)

    def test_report_contains_compliance_section(self):
        import zipfile
        with zipfile.ZipFile(self.tmp) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        self.assertIn("Compliance", xml)

    def test_report_contains_hse_section(self):
        import zipfile
        with zipfile.ZipFile(self.tmp) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        self.assertIn("HSE", xml)

    def test_report_contains_date(self):
        import zipfile
        with zipfile.ZipFile(self.tmp) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        self.assertIn("2026", xml)

    def test_report_contains_hub_name(self):
        import zipfile
        with zipfile.ZipFile(self.tmp) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        self.assertIn("Cebu", xml)

    def test_generate_to_nonexistent_dir_raises(self):
        with self.assertRaises(Exception):
            generate_report(
                kpi_report=self.kpi_report,
                compliance_report=self.comp_report,
                hse_report=self.hse_report,
                output_path="/nonexistent/path/report.docx",
                as_of=date(2026, 4, 16),
            )


if __name__ == "__main__":
    unittest.main()
