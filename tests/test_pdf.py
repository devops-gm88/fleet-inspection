"""PDF writer tests: one page per report, text extractable, images embedded."""
from __future__ import annotations

import unittest

from fleet import pdf


class PdfWriterTests(unittest.TestCase):
    def _document(self):
        document = pdf.Document()
        page = document.add_page()
        page.text(40, 40, 12, "Handover comparison (TRL-1042)")
        page.text_right(560, 40, 8, "page 1")
        page.rect(40, 60, 200, 20, pdf.TEAL)
        page.line(40, 90, 560, 90)
        page.wrapped(40, 100, 200, 9, "A wrapped paragraph that must fit inside a narrow column.", pdf.GREY)
        rgb = bytes([255, 0, 0] * 4)
        document.add_image(2, 2, rgb)
        page.image(document.add_image(2, 2, rgb), 400, 100, 40, 40)
        return document

    def test_single_page(self):
        data = self._document().build()
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertEqual(pdf.count_pages(data), 1)

    def test_text_is_extractable(self):
        data = self._document().build()
        text = pdf.extract_text(data)
        self.assertIn("Handover comparison (TRL-1042)", text)
        self.assertIn("page 1", text)
        self.assertIn("A wrapped paragraph", text)

    def test_images_are_embedded(self):
        data = self._document().build()
        self.assertIn(b"/Subtype /Image", data)
        self.assertIn(b"/Filter /FlateDecode", data)

    def test_unicode_is_folded_to_ascii(self):
        document = pdf.Document()
        page = document.add_page()
        page.text(40, 40, 10, "What changed — 3 findings · grade 4 -> 2")
        text = pdf.extract_text(document.build())
        self.assertIn("What changed - 3 findings - grade 4 -> 2", text)
        self.assertNotIn("?", text)

    def test_wrap_never_exceeds_width(self):
        text = "The quick brown fox jumps over the lazy dog and keeps running for several more words."
        lines = pdf.wrap_text(text, 180, 9)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(pdf.text_width(line, 9), 180.001)

    def test_two_pages_count(self):
        document = pdf.Document()
        document.add_page().text(40, 40, 10, "one")
        document.add_page().text(40, 40, 10, "two")
        data = document.build()
        self.assertEqual(pdf.count_pages(data), 2)
        self.assertIn("one", pdf.extract_text(data))
        self.assertIn("two", pdf.extract_text(data))


if __name__ == "__main__":
    unittest.main()
