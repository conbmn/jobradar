"""Companies source factory — dispatch on `companies_source` config (DESIGN §4.1)."""
from pathlib import Path

from jobradar.sources.base import CompaniesSource, CompanyRow
from jobradar.sources.csv_source import CsvCompaniesSource


def get_companies_source(config: dict, csv_path: Path) -> CompaniesSource:
    """Build the configured companies source. Defaults to CSV when unset."""
    kind = (config.get("companies_source") or "csv").strip().lower()
    if kind == "csv":
        return CsvCompaniesSource(csv_path)
    if kind == "notion":
        from jobradar.sources.notion_source import NotionCompaniesSource
        notion_cfg = config.get("notion") or {}
        db_id = (notion_cfg.get("companies_database_id") or "").strip()
        if not db_id:
            raise RuntimeError(
                "companies_source is 'notion' but notion.companies_database_id "
                "is not set in matcher.yml."
            )
        return NotionCompaniesSource(database_id=db_id)
    raise RuntimeError(f"Unknown companies_source: {kind!r} (expected 'csv' or 'notion').")


__all__ = ["get_companies_source", "CompaniesSource", "CompanyRow"]
