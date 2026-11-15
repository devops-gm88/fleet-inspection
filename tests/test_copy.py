"""Copy discipline: no regulatory claim without a quotation and a citation.

The demo makes no regulatory claim at all, so the only allowed regulatory words
appear inside the plain denial on every page. This test also proves the checker
itself can fail, so it is not a test that always passes.
"""
from __future__ import annotations

import unittest

from fleet.verify import CITATION, REGULATORY


class CopyRuleTests(unittest.TestCase):
    def test_denial_is_allowed(self):
        line = "Demonstration build - fictional assets, synthetic photos. No compliance or regulatory claim is made."
        self.assertIsNotNone(REGULATORY.search(line))
        self.assertIsNotNone(CITATION.search(line))

    def test_uncited_claim_is_flagged(self):
        line = "This inspection is required by law and is compliant with the standard."
        self.assertIsNotNone(REGULATORY.search(line))
        self.assertIsNone(CITATION.search(line))

    def test_cited_claim_is_allowed(self):
        line = 'The rule text says "a report must be prepared" [source: https://example.gov/rule].'
        self.assertIsNotNone(CITATION.search(line))


if __name__ == "__main__":
    unittest.main()
