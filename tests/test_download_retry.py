from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from requests.exceptions import ChunkedEncodingError, RequestException

from lovtidend.scraper import LovtidendScraper


class _FlakyResponse:
    def __init__(self, should_fail: bool, payload: bytes) -> None:
        self.should_fail = should_fail
        self.payload = payload
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def __enter__(self) -> "_FlakyResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 65536):
        if self.should_fail:
            raise ChunkedEncodingError("Response ended prematurely")
        yield self.payload


class _FlakySession:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, url: str, headers: dict[str, str], stream: bool = False, timeout: tuple[float, float] | None = None):
        self.calls += 1
        should_fail = self.calls == 1
        return _FlakyResponse(should_fail=should_fail, payload=b"<ok/>")


class _FailingSession:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, url: str, headers: dict[str, str], stream: bool = False, timeout: tuple[float, float] | None = None):
        del url, headers, stream, timeout
        self.calls += 1
        raise RequestException("simulated download failure")


class _FallbackScraper(LovtidendScraper):
    def __init__(self, output_dir: Path, session: _FailingSession, html_fixture: str):
        super().__init__(
            output_dir,
            client=session,
            max_retries=5,
            delay_range=(0, 0),
            download_delay_range=(0, 0),
            cache_dir=None,
        )
        self.fallback_calls: list[tuple[str, str]] = []
        self.cached_calls: list[str] = []
        self.html_fixture = html_fixture

    def _cached_text(self, url: str, policy):  # type: ignore[override]
        del policy
        self.cached_calls.append(url)
        return self.html_fixture

    def _download_html_fallback(self, html_url: str, xml_url: str, destination: Path):  # type: ignore[override]
        self.fallback_calls.append((html_url, xml_url))
        html_destination = destination.with_suffix(".html")
        html_destination.parent.mkdir(parents=True, exist_ok=True)
        html_destination.write_text(self._extract_relevant_html(self.html_fixture), encoding="utf-8")
        return html_destination


class DownloadRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_chunked_download_retries(self) -> None:
        session = _FlakySession()
        scraper = LovtidendScraper(
            self.root,
            client=session,
            max_retries=3,
            delay_range=(0, 0),
            download_delay_range=(0, 0),
            cache_dir=None,
        )
        try:
            paths = scraper.download_xml("https://example.com/xml/test.xml")
        finally:
            scraper.close()

        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].is_file())
        self.assertIn(b"<ok/>", paths[0].read_bytes())
        self.assertEqual(session.calls, 2)

    def test_html_fallback_after_three_failures(self) -> None:
        session = _FailingSession()
        html_fixture = """
        <html>
          <body>
            <header>Navigation</header>
            <main>
              <article id="doc">
                <h1>Tittel</h1>
                <p>Relevant innhold.</p>
              </article>
            </main>
            <footer>Footer</footer>
          </body>
        </html>
        """
        scraper = _FallbackScraper(
            self.root,
            session,
            html_fixture,
        )
        try:
            paths = scraper.download_xml(
                "https://example.com/xml/test.xml",
                html_fallback_url="https://example.com/html/test",
            )
        finally:
            scraper.close()

        self.assertEqual(session.calls, 3)
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].is_file())
        self.assertEqual(paths[0].suffix, ".html")
        saved = paths[0].read_text(encoding="utf-8")
        self.assertIn("Relevant innhold.", saved)
        self.assertIn("<article", saved)
        self.assertNotIn("Navigation", saved)
        self.assertNotIn("Footer", saved)
        self.assertEqual(
            scraper.fallback_calls,
            [("https://example.com/html/test", "https://example.com/xml/test.xml")],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
