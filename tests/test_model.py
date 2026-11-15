"""Seed validation tests: the loader refuses inconsistent data."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fleet.model import SeedError, load_registry

DATA = Path(__file__).resolve().parent.parent / "data"


class SeedValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fleet-seed-")
        self.data_dir = Path(self.temp.name) / "data"
        shutil.copytree(DATA, self.data_dir)

    def tearDown(self):
        self.temp.cleanup()

    def _mutate(self, filename: str, mutate) -> None:
        path = self.data_dir / filename
        document = json.loads(path.read_text(encoding="utf-8"))
        mutate(document)
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")

    def test_clean_seed_loads(self):
        registry = load_registry(self.data_dir)
        self.assertEqual(len(registry.assets), 7)
        self.assertEqual(len(registry.inspections), 10)
        self.assertEqual(len(registry.photos), 12)
        self.assertEqual(len(registry.templates), 3)

    def test_duplicate_asset_id_is_refused(self):
        def mutate(document):
            document["assets"][1]["id"] = document["assets"][0]["id"]

        self._mutate("assets.json", mutate)
        with self.assertRaisesRegex(SeedError, "duplicate id"):
            load_registry(self.data_dir)

    def test_dangling_photo_inspection_is_refused(self):
        def mutate(document):
            document["photos"][0]["inspection_id"] = "INS-DOES-NOT-EXIST"

        self._mutate("inspections.json", mutate)
        with self.assertRaisesRegex(SeedError, "inspection_id"):
            load_registry(self.data_dir)

    def test_graded_result_state_must_match_grade(self):
        def mutate(document):
            for inspection in document["inspections"]:
                if inspection["id"] == "INS-2026-11-11-TRL1042-PRE":
                    for result in inspection["results"]:
                        if result["item_id"] == "TRL-BED-07":
                            result["grade"] = 2  # fail zone, but state says pass

        self._mutate("inspections.json", mutate)
        with self.assertRaisesRegex(SeedError, "contradicts grade"):
            load_registry(self.data_dir)

    def test_template_class_must_match_asset_class(self):
        def mutate(document):
            document["inspections"][0]["template_id"] = "TPL-PU"

        self._mutate("inspections.json", mutate)
        with self.assertRaisesRegex(SeedError, "template"):
            load_registry(self.data_dir)

    def test_two_records_of_one_side_in_a_cycle_are_refused(self):
        def mutate(document):
            clone = json.loads(json.dumps(document["inspections"][0]))
            clone["id"] = "INS-DUPLICATE-PRE"
            document["inspections"].append(clone)

        self._mutate("inspections.json", mutate)
        with self.assertRaisesRegex(SeedError, "more than one pre_use"):
            load_registry(self.data_dir)

    def test_photo_outside_inspection_window_is_refused(self):
        def mutate(document):
            document["photos"][0]["captured_at"] = "2026-11-11T20:00:00-05:00"

        self._mutate("inspections.json", mutate)
        with self.assertRaisesRegex(SeedError, "outside inspection"):
            load_registry(self.data_dir)


if __name__ == "__main__":
    unittest.main()
