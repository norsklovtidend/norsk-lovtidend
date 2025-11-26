from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from lovtidend import make_start_url


class StartUrlTest(unittest.TestCase):
    def test_defaults_added_for_year(self) -> None:
        url = make_start_url(
            "https://lovdata.no/register/lovtidend",
            start_url=None,
            offset=None,
            year=1982,
        )
        self.assertIsNotNone(url)
        parsed = urlparse(url or "")
        query = parse_qs(parsed.query, keep_blank_values=True)
        self.assertEqual(query["year"], ["1982"])
        self.assertEqual(query["avdeling"], ["*"])
        self.assertEqual(query["ministry"], ["*"])
        self.assertEqual(query["kunngjortDato"], ["*"])
        self.assertEqual(query["search"], [""])

    def test_defaults_added_for_offset(self) -> None:
        url = make_start_url(
            "https://lovdata.no/register/lovtidend",
            start_url=None,
            offset=40,
            year=None,
        )
        parsed = urlparse(url or "")
        query = parse_qs(parsed.query, keep_blank_values=True)
        self.assertEqual(query["offset"], ["40"])
        self.assertEqual(query["avdeling"], ["*"])
        self.assertEqual(query["ministry"], ["*"])
        self.assertEqual(query["kunngjortDato"], ["*"])
        self.assertEqual(query["search"], [""])

    def test_explicit_start_url_passes_through(self) -> None:
        explicit = "https://example.com/register?year=1999&offset=10&search=foo"
        url = make_start_url(
            "https://lovdata.no/register/lovtidend",
            start_url=explicit,
            offset=None,
            year=None,
        )
        self.assertEqual(url, explicit)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
