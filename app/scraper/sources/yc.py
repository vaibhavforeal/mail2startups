import httpx

from app.scraper.sources.base import StartupRecord

BASE_URL = "https://yc-oss.github.io/api/companies/{list_name}.json"
VALID_LISTS = {"all", "top", "hiring"}


class YcSource:
    name = "yc"

    def fetch(self, limit: int = 100, list_name: str = "hiring",
              region: str | None = None, **filters) -> list[StartupRecord]:
        if list_name not in VALID_LISTS:
            raise ValueError(f"list_name must be one of {sorted(VALID_LISTS)}")
        resp = httpx.get(BASE_URL.format(list_name=list_name), timeout=30)
        resp.raise_for_status()
        records: list[StartupRecord] = []
        for company in resp.json():
            if company.get("status") != "Active":
                continue
            if region:
                haystack = " ".join(
                    [company.get("all_locations") or ""] + (company.get("regions") or [])
                ).lower()
                if region.lower() not in haystack:
                    continue
            description = ". ".join(
                part for part in
                [company.get("one_liner") or "", company.get("long_description") or ""]
                if part
            )
            records.append(StartupRecord(
                name=company.get("name") or "",
                website=company.get("website") or None,
                description=description,
                location=company.get("all_locations") or "",
                industry=", ".join(company.get("industries") or []) or (company.get("industry") or ""),
                team_size=company.get("team_size") or None,
                source=self.name,
            ))
            if len(records) >= limit:
                break
        return records
