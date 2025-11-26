"""Pagination behaviour should match the legacy PHP scraper."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from lovtidend.scraper import LovtidendScraper


def make_listing_html(
    start_idx: int,
    end_idx: int,
    total: int,
    nav_href: str | None,
    *,
    summary_start: int | None = None,
    summary_end: int | None = None,
) -> str:
    """Return a tiny HTML snippet that mimics Lovdata's listing format."""

    summary_start = summary_start if summary_start is not None else start_idx
    summary_end = summary_end if summary_end is not None else end_idx

    articles: list[str] = []
    for idx in range(start_idx, end_idx + 1):
        identifier = f"LTI/forskrift/1982-idx-{idx}"
        articles.append(
            f"""
            <article aria-labelledby="{identifier}">
                <h3 id="{identifier}">
                    <a href="/dokument/{identifier}">
                        <strong>Tittel {idx}</strong>
                    </a>
                </h3>
                <p>
                    <span class="red">FOR-1982-{idx:04d}</span>
                    <span class="blueLight">Justis- og beredskapsdepartementet</span>
                </p>
            </article>
            <hr/>
            """
        )

    nav_block = ""
    if nav_href:
        nav_block = f"""
        <nav class="footer-pagination" aria-labelledby="resultNav">
            <ul class="pager">
                <li class="next">
                    <a role="button" href="{nav_href}">Neste side &rarr;</a>
                </li>
            </ul>
        </nav>
        """

    return f"""
    <main>
        <section>
            <p class="center-align">Viser {summary_start} - {summary_end} av {total} treff</p>
            <div class="documentList">
                {''.join(articles)}
            </div>
            {nav_block}
        </section>
    </main>
    """


class PaginationParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.scraper = LovtidendScraper(root / "xml", cache_dir=None, delay_range=(0, 0))

    def tearDown(self) -> None:
        self.scraper.close()
        self._tmp.cleanup()

    def test_summary_controls_next_url(self) -> None:
        html = make_listing_html(1, 2, 4, nav_href="?year=1982&offset=999#doclistheader")
        (
            documents,
            next_url,
            description,
            first_index,
            last_index,
            total_count,
        ) = self.scraper._parse_listing(html, "https://example.com/register?year=1982&foo=bar")

        self.assertEqual(len(documents), 2)
        self.assertEqual(first_index, 1)
        self.assertEqual(last_index, 2)
        self.assertEqual(total_count, 4)
        self.assertEqual(description, "Viser 1 - 2 av 4 treff")
        self.assertEqual(next_url, "https://example.com/register?year=1982&foo=bar&offset=2")

    def test_anchor_next_preserves_filters(self) -> None:
        html = make_listing_html(1, 2, 2, nav_href="?year=1982&offset=40")
        (
            _documents,
            next_url,
            _description,
            first_index,
            last_index,
            total_count,
        ) = self.scraper._parse_listing(html, "https://example.com/register?year=1982&foo=bar")

        self.assertEqual(first_index, 1)
        self.assertEqual(last_index, 2)
        self.assertEqual(total_count, 2)
        self.assertEqual(next_url, "https://example.com/register?year=1982&foo=bar&offset=40")


