"""State store: insert/dedup/diff core (DESIGN §5–§7). In-memory SQLite."""
import pytest

from jobradar.store import (
    init_db, upsert_posting, mark_closed, close_unwatched,
    company_has_rows, mark_notified, open_matching_jobs,
)


@pytest.fixture
def conn():
    return init_db(":memory:")


def test_upsert_is_new_then_idempotent(conn):
    assert upsert_posting(conn, "Acme", "1", "Data Scientist", "Berlin", "u", 3.0, "ok") is True
    # Same job_id again → not new (an update, no re-alert)
    assert upsert_posting(conn, "Acme", "1", "Data Scientist (renamed)", "Berlin", "u", 4.0, "ok") is False
    rows = open_matching_jobs(conn, threshold=0.0)
    assert len(rows) == 1
    assert rows[0]["title"] == "Data Scientist (renamed)"  # display fields refreshed
    assert rows[0]["match_score"] == 4.0                   # re-scored


def test_company_has_rows(conn):
    assert company_has_rows(conn, "Acme") is False
    upsert_posting(conn, "Acme", "1", "t", "Berlin", "u", 1.0, "ok")
    assert company_has_rows(conn, "Acme") is True


def test_open_matching_jobs_threshold_and_order(conn):
    upsert_posting(conn, "Acme", "1", "low", "Berlin", "u", 1.0, "ok")
    upsert_posting(conn, "Acme", "2", "high", "Berlin", "u", 5.0, "ok")
    top = open_matching_jobs(conn, threshold=2.0)
    assert [r["job_id"] for r in top] == ["2"]  # below-threshold dropped, ranked desc


def test_mark_closed_closes_absent_ids(conn):
    upsert_posting(conn, "Acme", "1", "t", "Berlin", "u", 3.0, "ok")
    upsert_posting(conn, "Acme", "2", "t", "Berlin", "u", 3.0, "ok")
    closed = mark_closed(conn, "Acme", seen_ids={"1"})  # job 2 vanished from the board
    assert closed == 1
    assert {r["job_id"] for r in open_matching_jobs(conn)} == {"1"}


def test_mark_closed_empty_closes_all(conn):
    upsert_posting(conn, "Acme", "1", "t", "Berlin", "u", 3.0, "ok")
    assert mark_closed(conn, "Acme", seen_ids=set()) == 1
    assert open_matching_jobs(conn) == []


def test_close_unwatched(conn):
    upsert_posting(conn, "Keep", "1", "t", "Berlin", "u", 3.0, "ok")
    upsert_posting(conn, "Drop", "1", "t", "Berlin", "u", 3.0, "ok")
    closed = close_unwatched(conn, watched={"Keep"})
    assert closed == 1
    assert {r["company"] for r in open_matching_jobs(conn)} == {"Keep"}


def test_mark_notified_preserved_across_upsert(conn):
    upsert_posting(conn, "Acme", "1", "t", "Berlin", "u", 3.0, "ok")
    mark_notified(conn, "Acme", "1")
    upsert_posting(conn, "Acme", "1", "t", "Berlin", "u", 3.0, "ok")  # steady-state re-scrape
    row = conn.execute("SELECT notified FROM jobs WHERE job_id='1'").fetchone()
    assert row["notified"] == 1  # not reset → no duplicate alert
