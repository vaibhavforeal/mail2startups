import time

import httpx

from app.scraper.sources.base import StartupRecord

SEARCH_URL = "https://api.startupindia.gov.in/sih/api/noauth/search/profiles"
PROFILE_URL = "https://api.startupindia.gov.in/sih/api/common/replica/user/profile/{profile_id}"
PROFILE_DELAY_SECONDS = 0.4

# The API only responds to browser-like requests.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Origin": "https://www.startupindia.gov.in",
    "Referer": "https://www.startupindia.gov.in/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
}


def _find_first(data, keys: tuple[str, ...]):
    """Depth-first search for the first non-empty value under any of `keys`."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and value:
                # If value is a dict, try to extract a string representation
                # (e.g., industryName, name, or text field)
                if isinstance(value, dict):
                    return (value.get("industryName") or value.get("name")
                            or value.get("text") or str(value))
                return value
            found = _find_first(value, keys)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_first(item, keys)
            if found:
                return found
    return None


class StartupIndiaSource:
    name = "startup_india"

    def fetch(self, limit: int = 50, query: str = "",
              fetch_profiles: bool = True, **filters) -> list[StartupRecord]:
        records: list[StartupRecord] = []
        page = 0
        with httpx.Client(headers=BROWSER_HEADERS, timeout=30) as client:
            while len(records) < limit:
                resp = client.post(SEARCH_URL, json={
                    "query": query, "roles": ["Startup"], "page": page,
                })
                resp.raise_for_status()
                data = resp.json()
                for entry in data.get("content", []):
                    website = email = industry = None
                    if fetch_profiles and entry.get("id"):
                        try:
                            profile = client.get(
                                PROFILE_URL.format(profile_id=entry["id"])
                            ).json()
                            website = _find_first(profile, ("website", "websiteUrl"))
                            email = _find_first(profile, ("email", "emailId"))
                            industry = _find_first(profile, ("sector", "industry"))
                        except (httpx.HTTPError, ValueError):
                            pass
                        time.sleep(PROFILE_DELAY_SECONDS)
                    location = ", ".join(
                        part for part in [entry.get("city"), entry.get("state")] if part
                    )
                    records.append(StartupRecord(
                        name=entry.get("name") or "",
                        website=website,
                        location=location,
                        industry=industry or "",
                        contact_emails=[email] if email else [],
                        source=self.name,
                    ))
                    if len(records) >= limit:
                        break
                page += 1
                if page >= int(data.get("totalPages") or 1):
                    break
        return records
