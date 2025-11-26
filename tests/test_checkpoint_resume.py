"""Tests for checkpoint resume behaviour in the scraper."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from requests.exceptions import RequestException

from lovtidend.scraper import DocumentListing, ListingPage, LovtidendScraper


class DummyScraper(LovtidendScraper):
    """Scraper stub that exposes hooks for testing resume logic."""

    page_url = "https://example.com/register"

    def __init__(
        self,
        output_dir: Path,
        documents: list[DocumentListing],
        *,
        fail_download_ids: set[str] | None = None,
    ):
        cache_dir = output_dir.parent / "cache"
        super().__init__(output_dir, delay_range=(0, 0), cache_dir=cache_dir)
        self._documents = documents
        self.fetch_calls: list[str] = []
        self.download_calls: list[str] = []
        self._fail_download_ids = set(fail_download_ids or set())

    def iter_pages(
        self,
        *,
        start_url: str | None = None,
        max_pages: int | None = None,
        limit: int | None = None,
    ):
        del start_url, max_pages
        count = len(self._documents)
        if limit is not None:
            visible = min(limit, count)
        else:
            visible = count
        truncated = limit is not None and visible < count
        documents = self._documents[:visible]
        next_url = "https://example.com/register?offset=20" if not truncated else None
        resume_url = self.page_url if truncated else next_url
        yield ListingPage(
            number=1,
            documents=list(documents),
            current_url=self.page_url,
            next_url=next_url,
            resume_url=resume_url,
        )

    def fetch_xml_links(self, document: DocumentListing):
        self.fetch_calls.append(document.identifier)
        return [f"https://example.com/xml/{document.identifier}.xml"]

    def download_xml(self, url_or_urls, *, html_fallback_url=None):  # type: ignore[override]
        if isinstance(url_or_urls, str):
            urls = [url_or_urls]
        else:
            urls = list(url_or_urls)
        paths: list[Path] = []
        for url in urls:
            identifier = url.rsplit("/", 1)[-1]
            path = self.output_dir / identifier
            doc_id = identifier.rsplit(".", 1)[0]
            if doc_id in self._fail_download_ids:
                self._fail_download_ids.remove(doc_id)
                raise RequestException("simulated failure")
            path.write_text("data", encoding="utf-8")
            paths.append(path)
            self.download_calls.append(doc_id)
        return paths


class CheckpointResumeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.output_dir = base / "xml"
        self.checkpoint_path = base / "checkpoint.json"
        self.documents = [
            DocumentListing("doc1", "Doc 1", "https://example.com/doc1"),
            DocumentListing("doc2", "Doc 2", "https://example.com/doc2"),
            DocumentListing("doc3", "Doc 3", "https://example.com/doc3"),
        ]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_resume_skips_processed_documents(self) -> None:
        with DummyScraper(self.output_dir, self.documents) as scraper:
            scraper.run(limit=2, checkpoint_path=self.checkpoint_path, start_year=2024, end_year=2024)
            page_url = scraper.page_url

        payload = json.loads(self.checkpoint_path.read_text())
        self.assertEqual(payload["resume_url"], page_url)
        self.assertEqual(payload["resume_index"], 2)

        with DummyScraper(self.output_dir, self.documents) as scraper:
            scraper.run(checkpoint_path=self.checkpoint_path, start_year=2024, end_year=2024)
            self.assertEqual(scraper.fetch_calls, ["doc3"])
            self.assertEqual(scraper.download_calls, ["doc3"])

    def test_resume_retries_after_failure(self) -> None:
        with self.assertRaises(SystemExit):
            with DummyScraper(self.output_dir, self.documents, fail_download_ids={"doc2"}) as scraper:
                scraper.run(checkpoint_path=self.checkpoint_path, start_year=2024, end_year=2024)

        payload = json.loads(self.checkpoint_path.read_text())
        self.assertEqual(payload["resume_url"], DummyScraper.page_url)
        self.assertEqual(payload["resume_index"], 1)

        with DummyScraper(self.output_dir, self.documents) as scraper:
            scraper.run(checkpoint_path=self.checkpoint_path, start_year=2024, end_year=2024)
            self.assertEqual(scraper.fetch_calls, ["doc2", "doc3"])
            self.assertEqual(scraper.download_calls, ["doc2", "doc3"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
