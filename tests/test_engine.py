"""Unit tests for the comparison engine.

The engine is the product, so these tests pin the classification rules, not the
rendering. The most important one is that a defect recorded at pre-use can never
come out as new damage.
"""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from fleet.build import build
from fleet.engine import (
    CLEARED,
    CONDITION_DEGRADED,
    EXISTING_DAMAGE,
    MISSING_BASELINE,
    MISSING_ITEM,
    NEEDS_REVIEW,
    NEW_DAMAGE,
    RESOLVED,
    UNCHANGED,
    MissingEvidence,
    PhotoRef,
    classify,
    compare,
)
from fleet.model import TemplateItem

NOW = datetime.fromisoformat("2026-11-15T12:00:00-05:00")


def _item(**overrides) -> TemplateItem:
    base = dict(
        id="T-01",
        label="Test item",
        kind="boolean",
        critical=False,
        guidance="",
    )
    base.update(overrides)
    return TemplateItem(**base)


class ClassifyTests(unittest.TestCase):
    def test_pre_existing_failure_is_existing_damage_not_new(self):
        self.assertEqual(classify(_item(), "fail", "fail", None, None), EXISTING_DAMAGE)

    def test_pass_to_fail_is_new_damage(self):
        self.assertEqual(classify(_item(), "pass", "fail", None, None), NEW_DAMAGE)

    def test_fail_to_pass_is_cleared(self):
        self.assertEqual(classify(_item(), "fail", "pass", None, None), CLEARED)

    def test_missing_post_result_is_missing_item(self):
        self.assertEqual(classify(_item(), "pass", "missing", None, None), MISSING_ITEM)
        self.assertEqual(classify(_item(), "pass", "not_checked", None, None), MISSING_ITEM)

    def test_missing_pre_result_is_missing_baseline(self):
        self.assertEqual(classify(_item(), "missing", "fail", None, None), MISSING_BASELINE)

    def test_graded_change_at_threshold_degrades(self):
        graded = _item(kind="graded", scale_min=1, scale_max=5, fail_at_or_below=2, change_threshold=2)
        self.assertEqual(classify(graded, "pass", "pass", 5, 3), CONDITION_DEGRADED)
        self.assertEqual(classify(graded, "pass", "pass", 5, 4), UNCHANGED)
        self.assertEqual(classify(graded, "pass", "fail", 4, 2), NEW_DAMAGE)

    def test_graded_fail_to_fail_is_existing_even_if_worse(self):
        graded = _item(kind="graded", scale_min=1, scale_max=5, fail_at_or_below=2, change_threshold=2)
        self.assertEqual(classify(graded, "fail", "fail", 2, 1), EXISTING_DAMAGE)


class SeedComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="fleet-engine-")
        cls.result = build(out_dir=Path(cls.temp.name), now=NOW, write=False)
        cls.registry = cls.result.registry
        cls.report = cls.result.report

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_seed_stats(self):
        stats = self.report.stats
        self.assertEqual(stats.assets_total, 7)
        self.assertEqual(stats.comparable_assets, 4)
        self.assertEqual(stats.not_inspected_assets, 3)
        self.assertEqual(stats.findings_total, 8)
        self.assertEqual(stats.findings_resolved, 6)
        self.assertEqual(stats.findings_needs_review, 2)
        self.assertEqual(stats.new_damage, 5)
        self.assertEqual(stats.existing_damage, 1)
        self.assertEqual(stats.condition_degraded, 1)
        self.assertEqual(stats.newly_unroadworthy, 2)

    def test_trl_1042_three_named_failures_are_new_damage(self):
        comparison = self.report.comparison_for("TRL-1042")
        self.assertIsNotNone(comparison)
        self.assertEqual(
            sorted(f.item_id for f in comparison.new_damage),
            ["TRL-BED-07", "TRL-BRK-02", "TRL-LGT-04"],
        )
        self.assertTrue(all(f.status == RESOLVED for f in comparison.new_damage))
        self.assertEqual(comparison.newly_unroadworthy, ("TRL-BRK-02",))
        self.assertEqual(comparison.recommended_status, "out_of_service")

    def test_trl_2087_existing_damage_never_becomes_new(self):
        comparison = self.report.comparison_for("TRL-2087")
        self.assertIsNotNone(comparison)
        existing = [f for f in comparison.findings if f.category == EXISTING_DAMAGE]
        self.assertEqual([f.item_id for f in existing], ["TRL-BED-07"])
        self.assertFalse([f for f in comparison.new_damage if f.item_id == "TRL-BED-07"])
        self.assertEqual([d.item_id for d in comparison.cleared], ["TRL-LGT-04"])
        self.assertEqual(comparison.recommended_status, "available")

    def test_power_unit_condition_graded(self):
        comparison = self.report.comparison_for("PU-3312")
        self.assertIsNotNone(comparison)
        degraded = [f for f in comparison.findings if f.category == CONDITION_DEGRADED]
        self.assertEqual([f.item_id for f in degraded], ["PU-CAB-06"])
        self.assertEqual((degraded[0].pre_grade, degraded[0].post_grade), (5, 3))
        self.assertEqual([f.item_id for f in comparison.new_damage], ["PU-HRN-07"])
        self.assertEqual(comparison.recommended_status, "release_with_damage")

    def test_equipment_missing_result_and_missing_photo(self):
        comparison = self.report.comparison_for("EQ-5510")
        self.assertIsNotNone(comparison)
        by_item = {f.item_id: f for f in comparison.findings}
        self.assertEqual(by_item["EQ-PIN-03"].category, MISSING_ITEM)
        self.assertEqual(by_item["EQ-HYD-01"].category, NEW_DAMAGE)
        self.assertTrue(by_item["EQ-HYD-01"].newly_unroadworthy)
        self.assertTrue(all(f.status == NEEDS_REVIEW for f in comparison.findings))
        self.assertEqual(comparison.recommended_status, "out_of_service")

    def test_not_inspected_reasons(self):
        entries = {entry.asset.id: entry for entry in self.report.not_inspected}
        self.assertEqual(sorted(entries), ["EQ-6604", "EQ-7711", "TRL-3120"])
        self.assertEqual(entries["EQ-6604"].present_sides, ("pre_use",))
        self.assertIn("no post-use return inspection", entries["EQ-6604"].reason)
        self.assertEqual(entries["EQ-7711"].present_sides, ("post_use",))
        self.assertIn("no pre-use baseline", entries["EQ-7711"].reason)
        self.assertEqual(entries["TRL-3120"].present_sides, ())
        self.assertIn("no inspection record", entries["TRL-3120"].reason)

    def test_period_boundary_excludes_out_of_period_inspection(self):
        early_period = replace(
            self.registry.period,
            ends_at=datetime.fromisoformat("2026-11-12T00:00:00-05:00"),
        )
        registry = replace(self.registry, period=early_period)
        report = compare(registry, generated_at=NOW)
        # TRL-1042's post-use inspection is on 12 Nov at 17:05, outside the period.
        self.assertIsNone(report.comparison_for("TRL-1042"))
        entry = report.not_inspected_for("TRL-1042")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.present_sides, ("pre_use",))

    def test_compare_without_evidence_resolves_nothing_silently(self):
        report = compare(self.registry, generated_at=NOW)
        for comparison in report.comparisons:
            for finding in comparison.findings:
                self.assertEqual(finding.status, NEEDS_REVIEW)
                self.assertTrue(finding.reason)


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="fleet-evidence-")
        cls.result = build(out_dir=Path(cls.temp.name), now=NOW, write=False)
        cls.registry = cls.result.registry
        cls.report = cls.result.report

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_finding_with_unavailable_photo_needs_review(self):
        registry = self.registry
        # Resolve every photo to unavailable: nothing that needs a photo may resolve.
        report = compare(registry, MissingEvidence(), generated_at=NOW)
        comparison = report.comparison_for("TRL-1042")
        self.assertTrue(all(f.status == NEEDS_REVIEW for f in comparison.findings))
        for finding in comparison.findings:
            self.assertTrue(finding.reason)
            self.assertNotIn("sha256", finding.basis)

    def test_existing_damage_requires_pre_photo(self):
        registry = self.registry
        real = compare(registry, generated_at=NOW)
        self.assertIsNotNone(real.comparison_for("TRL-2087"))
        # A fake evidence source that has the post photo but not the pre photo.
        class PartialEvidence:
            def ref(self, photo):
                available = photo.id != "PHO-2087-PRE-BED"
                return PhotoRef(
                    photo_id=photo.id,
                    view=photo.view,
                    caption=photo.caption,
                    captured_at=photo.captured_at,
                    available=available,
                    sha256="0" * 64 if available else None,
                    filename=f"{photo.id}.png",
                )

        report = compare(registry, PartialEvidence(), generated_at=NOW)
        comparison = report.comparison_for("TRL-2087")
        finding = [f for f in comparison.findings if f.item_id == "TRL-BED-07"][0]
        self.assertEqual(finding.status, NEEDS_REVIEW)
        self.assertIn("missing or unreadable", finding.reason or "")


if __name__ == "__main__":
    unittest.main()
