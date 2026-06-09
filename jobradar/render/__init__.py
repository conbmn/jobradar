import csv
from pathlib import Path


def write_csv(rows, output_path: Path) -> int:
    """Write ranked job rows to a CSV file. Returns the number of rows written."""
    fieldnames = ["company", "title", "location", "score", "reason", "url"]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        count = 0
        for row in rows:
            writer.writerow({
                "company": row["company"],
                "title": row["title"],
                "location": row["location"] or "",
                "score": row["match_score"],
                "reason": row["match_reason"] or "",
                "url": row["url"] or "",
            })
            count += 1
    return count
