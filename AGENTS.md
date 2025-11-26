# Repository Guidelines

## Project Structure & Modules
- `src/lovtidend`: CLI entry point (`__init__.py`), scraper logic (`scraper.py`), checkpointing (`checkpoint.py`), caching (`cache.py`), and console display helpers (`display.py`).
- `tests`: Unit tests built with `unittest`, mirroring scraper edge cases (pagination, checkpoint resume). Add new tests here to cover regressions.
- `scripts/uv.sh`: Wrapper that clears foreign `VIRTUAL_ENV` values and sets `.uv-cache` so `uv` commands stay reproducible.
- `data`: Runtime outputs (XML downloads in `data/xml`, HTTP cache, checkpoint JSON). Git ignores this folder; keep it that way to avoid huge diffs.

## Build, Test, and Development Commands
- Install runtime deps: `./scripts/uv.sh sync`
- Install dev tooling: `./scripts/uv.sh sync --group dev`
- Activate env (one shell session): `source .venv/bin/activate`
- Run the scraper locally: `./scripts/uv.sh run python -m lovtidend --max-pages 1 --limit 20`
- List CLI options: `./scripts/uv.sh run lovtidend --help`
- Run tests: `./scripts/uv.sh run python -m unittest discover`
- Full lint/format sweep: `./scripts/uv.sh run pre-commit run --all-files`

## Coding Style & Naming
- Python 3.12, 4-space indentation, type hints on public functions, and snake_case for functions/variables; classes use PascalCase.
- Ruff handles linting and formatting (via pre-commit). Prefer incremental `ruff check src tests` during edits; let `ruff format` handle layout.
- Keep network-facing code polite and explicit: delays, limits, and cache handling should live in the scraper, not scattered helpers.

## Testing Guidelines
- Add `unittest` cases to `tests/` alongside similar scenarios; name files `test_*.py` and ensure deterministic fixtures (e.g., temporary directories).
- Favor unit-level coverage for pagination, resume logic, caching decisions, and URL generation. Mock HTTP fetches rather than hitting real endpoints.
- Run `python -m unittest` before proposing changes; new features should ship with tests that fail without the change.

## Commit & Pull Request Guidelines
- Use short, imperative summaries (`chore: add pagination guard`, `fix checkpoint resume regression`). Keep scope tight; avoid mixed concerns.
- Include what, why, and how in the PR description. Link issues if relevant; add CLI examples or before/after notes for scraper behavior changes.
- Attach logs or snippets when changing scraping cadence or cache behavior so reviewers can validate expected request counts and file outputs.

## Data & Security Notes
- Do not commit anything under `data/`; the folder may contain cached HTML, checkpoints, or thousands of XML files.
- When testing against lovdata.no, throttle requests with `--limit` and `--max-pages` to avoid unnecessary load; never parallelize fetches.***
