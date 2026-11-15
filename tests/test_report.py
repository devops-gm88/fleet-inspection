"""Report tests: one page, item names, inspector, timestamp, not-inspected list."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fleet import pdf
from fleet.build import DemoResult, build

NOW = datetime.fromisoformat("2026-11-15T12:00:00-05:00")


def _norm(text: str) -> str:
    return " ".join(text.split())


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="fleet-report-")
        cls.result: DemoResult = build(out_dir=Path(cls.temp.name), now=NOW, write=False)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_eight_reports_all_one_page(self):
        self.assertEqual(len(self.result.rendered), 8)
        for rendered in self.result.rendered:
            self.assertEqual(rendered.page_count, 1, rendered.filename)
            self.assertEqual(pdf.count_pages(rendered.pdf_bytes), 1)

    def test_trl_1042_names_items_inspector_timestamp(self):
        text = _norm(self.result.report_for("TRL-1042").text)
        for expected in (
            "TRL-BRK-02",
            "TRL-LGT-04",
            "TRL-BED-07",
            "Air brake system leak-down within limit",
            "Rear marker lamps, brake lights and turn signals",
            "Deck boards, cross-members and fasteners",
            "Marcus Hale",
            "Elena Duarte",
            "12 Nov 2026 17:24 (-05:00)",
            "RESOLVED",
            "item + photo",
            "OUT OF SERVICE",
        ):
            self.assertIn(expected, text)

    def test_photo_digests_appear_on_the_page(self):
        comparison = self.result.report.comparison_for("TRL-1042")
        self.assertIsNotNone(comparison)
        text = _norm(self.result.report_for("TRL-1042").text)
        digests = [
            photo.short_digest
            for finding in comparison.findings
            for photo in finding.photos_pre + finding.photos_post
        ]
        self.assertTrue(digests)
        for digest in digests[:3]:
            self.assertIn(digest, text)

    def test_existing_damage_language(self):
        text = _norm(self.result.report_for("TRL-2087").text)
        self.assertIn("pre-existing, recorded at pre-use", text)
        self.assertIn("never counted as new damage", text)

    def test_needs_review_page(self):
        text = _norm(self.result.report_for("EQ-5510").text)
        self.assertIn("NEEDS REVIEW", text)
        self.assertIn("no photo is attached to item EQ-HYD-01", text)
        self.assertIn("no post-use result was recorded", text)

    def test_missing_post_use_page(self):
        text = _norm(self.result.report_for("EQ-6604").text)
        self.assertIn("This asset cannot be compared this period", text)
        self.assertIn("no post-use return inspection", text)
        self.assertIn("MISSING", text)
        self.assertIn("What the pre-use record shows", text)

    def test_no_inspection_page(self):
        text = _norm(self.result.report_for("TRL-3120").text)
        self.assertIn("no inspection record", text)
        self.assertIn("nothing was recorded", text)

    def test_not_inspected_list_on_every_report(self):
        for rendered in self.result.rendered:
            text = _norm(rendered.text)
            for asset_id in ("EQ-6604", "EQ-7711", "TRL-3120"):
                self.assertIn(asset_id, text, f"{asset_id} missing from {rendered.filename}")
            self.assertIn("Not inspected this period", text)

    def test_fleet_summary_lists_all_assets(self):
        text = _norm(self.result.report_for("FLEET").text)
        for asset_id in ("TRL-1042", "TRL-2087", "PU-3312", "EQ-5510", "EQ-6604", "EQ-7711", "TRL-3120"):
            self.assertIn(asset_id, text)
        self.assertIn("NEEDS REVIEW", text)
        self.assertIn("inspector Marcus Hale", text)

    def test_render_is_deterministic(self):
        with tempfile.TemporaryDirectory(prefix="fleet-report-again-") as temp:
            again = build(out_dir=Path(temp), now=NOW, write=False)
            for first, second in zip(self.result.rendered, again.rendered):
                self.assertEqual(first.pdf_bytes, second.pdf_bytes, first.filename)


if __name__ == "__main__":
    unittest.main()
