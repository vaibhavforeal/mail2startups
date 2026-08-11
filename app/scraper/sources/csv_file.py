import csv
from pathlib import Path

from app.scraper.sources.base import StartupRecord


def _split(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(";") if part.strip()]


class CsvSource:
    name = "csv"

    def fetch(self, limit: int = 100_000, path: str | None = None, **filters) -> list[StartupRecord]:
        if not path:
            raise ValueError("csv source requires path=<file.csv>")
        records: list[StartupRecord] = []
        with Path(path).open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                records.append(StartupRecord(
                    name=name,
                    website=(row.get("website") or "").strip() or None,
                    description=(row.get("description") or "").strip(),
                    location=(row.get("location") or "").strip(),
                    industry=(row.get("industry") or "").strip(),
                    founder_names=_split(row.get("founder_names")),
                    contact_emails=_split(row.get("emails")),
                    source=self.name,
                ))
                if len(records) >= limit:
                    break
        return records
