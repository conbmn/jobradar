import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DB = Path(__file__).parent.parent / "jobradar.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    company       TEXT NOT NULL,
    job_id        TEXT NOT NULL,
    title         TEXT NOT NULL,
    location      TEXT,
    url           TEXT,
    content_hash  TEXT,
    match_score   REAL,
    match_reason  TEXT,
    status        TEXT DEFAULT 'open',
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    notified      INTEGER DEFAULT 0,
    PRIMARY KEY (company, job_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    companies     INTEGER,
    new_jobs      INTEGER,
    closed_jobs   INTEGER,
    alerted       INTEGER
);
"""


def init_db(path: Path = _DEFAULT_DB) -> sqlite3.Connection:
    """Open (or create) the SQLite DB at path and apply the schema."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def company_has_rows(conn: sqlite3.Connection, company: str) -> bool:
    """Return True if the company already has any rows in the DB (not in seed mode)."""
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE company = ? LIMIT 1", (company,)
    ).fetchone()
    return row is not None


def upsert_posting(
    conn: sqlite3.Connection,
    company: str,
    job_id: str,
    title: str,
    location: str,
    url: str,
    match_score: float,
    match_reason: str,
) -> bool:
    """Insert the posting if new; update last_seen if existing.

    Returns True if this was a new insertion, False if it already existed.
    Uses INSERT OR IGNORE so rowcount==1 reliably means a new row.
    """
    ts = now_iso()
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO jobs
            (company, job_id, title, location, url,
             match_score, match_reason, status, first_seen, last_seen, notified)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, 0)
        """,
        (company, job_id, title, location, url, match_score, match_reason, ts, ts),
    )
    is_new = cursor.rowcount == 1
    if not is_new:
        # Refresh display fields + re-score from current config so the discovery
        # view re-ranks after a matcher tweak (DESIGN §4.6). Match is on job_id,
        # so this never causes re-alerts. first_seen and notified are preserved.
        conn.execute(
            """
            UPDATE jobs
            SET last_seen = ?, status = 'open',
                title = ?, location = ?, url = ?,
                match_score = ?, match_reason = ?
            WHERE company = ? AND job_id = ?
            """,
            (ts, title, location, url, match_score, match_reason, company, job_id),
        )
    conn.commit()
    return is_new


def mark_closed(conn: sqlite3.Connection, company: str, seen_ids: set[str]) -> int:
    """Mark any open rows for company whose job_id is not in seen_ids as closed.

    Returns the count of newly-closed rows.
    """
    if not seen_ids:
        conn.execute(
            "UPDATE jobs SET status='closed' WHERE company=? AND status='open'",
            (company,),
        )
        conn.commit()
        return conn.execute(
            "SELECT changes()"
        ).fetchone()[0]

    placeholders = ",".join("?" * len(seen_ids))
    conn.execute(
        f"UPDATE jobs SET status='closed' WHERE company=? AND status='open' AND job_id NOT IN ({placeholders})",
        (company, *seen_ids),
    )
    conn.commit()
    return conn.execute("SELECT changes()").fetchone()[0]


def close_unwatched(conn: sqlite3.Connection, watched: set[str]) -> int:
    """Close open rows for companies no longer in the watch list (e.g. removed from
    Notion). Otherwise their roles linger as 'open' forever. Returns rows closed."""
    open_companies = [
        r[0] for r in conn.execute("SELECT DISTINCT company FROM jobs WHERE status='open'")
    ]
    stale = [c for c in open_companies if c not in watched]
    if not stale:
        return 0
    placeholders = ",".join("?" * len(stale))
    conn.execute(
        f"UPDATE jobs SET status='closed' WHERE status='open' AND company IN ({placeholders})",
        stale,
    )
    conn.commit()
    return conn.execute("SELECT changes()").fetchone()[0]


def mark_notified(conn: sqlite3.Connection, company: str, job_id: str) -> None:
    """Set notified=1 for a specific posting."""
    conn.execute(
        "UPDATE jobs SET notified = 1 WHERE company = ? AND job_id = ?",
        (company, job_id),
    )
    conn.commit()


def open_matching_jobs(conn: sqlite3.Connection, threshold: float = 0.0):
    """Return all open jobs with match_score >= threshold, ranked by score desc."""
    return conn.execute(
        """
        SELECT company, job_id, title, location, match_score, match_reason, url, first_seen
        FROM jobs
        WHERE status = 'open' AND match_score >= ?
        ORDER BY match_score DESC
        """,
        (threshold,),
    ).fetchall()
