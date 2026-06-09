"""CSV companies source — the simple, offline, no-account default (DESIGN §4.1)."""
import csv
from pathlib import Path

from jobradar.sources.base import CompaniesSource, CompanyRow

_FIELDS = ["name", "careers_url", "ats", "slug", "status"]


class CsvCompaniesSource(CompaniesSource):
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> list[CompanyRow]:
        rows: list[CompanyRow] = []
        with open(self.path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                name = (r.get("name") or "").strip()
                # Skip blank rows and '#' comment lines so configs can carry guidance.
                if not name or name.startswith("#"):
                    continue
                rows.append(CompanyRow(
                    name=name,
                    careers_url=(r.get("careers_url") or "").strip(),
                    ats=(r.get("ats") or "").strip(),
                    slug=(r.get("slug") or "").strip(),
                    status=(r.get("status") or "").strip(),
                    handle=name,
                ))
        return rows

    def save(self, rows: list[CompanyRow]) -> None:
        """Rewrite the whole file (inline '#' comment rows are dropped on write-back)."""
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "name": row.name,
                    "careers_url": row.careers_url,
                    "ats": row.ats,
                    "slug": row.slug,
                    "status": row.status,
                })
