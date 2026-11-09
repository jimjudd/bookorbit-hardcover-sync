# ABOUTME: Syncs a Hardcover "Want to Read" shelf against a Bookorbit ebook library.
# ABOUTME: Books not owned as an ebook go on a Hardcover list; owned or unwanted books come off it.
import logging
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from rapidfuzz import fuzz

log = logging.getLogger("sync")

HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"
LIST_NAME = "Missing from Bookorbit"
WANT_TO_READ = 1
PRIVATE = 3
AUDIO_FORMATS = {"m4b", "mp3", "m4a", "opus", "ogg", "flac"}
BOOKORBIT_PAGE_SIZE = 200
HARDCOVER_PAGE_SIZE = 100
# Hardcover allows 60 requests a minute.
MUTATION_DELAY = 1.1


def config():
    return {
        "bo_url": os.environ["BOOKORBIT_URL"].rstrip("/"),
        "bo_user": os.environ["BOOKORBIT_USERNAME"],
        "bo_pass": os.environ["BOOKORBIT_PASSWORD"],
        "bo_libraries": [n.strip() for n in os.environ.get("BOOKORBIT_LIBRARIES", "").split(",") if n.strip()],
        "hc_token": os.environ["HARDCOVER_API_TOKEN"],
        "run_time": os.environ.get("RUN_TIME", "03:00"),
        "timezone": os.environ.get("TIMEZONE", "America/New_York"),
        "threshold": float(os.environ.get("SIMILARITY_THRESHOLD", "85")),
        "dry_run": os.environ.get("DRY_RUN", "true").strip().lower() != "false",
    }


def isbn10_to_13(isbn10):
    body = "978" + isbn10[:9]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


def normalize_isbn(isbn):
    """Return an ISBN-13 string, or None if the value is not a usable ISBN."""
    if not isbn:
        return None
    digits = "".join(c for c in isbn if c.isdigit() or c in "xX").upper()
    if len(digits) == 13 and digits.isdigit():
        return digits
    if len(digits) == 10 and digits[:9].isdigit():
        return isbn10_to_13(digits)
    return None


# --- Bookorbit ---

def bookorbit_login(client, cfg):
    r = client.post("/api/v1/auth/login", json={"username": cfg["bo_user"], "password": cfg["bo_pass"]})
    r.raise_for_status()
    client.headers["Authorization"] = "Bearer " + r.json()["accessToken"]


def bookorbit_request(client, cfg, method, path, **kwargs):
    """Make a request, logging in again once if the token has expired."""
    r = client.request(method, path, **kwargs)
    if r.status_code == 401:
        bookorbit_login(client, cfg)
        r = client.request(method, path, **kwargs)
    r.raise_for_status()
    return r.json()


def has_ebook_file(book):
    return any(f["format"].lower() not in AUDIO_FORMATS for f in book.get("files", []))


def fetch_owned_books(cfg):
    """Return Bookorbit books that have at least one ebook file."""
    with httpx.Client(base_url=cfg["bo_url"], timeout=30) as client:
        bookorbit_login(client, cfg)
        libraries = bookorbit_request(client, cfg, "GET", "/api/v1/libraries")
        if cfg["bo_libraries"]:
            wanted = {n.lower() for n in cfg["bo_libraries"]}
            libraries = [lib for lib in libraries if lib["name"].lower() in wanted]
            missing = wanted - {lib["name"].lower() for lib in libraries}
            if missing:
                raise RuntimeError(f"Bookorbit libraries not found: {sorted(missing)}")
        books = {}
        for lib in libraries:
            page = 0
            while True:
                data = bookorbit_request(
                    client, cfg, "POST", f"/api/v1/libraries/{lib['id']}/books",
                    json={"pagination": {"page": page, "size": BOOKORBIT_PAGE_SIZE}},
                )
                for book in data["items"]:
                    books[book["id"]] = book
                page += 1
                if not data["items"] or page * data["size"] >= data["total"]:
                    break
    owned = [b for b in books.values() if has_ebook_file(b)]
    log.info("Bookorbit: %d books, %d with an ebook file", len(books), len(owned))
    return owned


# --- Hardcover ---

class Hardcover:
    def __init__(self, token):
        auth = token if token.lower().startswith("bearer ") else "Bearer " + token
        self.client = httpx.Client(
            base_url=HARDCOVER_URL, timeout=60,
            headers={"Authorization": auth, "User-Agent": "bookorbit-hardcover-sync"},
        )

    def query(self, query, variables=None):
        r = self.client.post("", json={"query": query, "variables": variables or {}})
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise RuntimeError(f"Hardcover error: {body['errors']}")
        return body["data"]

    def want_to_read(self):
        books, offset = [], 0
        while True:
            data = self.query(
                """query($offset: Int!, $limit: Int!) { me { user_books(
                    where: {status_id: {_eq: %d}}, order_by: {id: asc}, limit: $limit, offset: $offset) {
                    book { id slug title contributions { author { name } }
                           editions(limit: 100) { id isbn_10 isbn_13 } } } } }""" % WANT_TO_READ,
                {"offset": offset, "limit": HARDCOVER_PAGE_SIZE},
            )
            page = data["me"][0]["user_books"]
            books += [ub["book"] for ub in page]
            if len(page) < HARDCOVER_PAGE_SIZE:
                break
            offset += HARDCOVER_PAGE_SIZE
        log.info("Hardcover: %d Want to Read books", len(books))
        return books

    def find_list(self):
        """Return (list_id, {book_id: list_book_id}) or (None, {}) if the list does not exist."""
        data = self.query(
            """query($name: String!) { me { lists(where: {name: {_eq: $name}}) {
                id list_books(limit: 10000) { id book_id } } } }""",
            {"name": LIST_NAME},
        )
        lists = data["me"][0]["lists"]
        if not lists:
            return None, {}
        return lists[0]["id"], {lb["book_id"]: lb["id"] for lb in lists[0]["list_books"]}

    def create_list(self):
        data = self.query(
            "mutation($o: ListInput!) { insert_list(object: $o) { id errors } }",
            {"o": {"name": LIST_NAME, "privacy_setting_id": PRIVATE}},
        )["insert_list"]
        if data.get("errors") or not data.get("id"):
            raise RuntimeError(f"Could not create list: {data}")
        time.sleep(MUTATION_DELAY)
        return data["id"]

    def add_to_list(self, list_id, book_id):
        self.query(
            "mutation($o: ListBookInput!) { insert_list_book(object: $o) { id } }",
            {"o": {"list_id": list_id, "book_id": book_id}},
        )
        time.sleep(MUTATION_DELAY)

    def remove_from_list(self, list_book_id):
        self.query("mutation($id: Int!) { delete_list_book(id: $id) { id } }", {"id": list_book_id})
        time.sleep(MUTATION_DELAY)