class PaginationIteratorTest(unittest.TestCase):
    class StubScraper(LovtidendScraper):
        def __init__(self, output_dir: Path, pages: dict[int, str]) -> None:
            super().__init__(output_dir, cache_dir=None, delay_range=(0, 0))
            self._pages = pages
            self.requested_urls: list[str] = []

        def _fetch_listing_page(self, url: str, *, bypass_cache: bool = False) -> str:  # type: ignore[override]
            self.requested_urls.append(url if not bypass_cache else f"{url}#nocache")
            parsed = urlparse(url)
            offset_values = parse_qs(parsed.query).get("offset")
            offset = int(offset_values[0]) if offset_values else 0
            try:
                return self._pages[offset]
            except KeyError as exc:
                raise AssertionError(f"Unexpected offset {offset}") from exc

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_iter_pages_advances_with_summary_offset(self) -> None:
        first_page = make_listing_html(1, 2, 4, nav_href="?year=1982&offset=999#doclistheader")
        second_page = make_listing_html(3, 4, 4, nav_href=None)
        pages = {
            0: first_page,
            2: second_page,
        }

        with self.StubScraper(self.root / "xml", pages) as scraper:
            results = list(
                scraper.iter_pages(
                    start_url="https://example.com/register/lovtidend?year=1982&foo=bar",
                    max_pages=5,
                )
            )

        self.assertEqual(len(results), 2)
        self.assertEqual(
            [page.current_url for page in results],
            [
                "https://example.com/register/lovtidend?year=1982&foo=bar",
                "https://example.com/register/lovtidend?year=1982&foo=bar&offset=2",
            ],
        )

    def test_iter_pages_handles_repeated_summary_with_new_offset(self) -> None:
        first_page = make_listing_html(1, 20, 20, nav_href="?year=1982&offset=20#doclistheader")
        second_page = make_listing_html(1, 20, 20, nav_href=None)
        pages = {
            0: first_page,
            20: second_page,
        }

        with self.StubScraper(self.root / "xml", pages) as scraper:
            results = list(
                scraper.iter_pages(
                    start_url="https://example.com/register/lovtidend?year=1982&foo=bar",
                    max_pages=5,
                )
            )

        self.assertEqual(len(results), 2)
        self.assertEqual(
            [page.current_url for page in results],
            [
                "https://example.com/register/lovtidend?year=1982&foo=bar",
                "https://example.com/register/lovtidend?year=1982&foo=bar&offset=20",
            ],
        )
        self.assertEqual([len(page.documents) for page in results], [20, 20])
        self.assertEqual(
            scraper.requested_urls,
            [
                "https://example.com/register/lovtidend?year=1982&foo=bar",
                "https://example.com/register/lovtidend?year=1982&foo=bar&offset=20",
                "https://example.com/register/lovtidend?year=1982&foo=bar&offset=20#nocache",
            ],
        )

    def test_iter_pages_prefers_anchor_when_summary_stalls(self) -> None:
        first_page = make_listing_html(1, 20, 60, nav_href="?year=1982&offset=20")
        stalled_second_page = make_listing_html(
            21,
            40,
            60,
            nav_href="?year=1982&offset=40",
            summary_start=1,
            summary_end=20,
        )
        final_page = make_listing_html(41, 60, 60, nav_href=None)
        pages = {
            0: first_page,
            20: stalled_second_page,
            40: final_page,
        }

        with self.StubScraper(self.root / "xml", pages) as scraper:
            results = list(
                scraper.iter_pages(
                    start_url="https://example.com/register/lovtidend?year=1982&foo=bar",
                    max_pages=5,
                )
            )

        self.assertEqual(
            [page.current_url for page in results],
            [
                "https://example.com/register/lovtidend?year=1982&foo=bar",
                "https://example.com/register/lovtidend?year=1982&foo=bar&offset=20",
                "https://example.com/register/lovtidend?year=1982&foo=bar&offset=40",
            ],
        )
        self.assertEqual([len(page.documents) for page in results], [20, 20, 20])
        self.assertEqual(
            scraper.requested_urls,
            [
                "https://example.com/register/lovtidend?year=1982&foo=bar",
                "https://example.com/register/lovtidend?year=1982&foo=bar&offset=20",
                "https://example.com/register/lovtidend?year=1982&foo=bar&offset=20#nocache",
                "https://example.com/register/lovtidend?year=1982&foo=bar&offset=40",
            ],
        )

    def test_iter_pages_retries_when_offset_page_is_stale(self) -> None:
        first_page = make_listing_html(1, 20, 40, nav_href="?year=1982&offset=20")
        stale_second_page = make_listing_html(1, 20, 40, nav_href="?year=1982&offset=40")
        real_second_page = make_listing_html(21, 40, 40, nav_href=None)

        class BadCacheScraper(self.StubScraper):
            def _fetch_listing_page(self, url: str, *, bypass_cache: bool = False) -> str:  # type: ignore[override]
                self.requested_urls.append(url if not bypass_cache else f"{url}#nocache")
                parsed = urlparse(url)
                offset_values = parse_qs(parsed.query).get("offset")
                offset = int(offset_values[0]) if offset_values else 0
                if offset == 20 and not bypass_cache:
                    return stale_second_page
                if offset == 20 and bypass_cache:
                    return real_second_page
                return first_page

        with BadCacheScraper(self.root / "xml", {}) as scraper:
            results = list(
                scraper.iter_pages(
                    start_url="https://example.com/register/lovtidend?year=1982&foo=bar",
                    max_pages=5,
                )
            )

        self.assertEqual(len(results), 2)
        self.assertEqual(
            [page.current_url for page in results],
            [
                "https://example.com/register/lovtidend?year=1982&foo=bar",
                "https://example.com/register/lovtidend?year=1982&foo=bar&offset=20",
            ],
        )
        self.assertEqual([len(page.documents) for page in results], [20, 20])
        self.assertEqual(
            scraper.requested_urls,
            [
                "https://example.com/register/lovtidend?year=1982&foo=bar",
                "https://example.com/register/lovtidend?year=1982&foo=bar&offset=20",
                "https://example.com/register/lovtidend?year=1982&foo=bar&offset=20#nocache",
            ],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
