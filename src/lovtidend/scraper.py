"""Utilities for scraping XML documents from Norsk Lovtidend."""

from __future__ import annotations

import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from http.client import IncompleteRead
from pathlib import Path
import re
from typing import Iterable, Iterator, Sequence
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from requests import Response, Session
from requests.exceptions import (
    ChunkedEncodingError,
    ContentDecodingError,
    HTTPError,
    RequestException,
)
from urllib3.exceptions import DecodeError, ProtocolError

from .checkpoint import (
    CheckpointError,
    CheckpointState,
    describe_resume_point,
    extract_offset,
    load_checkpoint,
    update_checkpoint_file,
)
from .cache import CachePolicy, ResponseCache
from .display import describe_document, display_paths

DEFAULT_BASE_URL = "https://lovdata.no/register/lovtidend"
DEFAULT_HEADERS = {
    "Accept-Language": "nb,en;q=0.8",
}
XML_ACCEPT_HEADER = "application/xml, text/xml;q=0.9, */*;q=0.8"
DEFAULT_FIRST_YEAR = 1982
XML_ACCEPT_HEADER = "application/xml, text/xml;q=0.9, */*;q=0.8"
DEFAULT_CACHE_DIR = Path("data/http_cache")
LISTING_TTL_CURRENT = 60 * 60 * 24 * 4  # 4 days
DOCUMENT_TTL_CURRENT = 60 * 60 * 24 * 20  # 20 days
ARCHIVE_TTL = 60 * 60 * 24 * 2000  # ~5.5 years
TRUNCATION_SENTINEL = "Vis hele dokumentet"
NO_RESULTS_TEXT = "Ingen dokumenter å vise"
PAGINATION_PATTERN = re.compile(r"Viser\s+(\d+)\s*-\s*(\d+)\s+av\s+(\d+)", re.IGNORECASE)
RETRIABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
STREAM_RETRY_EXCEPTIONS = (
    ChunkedEncodingError,
    ContentDecodingError,
    DecodeError,
    ProtocolError,
    IncompleteRead,
)
USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/109.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/109.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/108.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/108.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/108.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/16.1 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) "
        "Gecko/20100101 Firefox/109.0"
    ),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) " "Gecko/20100101 Firefox/109.0"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:108.0) " "Gecko/20100101 Firefox/108.0"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:108.0) " "Gecko/20100101 Firefox/108.0"),
]


@dataclass(slots=True)
class DocumentListing:
    """Light-weight representation of a Lovtidend document."""

    identifier: str
    title: str
    document_url: str


@dataclass(slots=True)
class ListingPage:
    """Represents a paginated chunk from the register."""

    number: int
    documents: list[DocumentListing]
    current_url: str
    next_url: str | None
    resume_url: str | None
    description: str | None = None
    first_index: int | None = None
    last_index: int | None = None
    total_count: int | None = None