# --- Matching ---

def authors_of(hc_book):
    return " ".join(c["author"]["name"] for c in hc_book["contributions"] if c.get("author"))


def title_score(a, b):
    """Score two titles, also comparing the part before any subtitle colon."""
    a, b = a.lower(), b.lower()
    scores = [fuzz.token_sort_ratio(a, b)]
    for x, y in ((a.split(":")[0], b), (a, b.split(":")[0]), (a.split(":")[0], b.split(":")[0])):
        scores.append(fuzz.token_sort_ratio(x, y))
    return max(scores)


def similarity(hc_book, owned):
    """Combine title (70%) and author (30%) similarity into one 0-100 score."""
    title = title_score(hc_book["title"], owned["title"])
    author = fuzz.token_set_ratio(authors_of(hc_book).lower(), " ".join(owned.get("authors", [])).lower())
    return 0.7 * title + 0.3 * author


def build_owned_index(owned_books):
    index = {"slugs": set(), "editions": set(), "isbns": set(), "all": owned_books}
    for b in owned_books:
        if b.get("hardcoverId"):
            index["slugs"].add(b["hardcoverId"])
        if b.get("hardcoverEditionId"):
            index["editions"].add(str(b["hardcoverEditionId"]))
        isbn = normalize_isbn(b.get("isbn13"))
        if isbn:
            index["isbns"].add(isbn)
    return index


def match(hc_book, index, threshold):
    """Return a short reason string if the book is owned, otherwise None."""
    if hc_book["slug"] in index["slugs"]:
        return "hardcover id"
    if any(str(e["id"]) in index["editions"] for e in hc_book["editions"]):
        return "hardcover edition"
    isbns = {n for e in hc_book["editions"] for n in (normalize_isbn(e["isbn_13"]), normalize_isbn(e["isbn_10"])) if n}
    if isbns & index["isbns"]:
        return "isbn"
    # Fuzzy fallback against every owned book, because the same book often has a different edition ISBN.
    best = max((similarity(hc_book, o) for o in index["all"]), default=0)
    if best >= threshold:
        return f"fuzzy {best:.0f}"
    return None


# --- Sync ---

def sync(cfg):
    owned = fetch_owned_books(cfg)
    hardcover = Hardcover(cfg["hc_token"])
    want = hardcover.want_to_read()
    list_id, on_list = hardcover.find_list()
    # Everything is read before anything is changed, so a failure leaves the list untouched.

    index = build_owned_index(owned)
    missing = {}
    for book in want:
        reason = match(book, index, cfg["threshold"])
        if reason:
            log.debug("owned (%s): %s", reason, book["title"])
        else:
            missing[book["id"]] = book
    want_ids = {b["id"] for b in want}

    to_add = [b for bid, b in missing.items() if bid not in on_list]
    to_remove = {bid: lb for bid, lb in on_list.items() if bid not in missing}
    titles = {b["id"]: b["title"] for b in want}
    prefix = "[dry-run] would " if cfg["dry_run"] else ""

    if list_id is None and to_add:
        log.info("%screate list '%s'", prefix, LIST_NAME)
        if not cfg["dry_run"]:
            list_id = hardcover.create_list()
    for book in to_add:
        log.info("%sadd to list: %s", prefix, book["title"])
        if not cfg["dry_run"]:
            hardcover.add_to_list(list_id, book["id"])
    for book_id, list_book_id in to_remove.items():
        reason = "now owned" if book_id in want_ids else "no longer Want to Read"
        log.info("%sremove from list (%s): %s", prefix, reason, titles.get(book_id, book_id))
        if not cfg["dry_run"]:
            hardcover.remove_from_list(list_book_id)

    log.info(
        "Summary: dry_run=%s want=%d missing=%d added=%d removed=%d unchanged_on_list=%d",
        cfg["dry_run"], len(want), len(missing), len(to_add), len(to_remove), len(missing) - len(to_add),
    )


def run_once(cfg):
    try:
        sync(cfg)
    except Exception:
        log.exception("Sync failed; skipping this run with no list changes")


def seconds_until(run_time, timezone):
    tz = ZoneInfo(timezone)
    now = datetime.now(tz)
    hour, minute = (int(x) for x in run_time.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = config()
    log.info("Starting (dry_run=%s, daily at %s %s)", cfg["dry_run"], cfg["run_time"], cfg["timezone"])
    run_once(cfg)
    # One loop in one process, so only one sync can run at a time.
    while True:
        wait = seconds_until(cfg["run_time"], cfg["timezone"])
        log.info("Next run in %.1f hours", wait / 3600)
        time.sleep(wait)
        run_once(cfg)


if __name__ == "__main__":
    main()
