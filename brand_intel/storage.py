"""
SQLite history so the dashboard can show trends across scans and keep
an evidence log. The file lives in data/ (set BRAND_INTEL_DB to move it).
On a free Hugging Face Space this disk resets on restart unless you
enable persistent storage and point BRAND_INTEL_DB at /data.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .core import ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY, ran_at TEXT, query TEXT, sources TEXT,
    n_items INTEGER, avg_score REAL
);
CREATE TABLE IF NOT EXISTS items (
    scan_id INTEGER, title TEXT, url TEXT, source TEXT, platform TEXT,
    published TEXT, engagement INTEGER, score REAL, topics TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY, captured_at TEXT, url TEXT, title TEXT,
    sha256 TEXT, wayback_url TEXT, note TEXT
);
"""


def db_path() -> Path:
    return Path(os.environ.get("BRAND_INTEL_DB", ROOT / "data" / "brand_intel.db"))


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def save_scan(query: str, sources: list[str], items, avg: float) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO scans (ran_at, query, sources, n_items, avg_score) VALUES (?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), query,
             ", ".join(sources), len(items), avg),
        )
        scan_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?)",
            [(scan_id, i.title, i.url, i.source, i.platform, i.published,
              i.engagement, i.score, json.dumps(i.topics)) for i in items],
        )
    return scan_id


def history(query: str) -> list[tuple[str, float, int]]:
    with connect() as conn:
        return conn.execute(
            "SELECT ran_at, avg_score, n_items FROM scans WHERE lower(query)=lower(?) ORDER BY ran_at",
            (query,),
        ).fetchall()


def save_evidence(rec: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO evidence (captured_at, url, title, sha256, wayback_url, note) VALUES (?,?,?,?,?,?)",
            (rec["captured_at"], rec["url"], rec["title"], rec["sha256"], rec["wayback_url"], rec["note"]),
        )


def evidence() -> list[tuple]:
    with connect() as conn:
        return conn.execute(
            "SELECT captured_at, url, title, sha256, wayback_url, note FROM evidence ORDER BY id DESC"
        ).fetchall()
