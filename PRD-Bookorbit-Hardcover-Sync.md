# Bookorbit-Hardcover Sync

A small self-hosted service that finds books on a Hardcover "Want to Read" shelf that are missing from a Bookorbit ebook library and marks them on a Hardcover list. Automatic searching and downloading through Shelfmark is a future enhancement.

## Problem and Context

### Context and Background

Jim runs three self-hosted or personal systems that each hold part of his reading workflow:

- Bookorbit (https://github.com/bookorbit/bookorbit): a self-hosted library for ebooks, audiobooks, PDFs and comics. Stack is NestJS, Vue and PostgreSQL. It has no stable public API, but a running instance (v3.6.0) serves a Swagger document at `http://192.168.0.10:7080/api/docs` (JSON at `/api/docs-json`). Auth is a bearer JWT from `POST /api/v1/auth/login`, refreshed with `POST /api/v1/auth/refresh`.
- Hardcover: where Jim tracks books he wants to read. It exposes a GraphQL API authenticated with a personal API token.
- Shelfmark (https://github.com/calibrain/shelfmark): a self-hosted tool that searches release sources and queues ebook downloads. Its API (docs/api-access.md) uses a static key set in `SHELFMARK_API_KEY`, sent as `Authorization: Bearer <key>` or `X-Api-Key`.

Shelfmark has its own Hardcover Sync that turns a shelf into download requests. Jim tried it and found it unreliable, and it does not exclude the hundreds of books he already owns. Bookorbit is also not a documented target of Shelfmark's Library Check. The first build of this service does not talk to Shelfmark. A later version is intended to replace that built-in sync for Jim's setup (see Future Enhancements).

The service is for Jim's own use. It will not be published or shared.

### Problem Statement

Jim keeps a "Want to Read" shelf on Hardcover and a library of ebooks in Bookorbit. Nothing connects them. He has to compare the two by hand to see which wanted books he does not own. The existing Shelfmark-to-Hardcover sync does not fix this, because it cannot tell which books are already in Bookorbit and would request hundreds of duplicates.

The first build solves the comparison for one user, one Bookorbit instance and one Hardcover account. Searching for and downloading the missing books is left to a future enhancement. It covers ebooks only. Audiobooks are out of scope, and an audiobook-only entry in Bookorbit does not count as owning the book.

## Project Objectives

### Use Cases

1. Daily sync. Every night the service compares "Want to Read" on Hardcover against Bookorbit and updates a Hardcover list of missing books.
2. Safe preview. Jim runs the service in dry-run mode, reads the logs, and checks what it would do before enabling live changes.
3. Self-cleaning list. When a book arrives in Bookorbit, or leaves "Want to Read", the service removes it from the Hardcover list.

User stories:

- As Jim, I want books on my Hardcover "Want to Read" shelf that I do not own as an ebook to appear on a list called "Missing from Bookorbit", so that I can see my gaps at a glance.
- As Jim, I want the service to start in dry-run mode and log what it would do, so that I can check its decisions before it changes anything.
- As Jim, I want a run at every container start, so that I can check dry-run results without waiting until 3 AM.
- As Jim, I want books removed from the list once I own them or stop wanting them, so that the list stays accurate without upkeep.

### Acceptance Criteria

Items marked [MVP] are required. Shelfmark searching and downloading is not part of the MVP and is listed under Future Enhancements.

Run triggers and schedule

- [MVP] The service runs inside one Docker container with a small built-in scheduler loop.
- [MVP] A sync runs once at every container start.
- [MVP] A sync runs once a day at a configured time. Default is 03:00 in the configured timezone. Default timezone is America/New_York (proposed, to be confirmed).
- [MVP] Run time and timezone are set through environment variables.
- [MVP] Only one sync runs at a time.

Reading Bookorbit

- [MVP] The service logs in with a username and password from environment variables and refreshes the token as needed.
- [MVP] It reads books from all libraries by default. An environment variable can restrict this to named libraries.
- [MVP] A Bookorbit book counts as owned only if it has at least one ebook file. A book that has only an audiobook file does not count.
- [MVP] For each owned book it collects ISBNs, title and authors. Field names must be checked against the live instance, because the published spec leaves several schemas empty.

Reading Hardcover

- [MVP] The service reads only books with the "Want to Read" status, using the API token from an environment variable.
- [MVP] For each book it collects ISBNs (10 and 13), title and authors.

Matching a Hardcover book to Bookorbit

- [MVP] The Hardcover book ID (slug) or edition ID stored on a Bookorbit book is checked first. A Hardcover book that equals either one is owned.
- [MVP] ISBN is the next match. A Hardcover book matches if any of its ISBNs equals the ISBN of an owned Bookorbit book (after normalizing ISBN-10 and ISBN-13 and stripping hyphens).
- [MVP] If neither ID nor ISBN matches, fall back to a title and author similarity score from 0 to 100 against every owned book, because the same book often has a different edition ISBN. A score at or above the threshold is a match. Default threshold is 85, set through an environment variable.
- [MVP] A borderline score below the threshold counts as not owned and the book goes on the list.

The Hardcover list

- [MVP] The service uses a Hardcover list named "Missing from Bookorbit" and creates it if it does not exist.
- [MVP] A "Want to Read" book that is not owned is added to the list.
- [MVP] A book on the list is removed when it is now owned in Bookorbit.
- [MVP] A book on the list is removed when it is no longer in "Want to Read" (Reading, Read, or removed). Jim assumes he has obtained a physical copy.

Dry-run mode

- [MVP] An environment variable controls dry-run. Default is true.
- [MVP] In dry-run the service reads real data from Bookorbit and Hardcover and logs every action it would take. It does not change the Hardcover list.
- [MVP] When the variable is set to false, the service performs all actions live.

Failures and logging

- [MVP] If Bookorbit or Hardcover is unreachable, or a login fails, the service logs the error and skips the whole run. It makes no list changes on a failed run. It does not retry within the run.
- [MVP] Reporting is logs only. Each run logs a summary of books added to the list, removed from it, and left unmatched, plus whether it ran in dry-run mode.

Configuration (environment variables, final names set during build)

- Bookorbit: URL, username, password, optional library filter.
- Hardcover: API token.
- Schedule: run time (default 03:00), timezone (default America/New_York).
- Matching: similarity threshold (default 85).
- Behavior: dry-run (default true).

Out of scope for the MVP

- Searching Shelfmark and queuing downloads (see Future Enhancements).
- Audiobooks.
- A web UI or notifications.
- Hardcover shelves or lists other than "Want to Read".
- Mock modes. All runs, including dry-run, use real data and real APIs.

Implementation notes

- Python managed with uv, a `pyproject.toml` in the project root, one Dockerfile. Keep it very simple: a small script, few dependencies.
- Tests: Jim authorized skipping tests for this build ("I AUTHORIZE YOU TO SKIP WRITING TESTS THIS TIME"). He has backups and accepts the risk of running against live systems. This applies to this build only.

### Future Enhancements

Shelfmark search and download. The requirements below were drafted for the first build and moved out of the MVP. They are not committed and need review before work starts.

Sending to Shelfmark

- The service authenticates with the key from an environment variable.
- For each missing book it searches Shelfmark for ebook releases (`GET /api/metadata/search`, then `GET /api/releases` with `content_type=ebook`).
- A release is a confident match when its title and author score at or above the similarity threshold and its format is acceptable.
- Format preference is EPUB first. If no EPUB exists, AZW3 and MOBI are equal. PDF is never queued.
- If several releases match at the best available format, the first one Shelfmark lists is queued (`POST /api/releases/download`).
- If there is no confident match, nothing is queued.
- A book is sent to Shelfmark at most once every seven days while it stays missing. A newly listed book is sent on its first run.
- There is no cap on how many books are queued in one run, including the first run.
- A book Shelfmark cannot find is retried about once a week instead of every day.
- Dry-run also performs Shelfmark searches and logs what it would queue, without queuing or writing sent dates.
- The service keeps a SQLite file in a Docker volume with at least the Hardcover book ID and the last-sent date per book.
- Run summaries add counts for queued and skipped books.
- New configuration: Shelfmark URL and API key, SQLite file path. The similarity threshold is shared with ownership matching.
- A failed Shelfmark login or an unreachable Shelfmark skips the whole run.
- Shelfmark's built-in Hardcover Sync is turned off so only this service queues downloads.

### Value Propositions

This is a personal tool with no revenue goal. It saves Jim the manual comparison between Hardcover and Bookorbit and keeps a visible list of gaps on Hardcover. The Shelfmark enhancement would later remove the manual search for each missing book.

### Goals/Measurable Outcomes

- Zero duplicate downloads of books already in Bookorbit as an ebook.
- After each live run, the "Missing from Bookorbit" list matches the actual gaps between "Want to Read" and Bookorbit.
- The daily run completes with no manual intervention for 30 days in a row.

## Open Issues & Key Decisions

Key decisions

- Build a separate service instead of using Shelfmark's Hardcover Sync, because Shelfmark's sync is not robust and does not exclude owned books. Owner: Jim.
- The MVP covers Hardcover and Bookorbit syncing only. Shelfmark search and download is a future enhancement.
- Borderline fuzzy matches are treated as not owned and go on the list.
- A failed run is skipped entirely, so a Bookorbit outage cannot make every book look missing.
- Books leaving "Want to Read" are removed from the list.
- Tests are skipped for this build, by Jim's explicit authorization.

Open issues

| Issue | Owner |
|---|---|
| Confirm the default timezone (America/New_York proposed). | Jim |
| Bookorbit's API is not a stable public interface and may change on upgrade. Check the actual book and file payloads, including ISBN fields and file formats, against the live instance. | Developer |
| Future enhancement: Shelfmark's `POST /api/releases/download` and `/api/status` responses are not documented. Check them against the live instance before building it. | Developer |
| Confirm that Hardcover's GraphQL API allows creating a list and adding and removing books with a personal token, and which fields give ISBNs and the "Want to Read" status. | Developer |
| Choose a fuzzy matching library and how title and author scores combine into one score. | Developer |
| Shelfmark's Library Check may be able to be pointed at Bookorbit later. Not needed for this build. | Jim |
| Without Shelfmark, the MVP no longer needs SQLite for last-sent dates. I removed it from the MVP. Confirm you do not want it for anything else. | Jim |
| Until the Shelfmark enhancement ships, Shelfmark's built-in Hardcover Sync is not replaced. Decide whether to leave it on or off in the meantime. | Jim |
