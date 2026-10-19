import re
import sqlite3
import time
from pathlib import Path

import requests

DB_PATH = "library.db"
ISBN_FILE = "isbn13_current.txt"
COVERS_DIR = Path("Covers")  # the folder you already created
HEADERS = {"User-Agent": "PersonalLibrary/1.0 (noahaguillon03@gmail.com)"}  # put your own contact here
PAUSE = 0.4  # seconds between requests, to stay polite to Open Library

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    isbn13 TEXT UNIQUE,                -- TEXT, nullable: books without an ISBN can still be added
    title TEXT NOT NULL,
    author TEXT,
    publisher TEXT,
    year INTEGER,
    pages INTEGER,
    genre TEXT,
    cover_url TEXT,
    cover_path TEXT,                   -- relative to COVERS_DIR
    format TEXT CHECK (format IN ('Hardcover','Paperback','Ebook','Audiobook','Other')),
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('isbn13','manual'))
);

CREATE TABLE IF NOT EXISTS reads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    read_date DATE NOT NULL,
    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
    notes TEXT,
    FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reads_book ON reads (book_id);
"""


def get(url, **params):
    """GET with a pause; returns the response, or None on 404."""
    r = requests.get(url, headers=HEADERS, params=params or None, timeout=20)
    time.sleep(PAUSE)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r


def normalize_format(raw):
    """Map Open Library's free-text physical_format onto our allowed values."""
    if not raw:
        return None
    s = raw.lower()
    if any(w in s for w in ("hardcover", "hardback", "library binding")):
        return "Hardcover"
    if any(w in s for w in ("paperback", "mass market", "softcover", "trade paper")):
        return "Paperback"
    if any(w in s for w in ("ebook", "electronic", "kindle")):
        return "Ebook"
    if "audio" in s:
        return "Audiobook"
    return "Other"


def parse_year(text):
    m = re.search(r"\d{4}", text or "")
    return int(m.group()) if m else None


def names(items):
    """Join a list of {'name': ...} dicts (or plain strings) into one string."""
    out = []
    for it in items or []:
        out.append(it.get("name", "") if isinstance(it, dict) else str(it))
    return ", ".join(n for n in out if n)


def fetch_book(isbn):
    """Return a dict of book fields, or None if Open Library doesn't know the ISBN."""
    r = get("https://openlibrary.org/api/books",
            bibkeys=f"ISBN:{isbn}", format="json", jscmd="data")
    data = (r.json() if r else {}).get(f"ISBN:{isbn}")
    if not data:
        return None

    # The edition record is where physical_format lives.
    # An ISBN identifies one specific edition, so this describes the copy you own.
    fmt_raw = None
    er = get(f"https://openlibrary.org/isbn/{isbn}.json")
    if er:
        fmt_raw = er.json().get("physical_format")

    return {
        "isbn13": isbn,
        "title": data.get("title") or "Unknown Title",
        "author": names(data.get("authors")),
        "publisher": names(data.get("publishers")),
        "year": parse_year(data.get("publish_date")),
        "pages": data.get("number_of_pages"),
        "genre": ", ".join(s["name"] if isinstance(s, dict) else str(s)
                           for s in (data.get("subjects") or [])[:5]),
        "cover_url": (data.get("cover") or {}).get("large"),
        "format": normalize_format(fmt_raw),
        "source": "isbn13",
    }


def download_cover(isbn, cover_url):
    """Save the cover into COVERS_DIR; return the file name, or None if unavailable."""
    urls = [cover_url] if cover_url else []
    urls.append(f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false")
    for url in urls:
        r = get(url)
        if r is None:
            continue
        if not r.headers.get("Content-Type", "").startswith("image/") or len(r.content) < 1000:
            continue  # placeholder or error page, not a real cover
        path = COVERS_DIR / f"{isbn}.jpg"
        path.write_bytes(r.content)
        return path.name
    return None


def load_isbns(path):
    seen, out = set(), []
    for line in Path(path).read_text().splitlines():
        isbn = re.sub(r"[^0-9Xx]", "", line)  # strips spaces and hyphens
        if len(isbn) != 13:
            if isbn:
                print(f"Skipping malformed ISBN: {line!r}")
            continue
        if isbn not in seen:
            seen.add(isbn)
            out.append(isbn)
    return out


def main():
    COVERS_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)  # executescript allows multiple statements

    not_found = []
    for isbn in load_isbns(ISBN_FILE):
        row = conn.execute("SELECT id, cover_path FROM books WHERE isbn13 = ?", (isbn,)).fetchone()

        if row is None:
            book = fetch_book(isbn)
            if book is None:
                not_found.append(isbn)
                print(f"Not found: {isbn}")
                continue
            cols = ", ".join(book)
            marks = ", ".join("?" for _ in book)
            cur = conn.execute(f"INSERT INTO books ({cols}) VALUES ({marks})", tuple(book.values()))
            book_id, cover_path, cover_url = cur.lastrowid, None, book["cover_url"]
            print(f"Added: {book['title']} [{book['format']}]")
        else:
            book_id, cover_path = row
            cover_url = None

        if not cover_path:  # new book, or an earlier run that failed to get a cover
            saved = download_cover(isbn, cover_url)
            if saved:
                conn.execute("UPDATE books SET cover_path = ? WHERE id = ?", (saved, book_id))

        conn.commit()

    conn.close()
    if not_found:
        print("\nAdd these by hand:", *not_found, sep="\n  ")


if __name__ == "__main__":
    main()