"""Notion companies source — the friendly human surface (DESIGN §4.1).

Reads the company list from a Notion database and writes detected ats/slug back
into the rows. Plain Notion REST via `requests` (no SDK). Token from env.

Expected database columns (created by `setup-notion`, or by hand):
  Company      (title)      → name
  Careers URL  (url)        → careers_url
  ATS          (select)     → ats   ('auto' or empty means "let detect decide")
  Slug         (rich_text)  → slug  (machine-filled)
"""
import os
import re

import requests

from jobradar.sources.base import CompaniesSource, CompanyRow

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"


def _api_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": _VERSION,
        "Content-Type": "application/json",
    }


def extract_page_id(url_or_id: str) -> str:
    """Pull the 32-hex Notion id out of a page URL or raw id.

    Handles both the dashed-UUID form and the bare 32-hex form that Notion URLs
    append after the title slug. Dashes in a URL separate the slug from the id, so
    we must NOT strip them before matching (that would merge slug into the id).
    """
    s = (url_or_id or "").strip()
    uuid = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", s)
    if uuid:
        return uuid.group(0).replace("-", "")
    runs = re.findall(r"[0-9a-fA-F]{32}", s)
    if runs:
        return runs[-1]
    raise ValueError(f"Could not find a Notion page id in {url_or_id!r}")


# ATS dropdown options for the Companies database. 'auto' = let detect decide.
_ATS_OPTIONS = [
    {"name": "auto", "color": "gray"}, {"name": "greenhouse", "color": "green"},
    {"name": "lever", "color": "blue"}, {"name": "ashby", "color": "purple"},
    {"name": "personio", "color": "orange"}, {"name": "workday", "color": "red"},
    {"name": "smartrecruiters", "color": "pink"}, {"name": "recruitee", "color": "yellow"},
]

# Detect Status options (mirror jobradar.detector STATUS_* strings). The detector
# writes one of these back after each detect, so the unfetchable rows are visible.
_STATUS_PROP = "Detect Status"
_STATUS_OPTIONS = [
    {"name": "✓ ready", "color": "green"},
    {"name": "⚠ no adapter", "color": "yellow"},
    {"name": "✗ not detected", "color": "red"},
    {"name": "✗ fetch failed", "color": "red"},
]


def create_companies_database(token: str, parent_page_id: str) -> str:
    """Create the Companies database under a parent page; return its id."""
    body = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "jobradar — Companies"}}],
        "properties": {
            "Company": {"title": {}},
            "Careers URL": {"url": {}},
            "ATS": {"select": {"options": _ATS_OPTIONS}},
            "Slug": {"rich_text": {}},
            _STATUS_PROP: {"select": {"options": _STATUS_OPTIONS}},
            "Notes": {"rich_text": {}},
        },
    }
    resp = requests.post(f"{_API}/databases", headers=_api_headers(token), json=body, timeout=20)
    resp.raise_for_status()
    return resp.json()["id"]


def seed_example_company(token: str, database_id: str) -> None:
    """Add one example row so the format is obvious (safe to delete)."""
    props = {
        "Company": {"title": [{"text": {"content": "Helsing (example — edit or delete)"}}]},
        "Careers URL": {"url": "https://helsing.ai/jobs"},
        "ATS": {"select": {"name": "auto"}},
    }
    resp = requests.post(
        f"{_API}/pages", headers=_api_headers(token),
        json={"parent": {"database_id": database_id}, "properties": props}, timeout=20,
    )
    resp.raise_for_status()


def _title(prop) -> str:
    if not prop:
        return ""
    return "".join(t.get("plain_text", "") for t in prop.get("title", [])).strip()


def _text(prop) -> str:
    if not prop:
        return ""
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text", [])).strip()


def _select(prop) -> str:
    sel = (prop or {}).get("select")
    return (sel.get("name") or "").strip() if sel else ""


def _url(prop) -> str:
    return ((prop or {}).get("url") or "").strip()


class NotionCompaniesSource(CompaniesSource):
    def __init__(self, database_id: str, token: str = ""):
        self.database_id = database_id
        self.token = token or os.environ.get("NOTION_TOKEN", "")
        if not self.token:
            raise RuntimeError(
                "companies_source is 'notion' but NOTION_TOKEN is not set in .env.local."
            )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": _VERSION,
            "Content-Type": "application/json",
        }

    def load(self) -> list[CompanyRow]:
        rows: list[CompanyRow] = []
        cursor = None
        while True:
            payload: dict = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            resp = requests.post(
                f"{_API}/databases/{self.database_id}/query",
                headers=self._headers(),
                json=payload,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            for page in data.get("results", []):
                props = page.get("properties", {})
                name = _title(props.get("Company"))
                if not name:
                    continue
                ats = _select(props.get("ATS"))
                if ats.lower() == "auto":
                    ats = ""  # 'auto' is the human-facing "let detect decide"
                rows.append(CompanyRow(
                    name=name,
                    careers_url=_url(props.get("Careers URL")),
                    ats=ats,
                    slug=_text(props.get("Slug")),
                    handle=page["id"],
                ))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return rows

    def _ensure_status_property(self) -> None:
        """Add the Detect Status select column to an existing DB if it's missing,
        so databases created before this feature upgrade in place on first detect."""
        resp = requests.get(
            f"{_API}/databases/{self.database_id}", headers=self._headers(), timeout=20)
        resp.raise_for_status()
        if _STATUS_PROP in resp.json().get("properties", {}):
            return
        patch = requests.patch(
            f"{_API}/databases/{self.database_id}", headers=self._headers(),
            json={"properties": {_STATUS_PROP: {"select": {"options": _STATUS_OPTIONS}}}},
            timeout=20,
        )
        patch.raise_for_status()

    def save(self, rows: list[CompanyRow]) -> None:
        """Patch ats/slug/status onto the Notion pages the detector actually changed."""
        if any(r.dirty and r.status for r in rows):
            self._ensure_status_property()
        for row in rows:
            if not row.dirty or not row.handle:
                continue
            props: dict = {}
            if row.ats:
                props["ATS"] = {"select": {"name": row.ats}}
            if row.slug:
                props["Slug"] = {"rich_text": [{"text": {"content": row.slug}}]}
            if row.status:
                props[_STATUS_PROP] = {"select": {"name": row.status}}
            if not props:
                continue
            resp = requests.patch(
                f"{_API}/pages/{row.handle}",
                headers=self._headers(),
                json={"properties": props},
                timeout=20,
            )
            resp.raise_for_status()