class LovtidendScraper:
    """Scraper that iterates over Norsk Lovtidend and downloads XML versions."""

    def __init__(
        self,
        output_dir: Path,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client: Session | None = None,
        overwrite: bool = False,
        delay_range: tuple[float, float] | None = None,
        download_delay_range: tuple[float, float] | None = None,
        max_retries: int = 5,
        backoff_factor: float = 0.65,
        cache_dir: Path | None = DEFAULT_CACHE_DIR,
    ) -> None:
        self.base_url = base_url
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._output_root = self.output_dir.resolve()
        self.overwrite = overwrite
        self._delay_range = delay_range or (0.35, 0.85)
        self._download_delay_range = download_delay_range or (0.9, 1.8)
        self._download_retry_min = 1.5
        self.max_retries = max(1, max_retries)
        self.backoff_factor = max(backoff_factor, 0.0)
        self._timeout = (15.0, 90.0)
        self._session_owner = client is None
        self._session = client or self._build_session()
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._cache = ResponseCache(self.cache_dir) if self.cache_dir else None

    def __enter__(self) -> "LovtidendScraper":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        if self._session_owner:
            self._session.close()

    def _build_session(self) -> Session:
        session = Session()
        session.headers.update(DEFAULT_HEADERS)
        return session

    def iter_pages(
        self,
        *,
        start_url: str | None = None,
        max_pages: int | None = None,
        limit: int | None = None,
    ) -> Iterator[ListingPage]:
        """Yield pages of document metadata respecting pagination constraints."""

        if max_pages is not None and max_pages <= 0:
            return

        current_url = self._normalize_listing_url(start_url or self.base_url)
        processed = 0
        page_number = 0
        visited_pages: set[str] = set()
        seen_offsets: set[int] = set()
        last_description: str | None = None
        last_offset: int | None = None

        while current_url:
            if current_url in visited_pages:
                print(
                    f"Detected repeated listing page {current_url}; stopping to avoid pagination loops.",
                    file=sys.stderr,
                )
                break
            visited_pages.add(current_url)
            offset_value = extract_offset(current_url)
            if offset_value is not None:
                if offset_value in seen_offsets:
                    print(
                        f"Listing offset {offset_value} already processed; stopping to avoid repeated work.",
                        file=sys.stderr,
                    )
                    break
                seen_offsets.add(offset_value)
            page_number += 1
            cache_bypassed = False
            while True:
                html = self._fetch_listing_page(current_url, bypass_cache=cache_bypassed)
                (
                    documents,
                    next_url,
                    page_description,
                    first_index,
                    last_index,
                    total_count,
                ) = self._parse_listing(html, current_url)
                if (
                    not cache_bypassed
                    and offset_value is not None
                    and offset_value > 0
                    and first_index is not None
                    and first_index <= offset_value
                ):
                    print(
                        f"Listing page {current_url} returned entries starting at {first_index}; bypassing cache and retrying.",
                        file=sys.stderr,
                    )
                    cache_bypassed = True
                    continue
                break
            next_url = self._normalize_listing_url(next_url)
            current_documents = documents
            truncated = False

            if limit is not None:
                remaining = max(limit - processed, 0)
                if remaining == 0:
                    break
                if remaining < len(current_documents):
                    current_documents = current_documents[:remaining]
                    truncated = True

            if not truncated and page_description:
                if last_description and page_description == last_description:
                    if offset_value is None or offset_value == last_offset:
                        print(
                            f"Pagination summary {page_description!r} repeated on {current_url}; stopping to avoid loops.",
                            file=sys.stderr,
                        )
                        break
                last_description = page_description
                last_offset = offset_value

            processed += len(current_documents)
            resume_url = current_url if truncated else next_url
            yield ListingPage(
                number=page_number,
                documents=list(current_documents),
                current_url=current_url,
                next_url=next_url,
                resume_url=resume_url,
                description=page_description,
                first_index=first_index,
                last_index=last_index,
                total_count=total_count,
            )

            if truncated or not next_url:
                break

            if max_pages is not None and page_number >= max_pages:
                break

            if next_url in visited_pages:
                print(
                    f"Next page {next_url} was already seen; stopping pagination early.",
                    file=sys.stderr,
                )
                break
            current_url = next_url

    def iter_documents(
        self,
        *,
        start_url: str | None = None,
        max_pages: int | None = None,
        limit: int | None = None,
    ) -> Iterator[DocumentListing]:
        for page in self.iter_pages(start_url=start_url, max_pages=max_pages, limit=limit):
            for document in page.documents:
                yield document

    def fetch_xml_links(self, document: DocumentListing) -> Sequence[str]:
        """Return XML download links for a specific document."""

        html = self._fetch_document_html(document.document_url)
        soup = BeautifulSoup(html, "html.parser")
        links: list[str] = []

        for anchor in soup.select("a[href]"):
            href = anchor["href"].strip()
            if not href or not href.lower().endswith(".xml"):
                continue
            absolute = urljoin(document.document_url, href)
            if absolute not in links:
                links.append(absolute)

        return links

    def download_xml(self, url_or_urls: str | Sequence[str], *, html_fallback_url: str | None = None) -> list[Path]:
        """Download one or many XML files and return their paths.

        If XML repeatedly fails, falls back to saving the document HTML after
        three attempts (or fewer if max_retries is lower).
        """

        if isinstance(url_or_urls, str):
            urls: Iterable[str] = [url_or_urls]
        else:
            urls = url_or_urls

        saved_paths: list[Path] = []
        for xml_url in urls:
            destination = self._target_path(xml_url)
            html_destination = self._html_fallback_path(destination)
            if destination.exists() and not self.overwrite:
                saved_paths.append(destination)
                continue
            if html_fallback_url and html_destination.exists() and not self.overwrite:
                saved_paths.append(html_destination)
                continue

            attempt = 0
            last_error: RequestException | None = None
            while attempt < self.max_retries:
                attempt += 1
                headers = self._request_headers()
                headers.setdefault("Accept", XML_ACCEPT_HEADER)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temp_path = destination.with_suffix(destination.suffix + ".part")
                try:
                    with self._session.get(
                        xml_url,
                        headers=headers,
                        stream=True,
                        timeout=self._timeout,
                    ) as response:
                        response.raise_for_status()
                        try:
                            with temp_path.open("wb") as handle:
                                for chunk in response.iter_content(chunk_size=65536):
                                    if chunk:
                                        handle.write(chunk)
                        except STREAM_RETRY_EXCEPTIONS as exc:
                            raise RequestException(f"Stream error while downloading {xml_url}: {exc}") from exc
                        temp_path.replace(destination)
                        saved_paths.append(destination)
                        self._sleep_download_delay()
                        break
                except HTTPError as exc:
                    last_error = exc
                    temp_path.unlink(missing_ok=True)
                    if self._should_use_html_fallback(attempt, html_fallback_url):
                        saved_paths.append(
                            self._download_html_fallback(html_fallback_url, xml_url, destination)
                        )
                        break
                    if not self._should_retry(exc.response, attempt):
                        raise
                    self._sleep(self._download_retry_delay(attempt, exc.response))
                except RequestException as exc:
                    last_error = exc
                    temp_path.unlink(missing_ok=True)
                    if self._should_use_html_fallback(attempt, html_fallback_url):
                        saved_paths.append(
                            self._download_html_fallback(html_fallback_url, xml_url, destination)
                        )
                        break
                    if attempt >= self.max_retries:
                        raise
                    self._sleep(self._download_retry_delay(attempt))
                except Exception:
                    temp_path.unlink(missing_ok=True)
                    raise
            else:
                if last_error is not None:
                    raise last_error

        return saved_paths

    def _target_path(self, xml_url: str) -> Path:
        parsed = urlparse(xml_url)
        relative = Path(parsed.path.lstrip("/"))
        parts = relative.parts
        if parts and parts[0] == "xml":
            relative = Path(*parts[1:])
        year = self._guess_year(xml_url) or self._year_from_fragment(relative.name)
        if year is not None and (not relative.parts or relative.parts[0] != str(year)):
            relative = Path(str(year)) / relative
        target = (self._output_root / relative).resolve()
        try:
            target.relative_to(self._output_root)
        except ValueError as exc:  # pragma: no cover - safety net
            raise ValueError(f"Unexpected XML path outside output directory: {xml_url}") from exc
        return target

    def _html_fallback_path(self, destination: Path) -> Path:
        return destination.with_suffix(".html")

    def _should_use_html_fallback(self, attempt: int, html_fallback_url: str | None) -> bool:
        if not html_fallback_url:
            return False
        fallback_after = min(self.max_retries, 3)
        return attempt >= fallback_after

    def _download_html_fallback(self, html_url: str, xml_url: str, destination: Path) -> Path:
        html_destination = self._html_fallback_path(destination)
        html_destination.parent.mkdir(parents=True, exist_ok=True)
        html_content = self._fetch_document_html(html_url)
        cached = self._cache.read(self._document_cache_policy(html_url), html_url) if self._cache else None
        used_cache = cached is not None
        trimmed_html = self._extract_relevant_html(html_content)
        html_destination.write_text(trimmed_html, encoding="utf-8")
        print(
            f"Falling back to HTML after repeated XML failures for {xml_url} -> {html_destination}",
            file=sys.stderr,
        )
        if not used_cache:
            self._sleep_download_delay()
        return html_destination

    def _extract_relevant_html(self, html_content: str) -> str:
        """Extract the most relevant portion of a Lovdata document page.

        Prefers the article inside <main>, then any article tag, falling back
        to the <main> element or the raw body. Returns the original HTML if no
        suitable container is found.
        """

        soup = BeautifulSoup(html_content, "html.parser")
        preferred_selectors = ["main article", "article", "main"]
        for selector in preferred_selectors:
            node = soup.select_one(selector)
            if node:
                return node.decode()
        if soup.body:
            return soup.body.decode()
        return html_content

    def _fetch_listing_page(self, url: str, *, bypass_cache: bool = False) -> str:
        policy = self._listing_cache_policy(url)
        return self._cached_text(url, policy, bypass_cache=bypass_cache)

    def _fetch_document_html(self, url: str) -> str:
        policy = self._document_cache_policy(url)
        html = self._cached_text(url, policy)
        if TRUNCATION_SENTINEL in html and not self._is_full_document_url(url):
            full_url = self._full_document_url(url)
            html = self._cached_text(full_url, self._document_cache_policy(full_url))
        return html

    def _cached_text(self, url: str, policy: CachePolicy, *, bypass_cache: bool = False) -> str:
        if self._cache and not bypass_cache:
            cached = self._cache.read(policy, url)
            if cached is not None:
                return cached
        response = self._get(url)
        text = response.text
        if self._cache:
            self._cache.write(policy, url, text)
        return text

    def _listing_cache_policy(self, url: str) -> CachePolicy:
        year = self._guess_year(url)
        namespace = f"listing/{year if year is not None else 'unknown'}"
        ttl = LISTING_TTL_CURRENT if self._is_current_year(year) else ARCHIVE_TTL
        return CachePolicy(namespace=namespace, ttl_seconds=ttl)

    def _document_cache_policy(self, url: str) -> CachePolicy:
        year = self._guess_year(url)
        namespace = f"document/{year if year is not None else 'unknown'}"
        ttl = DOCUMENT_TTL_CURRENT if self._is_current_year(year) else ARCHIVE_TTL
        return CachePolicy(namespace=namespace, ttl_seconds=ttl)

    def _is_current_year(self, year: int | None) -> bool:
        if year is None:
            return True
        return year >= datetime.now().year

    def _guess_year(self, url: str) -> int | None:
        parsed = urlparse(url)
        for values in parse_qs(parsed.query).values():
            for value in values:
                year = self._year_from_fragment(value)
                if year is not None:
                    return year
        year = self._year_from_fragment(parsed.path)
        return year

    def _year_from_fragment(self, fragment: str) -> int | None:
        match = re.search(r"(19|20)\d{2}", fragment)
        if not match:
            return None
        try:
            return int(match.group(0))
        except ValueError:  # pragma: no cover - defensive
            return None

    def _resolve_year_sequence(self, start_year: int | None, end_year: int | None) -> list[int]:
        current_year = datetime.now().year
        first = start_year if start_year is not None else DEFAULT_FIRST_YEAR
        last = end_year if end_year is not None else current_year
        if last < first:
            first, last = last, first
        return list(range(first, last + 1))

    def _year_url(self, year: int) -> str:
        parsed = urlparse(self.base_url)
        query = parse_qs(parsed.query)
        query.setdefault("avdeling", ["*"])
        query.setdefault("ministry", ["*"])
        query.setdefault("kunngjortDato", ["*"])
        query.setdefault("search", [""])
        query["year"] = [str(year)]
        encoded = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=encoded))

    def _extract_year_from_url(self, url: str | None) -> int | None:
        if not url:
            return None
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        values = query.get("year")
        if values:
            try:
                return int(values[0])
            except (TypeError, ValueError):
                return None
        return self._year_from_fragment(parsed.path)

    def _full_document_url(self, url: str) -> str:
        trimmed = url.rstrip("/")
        if trimmed.endswith("*"):
            return trimmed
        return f"{trimmed}/*"

    def _is_full_document_url(self, url: str) -> bool:
        return url.rstrip().endswith("/*")

    def _normalize_listing_url(self, url: str | None) -> str | None:
        if not url:
            return None
        parsed = urlparse(url)
        normalized = parsed._replace(fragment="")
        return urlunparse(normalized)

    def _merge_listing_query(self, current_url: str, next_url: str) -> str:
        """Ensure pagination URLs keep the same filters as the current page."""

        current = urlparse(current_url)
        target = urlparse(next_url)

        merged_query = parse_qs(current.query)
        merged_query.update(parse_qs(target.query))
        encoded = urlencode(merged_query, doseq=True)

        combined = target._replace(query=encoded, fragment="")
        if not combined.scheme:
            combined = combined._replace(scheme=current.scheme)
        if not combined.netloc:
            combined = combined._replace(netloc=current.netloc)
        if not combined.path:
            combined = combined._replace(path=current.path)

        return urlunparse(combined)

    def _parse_pagination_summary(
        self,
        soup: BeautifulSoup,
        html: str,
        page_url: str,
    ) -> tuple[str | None, int | None, int | None, int | None]:
        paragraphs = soup.select("main section p.center-align")
        if len(paragraphs) != 1:
            if NO_RESULTS_TEXT in html:
                return NO_RESULTS_TEXT, None, None, 0
            raise ValueError(
                f"Expected a single pagination summary on {page_url}, found {len(paragraphs)} paragraphs."
            )

        text = " ".join(paragraphs[0].stripped_strings)
        match = PAGINATION_PATTERN.search(text)
        if match:
            try:
                first = int(match.group(1))
                last = int(match.group(2))
                total = int(match.group(3))
            except ValueError as exc:  # pragma: no cover - defensive
                raise ValueError(f"Failed to parse pagination summary {text!r}") from exc
            return text or None, first, last, total

        if NO_RESULTS_TEXT in html:
            return text or NO_RESULTS_TEXT, None, None, 0

        raise ValueError(f"Could not parse pagination summary {text!r} on {page_url}")

    def _build_offset_url(self, source_url: str, offset: int) -> str:
        parsed = urlparse(source_url)
        query = parse_qs(parsed.query)
        query["offset"] = [str(max(offset, 0))]
        encoded = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=encoded, fragment=""))

    def _select_next_url(
        self, page_url: str, summary_next: str | None, anchor_next: str | None
    ) -> str | None:
        """Choose a next URL that moves forward when the summary disagrees with the anchor."""

        current_offset = extract_offset(page_url)
        candidates: list[str] = []

        if summary_next:
            candidates.append(summary_next)
        if anchor_next and anchor_next not in candidates:
            candidates.append(anchor_next)

        for candidate in candidates:
            candidate_offset = extract_offset(candidate)
            if candidate_offset is None:
                return candidate
            if current_offset is None or candidate_offset > current_offset:
                return candidate

        return None

    def _parse_listing(
        self, html: str, page_url: str
    ) -> tuple[list[DocumentListing], str | None, str | None, int | None, int | None, int | None]:
        soup = BeautifulSoup(html, "html.parser")
        description, first_index, last_index, total_count = self._parse_pagination_summary(
            soup, html, page_url
        )
        documents: list[DocumentListing] = []

        for article in soup.select("article[aria-labelledby]"):
            anchor = article.select_one("h3 a[href]")
            if not anchor:
                continue
            identifier = article.get("aria-labelledby") or anchor.get("id") or ""
            title = " ".join(anchor.stripped_strings)
            document_url = urljoin(page_url, anchor["href"])
            documents.append(DocumentListing(identifier=identifier, title=title, document_url=document_url))

        if first_index is not None and last_index is not None:
            expected = max(last_index - first_index + 1, 0)
            if expected != len(documents):
                raise ValueError(
                    f"Listing page {page_url} reported {expected} documents but parsed {len(documents)}."
                )

        next_anchor = soup.select_one(".footer-pagination .pager .next a[href]")
        anchor_next = urljoin(page_url, next_anchor["href"]) if next_anchor else None
        if anchor_next:
            anchor_next = self._merge_listing_query(page_url, anchor_next)
        summary_next: str | None = None
        if total_count is not None and last_index is not None and total_count > last_index:
            summary_next = self._build_offset_url(page_url, last_index)
        next_url = self._select_next_url(page_url, summary_next, anchor_next)

        return documents, next_url, description, first_index, last_index, total_count

    def _get(self, url: str) -> Response:
        attempt = 0
        last_error: RequestException | None = None

        while attempt < self.max_retries:
            attempt += 1
            headers = self._request_headers()
            try:
                response = self._session.get(url, headers=headers, timeout=self._timeout)
                response.raise_for_status()
                # Read the body here so transient read errors can be retried.
                try:
                    response.content
                except STREAM_RETRY_EXCEPTIONS as exc:
                    raise RequestException(f"Stream error while reading {url}: {exc}") from exc
                self._sleep_with_jitter()
                return response
            except HTTPError as exc:
                last_error = exc
                if not self._should_retry(exc.response, attempt):
                    raise
                self._sleep(self._retry_delay(attempt, exc.response))
            except RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                self._sleep(self._retry_delay(attempt))

        if last_error is None:  # pragma: no cover - defensive
            raise RequestException("Request failed without capturing exception")
        raise last_error

    def _sleep_with_jitter(self) -> None:
        if not self._delay_range:
            return
        lower, upper = self._delay_range
        wait = random.uniform(lower, upper) if upper > lower else lower
        if wait > 0:
            time.sleep(wait)

    def _sleep_download_delay(self) -> None:
        if not self._download_delay_range:
            return
        lower, upper = self._download_delay_range
        wait = random.uniform(lower, upper) if upper > lower else lower
        if wait > 0:
            time.sleep(wait)

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def _should_retry(self, response: Response, attempt: int) -> bool:
        return response.status_code in RETRIABLE_STATUSES and attempt < self.max_retries

    def _retry_delay(self, attempt: int, response: Response | None = None) -> float:
        if response:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    parsed = float(retry_after)
                    if parsed >= 0:
                        return parsed
                except ValueError:
                    pass
        base = self.backoff_factor * (2 ** (attempt - 1))
        jitter = random.uniform(0, base / 2 if base else 0)
        return base + jitter

    def _download_retry_delay(self, attempt: int, response: Response | None = None) -> float:
        delay = self._retry_delay(attempt, response)
        if delay < self._download_retry_min:
            delay = self._download_retry_min
        return delay

    def _request_headers(self) -> dict[str, str]:
        headers = dict(DEFAULT_HEADERS)
        headers["User-Agent"] = random.choice(USER_AGENTS)
        headers.setdefault("Connection", "close")
        headers.setdefault("Accept-Encoding", "identity")
        return headers

    def run(
        self,
        *,
        start_url: str | None = None,
        max_pages: int | None = None,
        limit: int | None = None,
        checkpoint_path: Path | None = None,
        no_resume: bool = False,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> None:
        total_documents = 0
        total_files = 0
        checkpoint_state: CheckpointState | None = None
        checkpoint_location = checkpoint_path or Path("data/lovtidend_checkpoint.json")
        use_checkpoint = not no_resume
        session_documents = 0
        session_files = 0
        resume_index = 0
        download_total = 0
        download_failures = 0

        if use_checkpoint:
            try:
                checkpoint_state = load_checkpoint(checkpoint_location)
            except CheckpointError as exc:
                print(exc, file=sys.stderr)
                raise SystemExit(2)

        if use_checkpoint and checkpoint_state:
            if start_url:
                print(
                    f"Ignoring checkpoint stored in {checkpoint_location} because a start URL was provided."
                )
            else:
                start_url = checkpoint_state.resume_url
                print(
                    f"Resuming from checkpoint {describe_resume_point(checkpoint_state)}"
                    f" stored in {checkpoint_location}"
                )
                total_documents = checkpoint_state.total_documents
                total_files = checkpoint_state.total_files
                resume_index = max(checkpoint_state.resume_index, 0)

        years = self._resolve_year_sequence(start_year, end_year)
        if not years:
            years = [datetime.now().year]
        year_index_map = {year: idx for idx, year in enumerate(years)}

        def ensure_year(year_value: int) -> int:
            nonlocal years, year_index_map
            if year_value not in year_index_map:
                years = sorted(set(years + [year_value]))
                year_index_map = {year: idx for idx, year in enumerate(years)}
            return year_index_map[year_value]

        def align_start(url: str | None) -> tuple[str, int]:
            if not url:
                return self._year_url(years[0]), 0
            year_value = self._extract_year_from_url(url)
            if year_value is None:
                merged = self._merge_listing_query(self._year_url(years[0]), url)
                return merged, 0
            index = ensure_year(year_value)
            merged = self._merge_listing_query(self._year_url(year_value), url)
            return merged, index

        current_start_url, current_year_idx = align_start(start_url)
        remaining_limit = limit
        remaining_pages = max_pages
        pending_resume_index = resume_index
        stop_all = False

        try:
            while current_year_idx < len(years):
                if stop_all:
                    break
                if remaining_limit is not None and remaining_limit <= 0:
                    break
                if remaining_pages is not None and remaining_pages <= 0:
                    break

                current_year = years[current_year_idx]
                if not current_start_url or self._extract_year_from_url(current_start_url) != current_year:
                    current_start_url = self._year_url(current_year)
                    pending_resume_index = 0

                call_limit = remaining_limit if remaining_limit is not None else None
                call_pages = remaining_pages if remaining_pages is not None else None

                year_completed = True
                last_page: ListingPage | None = None
                local_resume_index = pending_resume_index
                pending_resume_index = 0

                for page in self.iter_pages(
                    start_url=current_start_url,
                    max_pages=call_pages,
                    limit=call_limit,
                ):
                    last_page = page
                    documents = page.documents
                    skip_in_page = 0
                    page_resume_index = 0
                    if local_resume_index and documents:
                        capped = min(local_resume_index, len(documents))
                        skip_in_page = capped
                        page_resume_index = capped
                    else:
                        page_resume_index = local_resume_index if local_resume_index else 0
                    local_resume_index = 0
                    contiguous_prefix = True

                    if use_checkpoint:
                        update_checkpoint_file(
                            checkpoint_location,
                            resume_url=page.current_url,
                            resume_index=page_resume_index,
                            total_documents=total_documents,
                            total_files=total_files,
                        )

                    for idx, document in enumerate(documents):
                        if idx < skip_in_page:
                            continue
                        try:
                            xml_links = self.fetch_xml_links(document)
                        except RequestException as exc:
                            print(
                                f"Failed to fetch XML links for {describe_document(document)}: {exc}",
                                file=sys.stderr,
                            )
                            if contiguous_prefix:
                                contiguous_prefix = False
                                page_resume_index = idx
                                if use_checkpoint:
                                    update_checkpoint_file(
                                        checkpoint_location,
                                        resume_url=page.current_url,
                                        resume_index=page_resume_index,
                                        total_documents=total_documents,
                                        total_files=total_files,
                                    )
                            continue

                        if not xml_links:
                            print(f"No XML link found for {document.identifier}", file=sys.stderr)
                            if contiguous_prefix:
                                page_resume_index = idx + 1
                                if use_checkpoint:
                                    update_checkpoint_file(
                                        checkpoint_location,
                                        resume_url=page.current_url,
                                        resume_index=page_resume_index,
                                        total_documents=total_documents,
                                        total_files=total_files,
                                    )
                            continue

                        download_total += 1
                        try:
                            written = self.download_xml(xml_links, html_fallback_url=document.document_url)
                        except RequestException as exc:
                            download_failures += 1
                            print(
                                f"Failed to download XML for {describe_document(document)}: {exc}",
                                file=sys.stderr,
                            )
                            if contiguous_prefix:
                                contiguous_prefix = False
                                page_resume_index = idx
                                if use_checkpoint:
                                    update_checkpoint_file(
                                        checkpoint_location,
                                        resume_url=page.current_url,
                                        resume_index=page_resume_index,
                                        total_documents=total_documents,
                                        total_files=total_files,
                                    )
                            raise

                        total_documents += 1
                        total_files += len(written)
                        session_documents += 1
                        session_files += len(written)
                        page_resume_index = idx + 1 if contiguous_prefix else page_resume_index
                        descriptor = "XML file(s)" if all(path.suffix.lower() == ".xml" for path in written) else "file(s)"
                        print(
                            f"Saved {len(written)} {descriptor} for {describe_document(document)} -> "
                            f"{display_paths(written, self.output_dir)}"
                        )

                        if contiguous_prefix and use_checkpoint:
                            update_checkpoint_file(
                                checkpoint_location,
                                resume_url=page.current_url,
                                resume_index=page_resume_index,
                                total_documents=total_documents,
                                total_files=total_files,
                            )

                    page_truncated = page.resume_url == page.current_url
                    page_completed = contiguous_prefix and page_resume_index >= len(documents)

                    if not page_completed:
                        year_completed = False

                    if use_checkpoint:
                        if page_truncated or not page_completed:
                            update_checkpoint_file(
                                checkpoint_location,
                                resume_url=page.current_url,
                                resume_index=page_resume_index,
                                total_documents=total_documents,
                                total_files=total_files,
                            )
                        else:
                            update_checkpoint_file(
                                checkpoint_location,
                                resume_url=page.resume_url,
                                resume_index=0,
                                total_documents=total_documents,
                                total_files=total_files,
                            )

                    if remaining_limit is not None:
                        remaining_limit -= len(documents)
                        if remaining_limit <= 0:
                            year_completed = False
                            stop_all = True
                            break
                    if remaining_pages is not None:
                        remaining_pages -= 1
                        if remaining_pages <= 0:
                            year_completed = False
                            stop_all = True
                            break

                if not year_completed:
                    break

                current_year_idx += 1
                pending_resume_index = 0
                if current_year_idx >= len(years):
                    next_resume_url = None
                    current_start_url = None
                else:
                    next_resume_year = years[current_year_idx]
                    next_resume_url = self._year_url(next_resume_year)
                    current_start_url = next_resume_url

                if use_checkpoint:
                    update_checkpoint_file(
                        checkpoint_location,
                        resume_url=next_resume_url,
                        resume_index=0,
                        total_documents=total_documents,
                        total_files=total_files,
                    )

                if next_resume_url is None:
                    break

        except KeyboardInterrupt:
            print("\nInterrupted by user", file=sys.stderr)
        except RequestException as exc:
            print(f"HTTP error: {exc}", file=sys.stderr)
            raise SystemExit(1)

        if session_documents:
            print(
                f"\nFinished. Documents processed this run: {session_documents}, XML files saved this run: {session_files}."
            )
        else:
            print("\nFinished. No new documents were downloaded.")

        if use_checkpoint and (total_documents or total_files):
            print(f"Total progress stored in checkpoint: {total_documents} documents, {total_files} files.")

        if download_total:
            failure_rate = download_failures / download_total
            if failure_rate > 0.1:
                percent = round(failure_rate * 100, 2)
                print(
                    f"Warning: {percent}% of XML downloads failed this run. Consider retrying or reducing load."
                )


__all__ = ["DocumentListing", "ListingPage", "LovtidendScraper", "DEFAULT_BASE_URL"]
