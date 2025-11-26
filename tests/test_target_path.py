from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lovtidend.scraper import LovtidendScraper


class TargetPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_places_files_under_year_from_filename(self) -> None:
        scraper = LovtidendScraper(self.root, cache_dir=None, delay_range=(0, 0))
        try:
            target = scraper._target_path("https://example.com/xml/LTI/sf-19821209-1673.xml")
        finally:
            scraper.close()

        self.assertEqual(target, self.root / "1982" / "LTI" / "sf-19821209-1673.xml")

    def test_keeps_existing_year_folder(self) -> None:
        scraper = LovtidendScraper(self.root, cache_dir=None, delay_range=(0, 0))
        try:
            target = scraper._target_path("https://example.com/xml/2024/LTI/sf-2024-01.xml")
        finally:
            scraper.close()

        self.assertEqual(target, self.root / "2024" / "LTI" / "sf-2024-01.xml")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
