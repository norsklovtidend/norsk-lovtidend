# Lovtidend XML scraper

This project is a tiny scraper that walks through the public Norsk Lovtidend
register at [lovdata.no](https://lovdata.no/register/lovtidend) and downloads
the XML versions of every listed document. The project is managed with
[uv](https://github.com/astral-sh/uv) and targets Python 3.12.

## Getting started

1. Install the dependencies (uv creates `.venv` automatically). The helper script
   ignores any unrelated `VIRTUAL_ENV` that might already be active:

   ```bash
   ./scripts/uv.sh sync
   ```

2. Activate the local environment (only once per shell session):

   ```bash
   source .venv/bin/activate
   ```

3. Show the CLI options:

   ```bash
   python -m lovtidend --help
   ```

4. Download a few XML documents into `data/xml` (limits protect Lovdata while
   testing). By default the scraper works year-by-year starting from 1982, so
   the first run may take a little longer while it walks the oldest listings:

   ```bash
   python -m lovtidend --max-pages 1 --limit 20
   ```

Files are stored inside `data/xml`, mirroring the folder structure from the
`/xml/...` URL on lovdata.no. The folder is git-ignored so downloads never pollute
the repository. Re-running the scraper will skip files that are already present
unless `--overwrite` is supplied.

> **Note**: Scraping the entire dataset (80k+ documents) involves a large number
> of HTTP requests. Use the `--limit` and `--max-pages` options while testing to
> stay polite against Lovdata's public servers. The `./scripts/uv.sh` helper used
> above unsets any unrelated `VIRTUAL_ENV` before invoking `uv`, so it is safe to
> jump between multiple projects without seeing warnings. If you prefer to let
> `uv` run commands without activating `.venv`, run `uv run lovtidend …` from a
> shell where no other virtual environment is active (or manually unset
> `VIRTUAL_ENV`).

## Checkpoints and resume

- Progress is automatically stored in `data/lovtidend_checkpoint.json`, and the
  scraper always resumes from this checkpoint when restarted.
- State is synced before and after every page request, so interruptions in the
  middle of a download pick up from the last page without re-processing earlier
  entries.
- Request pacing, jitter, retries, and backoff are handled internally. There is
  no CLI flag to tweak the delay; the scraper already mimics a patient browsing
  session by default.

## Polite scraping and caching

- Listing and document HTML pages are cached inside `data/http_cache` to avoid
  hammering Lovdata with identical requests. Pages from the current year expire
  after a few days (4 days for listings, 20 for documents) while older years are
  kept for much longer. Remove the folder to force a cold scrape.
- The scraper detects truncated pages (“Vis hele dokumentet”) and automatically
  downloads the full `/*` variant before extracting XML links.
- Pagination loops are detected via offsets and page URLs so we never keep
  requesting the same register page if Lovdata changes its navigation.
- Years are processed sequentially. Use `--start-year`/`--end-year` to focus on a
  specific range; the scraper will not advance to the next year until the
  current one finishes (or you stop it), ensuring Lovdata is queried in a
  predictable, polite order.

## Development

Code style and static analysis are handled by
[pre-commit](https://pre-commit.com/). Install the development dependencies and
activate the environment:

```bash
./scripts/uv.sh sync --group dev
source .venv/bin/activate
```

Then install the git hooks:

```bash
pre-commit install
```

Run all checks locally (useful for CI) with:

```bash
pre-commit run --all-files
```
