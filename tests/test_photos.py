"""The synthetic photo generator must be deterministic and readable by the PDF writer."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from fleet.build import load
from fleet.photos import FOCUS_BOXES, PhotoStore, _marker_position, decode_png, encode_png, render_photo


class PhotoGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load()
        cls.temp = tempfile.TemporaryDirectory(prefix="fleet-photos-")
        cls.store = PhotoStore(Path(cls.temp.name) / "photos")
        cls.manifest = cls.store.generate_all(cls.registry)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def _first(self):
        photo = sorted(self.registry.photos.values(), key=lambda p: p.id)[0]
        inspection = next(i for i in self.registry.inspections if i.id == photo.inspection_id)
        asset = self.registry.asset(inspection.asset_id)
        return photo, inspection, asset

    def test_all_photos_generated(self):
        self.assertEqual(len(self.manifest), 12)
        for photo_id, row in self.manifest.items():
            self.assertTrue((self.store.root / row["file"]).exists(), photo_id)
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")

    def test_generation_is_deterministic(self):
        photo, inspection, asset = self._first()
        first = render_photo(photo, asset, inspection)
        second = render_photo(photo, asset, inspection)
        self.assertEqual(first, second)
        self.assertEqual(hashlib.sha256(first).hexdigest(), self.manifest[photo.id]["sha256"])

    def test_png_roundtrip(self):
        photo, inspection, asset = self._first()
        width, height, pixels = decode_png(render_photo(photo, asset, inspection))
        self.assertEqual((width, height), (360, 270))
        self.assertEqual(len(pixels), width * height * 3)
        # The image is not a single flat colour (a scene was actually drawn).
        first_pixel = pixels[:3]
        self.assertTrue(any(pixels[i : i + 3] != first_pixel for i in range(0, len(pixels), 3_000)))

    def test_encode_decode_is_lossless(self):
        from fleet.photos import Image

        image = Image(width=8, height=4)
        for x in range(8):
            for y in range(4):
                image.set_pixel(x, y, (x * 30, y * 60, 128))
        width, height, pixels = decode_png(encode_png(image))
        self.assertEqual((width, height), (8, 4))
        self.assertEqual(pixels, bytes(image.pixels))

    def test_defect_changes_the_image(self):
        photo, inspection, asset = self._first()
        from dataclasses import replace

        clean = render_photo(replace(photo, defect="none"), asset, inspection)
        damaged = render_photo(replace(photo, defect="crack"), asset, inspection)
        self.assertNotEqual(clean, damaged)

    def test_marker_position_is_stable_per_item_and_view(self):
        box = FOCUS_BOXES["rear"]
        first = _marker_position("TRL-1042", "TRL-LGT-04", "rear", box)
        second = _marker_position("TRL-1042", "TRL-LGT-04", "rear", box)
        other_view = _marker_position("TRL-1042", "TRL-LGT-04", "deck", FOCUS_BOXES["deck"])
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_view)
        self.assertTrue(box[0] <= first[0] < box[0] + box[2])
        self.assertTrue(box[1] <= first[1] < box[1] + box[3])

    def test_asset_classes_have_distinct_scenes(self):
        from dataclasses import replace

        photo, inspection, asset = self._first()
        trailer = render_photo(photo, replace(asset, asset_class="trailer"), inspection)
        equipment = render_photo(photo, replace(asset, asset_class="equipment"), inspection)
        self.assertNotEqual(trailer, equipment)


if __name__ == "__main__":
    unittest.main()
