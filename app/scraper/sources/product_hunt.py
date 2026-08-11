import httpx

from app.config import get_settings
from app.scraper.sources.base import StartupRecord

GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"

_QUERY = """
query Posts($first: Int!, $topic: String) {
  posts(first: $first, topic: $topic, order: VOTES) {
    edges { node {
      name tagline description website
      topics(first: 3) { edges { node { name } } }
    } }
  }
}
"""


class ProductHuntSource:
    name = "product_hunt"

    def fetch(self, limit: int = 50, topic: str | None = None, **filters) -> list[StartupRecord]:
        token = get_settings().product_hunt_token
        if not token:
            raise ValueError(
                "Product Hunt requires an API token. Create one at "
                "https://www.producthunt.com/v2/oauth/applications and set "
                "M2S_PRODUCT_HUNT_TOKEN in .env"
            )
        resp = httpx.post(
            GRAPHQL_URL,
            json={"query": _QUERY, "variables": {"first": min(limit, 50), "topic": topic}},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        edges = resp.json().get("data", {}).get("posts", {}).get("edges", [])
        records: list[StartupRecord] = []
        for edge in edges[:limit]:
            node = edge.get("node", {})
            topics = [t["node"]["name"] for t in node.get("topics", {}).get("edges", [])]
            records.append(StartupRecord(
                name=node.get("name") or "",
                website=node.get("website") or None,
                description=". ".join(p for p in [node.get("tagline"), node.get("description")] if p),
                industry=", ".join(topics),
                source=self.name,
            ))
        return records
