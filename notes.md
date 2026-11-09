# Notes: Bookorbit-Hardcover Sync

## Source idea (context.md)
Self-hosted Docker sync service, personal use only. Runs about once a day:
1. List books in Bookorbit (https://github.com/bookorbit/bookorbit)
2. List "Want to Read" books on Hardcover
3. Tag Hardcover want-to-read books that are not in Bookorbit
4. Send those books to Shelfmark to look for a downloadable copy

## Decisions
- No existing PRD in Confluence or in this folder (2026-11-09).

- Matching: ISBN is the primary match. If ISBN is missing, fall back to title + author fuzzy match with a reasonable threshold (threshold value TBD).

- Borderline fuzzy matches are treated as non-matches and sent to Shelfmark. Why: Bookorbit has a post-download review process that catches false matches, so a duplicate download is cheap.

- Hardcover marker: a custom list. The service creates it if missing. Name confirmed by Jim: "Missing from Bookorbit".
- When a book on that list later appears in Bookorbit, the service removes it from the list.

- Shelfmark retries: a missing book is sent to Shelfmark at most once per week while it stays missing from Bookorbit. Newly listed books are sent on the first run. The service needs to persist the last-sent date per book.

- State storage: a small SQLite file in a Docker volume (interpreted from "small sql file"; confirm if a different SQL engine was meant).

- Bookorbit access: read through its HTTP API. The README says the API is not a stable public integration surface, so breakage on upgrade is a known risk. Spec lives at http://192.168.0.10:7080/api/docs (JSON at /api/docs-json, version v3.6.0).
  - Auth: bearer JWT. POST /api/v1/auth/login, POST /api/v1/auth/refresh. No API key endpoint seen.
  - Useful endpoints: GET /api/v1/libraries, POST /api/v1/libraries/{id}/books, POST /api/v1/books/query, GET /api/v1/books/search, GET /api/v1/books/{id}.
  - The published spec has empty request/response schemas for several endpoints (LoginDto, books/query), so the real payloads and ISBN field names must be verified against the live instance.
  - Other Bookorbit facts: stack is NestJS + Vue + PostgreSQL; it already has a built-in Hardcover sync (relevance to this project not yet discussed).

- Bookorbit credentials: username and password supplied to the container via environment variables. Whether it is a dedicated account or Jim's own was not specified; either works with this design.

- Shelfmark API (docs/api-access.md): static key set via SHELFMARK_API_KEY env var, sent as `Authorization: Bearer <key>` or `X-Api-Key`. Key acts as the first admin user. Flow to queue a book:
  1. GET /api/metadata/search?query=... returns books with provider and provider_id
  2. GET /api/releases?provider=...&book_id=...&content_type=ebook returns candidate releases
  3. POST /api/releases/download with one release object (source and source_id required)
  - Response bodies for download and status are not documented; verify against the live instance. Health check: GET /api/health (no auth).
- Overlap found: Shelfmark has its own Hardcover Sync (docs/hardcover-sync.md). It reads a Hardcover shelf (default "Want to Read"), skips books already in libraries enabled under Library Check, creates requests, can auto-download on a strict match, retries failures, and backs off on unmatched requests. Default interval is 6 hours. Library Check documented targets: Calibre, Calibre-Web, CWA, Grimmory, Audiobookshelf. Bookorbit was not named in what was reviewed (docs/library-check.md not yet read).

- Why build this despite the overlap: Jim wants the specific "Missing" list on Hardcover, and Shelfmark's built-in Hardcover sync is not robust and does not exclude the hundreds of books already owned. Jim confirmed he will turn off Shelfmark's built-in Hardcover Sync, so this service is the only thing queuing downloads.

- Shelfmark behavior: the service queues a download only when there is a confident match (title, author, format). Otherwise it does not queue, and the book is retried on the weekly schedule. Exact confidence rules still to define.

- Scope: ebooks only (content_type=ebook). Audiobooks are out of scope.

- Ownership: only a Bookorbit book with an ebook file counts as owned. An audiobook-only entry does not, so that title goes on the Missing list and the service looks for an ebook. The Bookorbit fetch must therefore check file formats, not just book records.

- Hardcover input: only the "Want to Read" reading status. No other shelves or lists.

- Status change: if a book leaves "Want to Read" on Hardcover (Reading, Read, removed), the service removes it from the Missing list and stops sending it to Shelfmark. Why: Jim assumes he has obtained a physical copy.

- Schedule: once daily, overnight. Run time and timezone are both set through environment variables. Default run time is 03:00 (confirmed by Jim). Default timezone is America/New_York (proposed, not explicitly confirmed).
- Reporting: logs only, no notifications. Logs should summarize books added to the list, removed, queued, and skipped.

- Implementation: Python managed with uv, SQLite for state, packaged as one Docker container. Keep it very simple: a small script with a built-in scheduler loop, minimal dependencies.

- Failure handling: if Bookorbit, Hardcover, or Shelfmark is unreachable or authentication fails, log the error, skip the whole run, and try again at the next scheduled time. No changes to the Hardcover list on a failed run, so an outage cannot make every book look missing. No in-run retries.

- First run and volume: no cap on Shelfmark queues per run. The first run queues every confident match.

- Testing: Jim gave the exact authorization phrase "I AUTHORIZE YOU TO SKIP WRITING TESTS THIS TIME" for this project, so no tests are required for this build. Jim has backups and accepts data loss risk from running against live Bookorbit, Hardcover, and Shelfmark. No mock mode. The authorization covers this time only.

- Bookorbit libraries: all libraries are checked by default. An environment variable can restrict the check to specific libraries.

- Confident match for a Shelfmark release: title and author match the Hardcover book at or above the fuzzy threshold (proposed, not yet confirmed to share the Bookorbit threshold), and the format is acceptable. Format preference order: EPUB, then AZW3 or MOBI if no EPUB exists. AZW3 and MOBI are treated as equal (confirmed). PDF is never queued. Among equal-format matches, the first release Shelfmark lists is used (proposed, not yet confirmed).

- Fuzzy threshold: one shared environment variable for both the Bookorbit ownership match and the Shelfmark release match. Title and author similarity score on a 0 to 100 scale, default 85 (confirmed).

- Credentials summary: Hardcover API token, Bookorbit username and password, and Shelfmark API key are all supplied as environment variables.

- Success measures (confirmed): zero duplicate downloads of books already in Bookorbit; the Missing list matches actual gaps after each run; the daily run completes without manual intervention for 30 days in a row.

- Dry-run: controlled by an environment variable, default true. When true, the service reads real data from the real systems and logs what it would do, but makes no changes. When set to false, it syncs live. This is not a mock mode (real data and real APIs throughout), so it does not conflict with the no-mock rule.
- Startup run: a sync runs once whenever the container starts, in addition to the daily scheduled run, so dry-run results can be checked without waiting for 3 AM.

- Dry-run still searches Shelfmark (read-only) so logs show which books would be queued. It does not queue, change the Hardcover list, or write sent dates to SQLite.

## Status
- Interview complete (30 questions). PRD drafted to PRD-Bookorbit-Hardcover-Sync.md.

## Open questions
- Confirm default timezone (America/New_York proposed).
- Bookorbit's built-in Hardcover sync: does it overlap with this service, and should it stay on?
- (in progress)
