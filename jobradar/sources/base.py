"""Pluggable companies source (DESIGN §4.1).

The pipeline is agnostic to where the company list comes from. A source exposes:
- load()         → the rows
- save(rows)     → persist detector output (ats/slug) back to the source

Rows carry an opaque `handle` for write-back (CSV: the name; Notion: the page id)
and a transient `dirty` flag the detector sets on rows it changed, so a source can
persist only what actually changed (Notion) while CSV rewrites the whole file.
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class CompanyRow:
    name: str
    careers_url: str = ""
    ats: str = ""
    slug: str = ""
    status: str = ""  # detector outcome, human-readable (DESIGN §4.2); machine-filled
    handle: Any = None
    dirty: bool = False


class CompaniesSource:
    def load(self) -> list[CompanyRow]:
        raise NotImplementedError

    def save(self, rows: list[CompanyRow]) -> None:
        raise NotImplementedError
