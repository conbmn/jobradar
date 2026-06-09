"""Notion discovery renderer — upsert matching roles into a Notion DB (DESIGN §4.6).

Closes the loop: companies in (sources/notion), roles out (here), triaged in place
via a Status column. The upsert key is `company::job_id` — stable across title edits —
so re-running updates rows in place without duplicating them, and **never touches the
human's Status** (only set once, to 'New', on first insert).

Plain Notion REST via `requests` (no SDK). Token from env.
"""
import os

import requests

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"
_TEXT_CAP = 2000  # Notion rich_text content limit per text object


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": _VERSION,
        "Content-Type": "application/json",
    }


def _title(s: str) -> dict:
    return {"title": [{"text": {"content": (s or "")[:_TEXT_CAP]}}]}


def _rich(s: str) -> dict:
    return {"rich_text": [{"text": {"content": (s or "")[:_TEXT_CAP]}}]}


def _existing_rows(token: str, database_id: str) -> dict:
    """Map Key → {id, status} for rows already in the roles DB (paginated)."""
    out: dict = {}
    cursor = None
    while True:
        payload: dict = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        resp = requests.post(
            f"{_API}/databases/{database_id}/query",
            headers=_headers(token), json=payload, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        for page in data.get("results", []):
            props = page.get("properties", {})
            key = "".join(t.get("plain_text", "") for t in (props.get("Key") or {}).get("rich_text", []))
            status = ((props.get("Status") or {}).get("select") or {}).get("name")
            if key:
                out[key] = {"id": page["id"], "status": status}
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return out


def _ensure_property(token: str, database_id: str, name: str, definition: dict) -> None:
    """Add a property to an existing roles DB if it's missing, so databases created
    before a new field (e.g. 'First seen') upgrade in place on the next render."""
    resp = requests.get(f"{_API}/databases/{database_id}", headers=_headers(token), timeout=20)
    resp.raise_for_status()
    if name in resp.json().get("properties", {}):
        return
    requests.patch(
        f"{_API}/databases/{database_id}", headers=_headers(token),
        json={"properties": {name: definition}}, timeout=20,
    ).raise_for_status()


def create_roles_database(token: str, parent_page_id: str) -> str:
    """Create the Roles database under a parent page; return its id."""
    body = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "jobradar — Roles"}}],
        "properties": {
            "Role": {"title": {}},
            "Company": {"select": {"options": []}},  # auto-populated on write
            "Status": {"select": {"options": [
                {"name": "New", "color": "blue"}, {"name": "Interested", "color": "yellow"},
                {"name": "Applied", "color": "green"}, {"name": "Pass", "color": "gray"},
            ]}},
            "Score": {"number": {}},
            "Location": {"rich_text": {}},
            "Reason": {"rich_text": {}},
            "Apply": {"url": {}},
            "First seen": {"date": {}},  # when jobradar first saw this role — sort a view by this
            "Key": {"rich_text": {}},
        },
    }
    resp = requests.post(f"{_API}/databases", headers=_headers(token), json=body, timeout=20)
    resp.raise_for_status()
    return resp.json()["id"]


def render_notion(rows, database_id: str, token: str = "") -> int:
    """Upsert matching role rows into the Notion roles DB. Returns rows written."""
    token = token or os.environ.get("NOTION_TOKEN", "")
    if not token:
        raise RuntimeError("Notion roles render requires NOTION_TOKEN in .env.local.")

    _ensure_property(token, database_id, "First seen", {"date": {}})
    existing = _existing_rows(token, database_id)
    current_keys = set()
    count = 0
    for r in rows:
        key = f"{r['company']}::{r['job_id']}"
        current_keys.add(key)
        # first_seen is stable per job_id, so writing it on every render is idempotent
        # and backfills rows created before this field existed.
        first_seen = r["first_seen"] if "first_seen" in r.keys() else None
        props = {
            "Role": _title(r["title"]),
            "Company": {"select": {"name": (r["company"] or "")[:100]}},
            "Location": _rich(r["location"]),
            "Score": {"number": round(float(r["match_score"] or 0.0), 2)},
            "Reason": _rich(r["match_reason"]),
            "Apply": {"url": r["url"] or None},
            "First seen": {"date": {"start": first_seen} if first_seen else None},
            "Key": _rich(key),
        }
        info = existing.get(key)
        if info:
            # Update details; deliberately leave Status as the human set it.
            resp = requests.patch(
                f"{_API}/pages/{info['id']}",
                headers=_headers(token), json={"properties": props}, timeout=20,
            )
        else:
            props["Status"] = {"select": {"name": "New"}}
            resp = requests.post(
                f"{_API}/pages",
                headers=_headers(token),
                json={"parent": {"database_id": database_id}, "properties": props},
                timeout=20,
            )
        resp.raise_for_status()
        count += 1

    # Prune rows that are no longer matches — but only untriaged ones, so any role
    # you've marked (Interested / Applied / Pass) is kept as a record.
    for key, info in existing.items():
        if key not in current_keys and (info["status"] in (None, "New")):
            requests.patch(
                f"{_API}/pages/{info['id']}",
                headers=_headers(token), json={"archived": True}, timeout=20,
            ).raise_for_status()
    return count
