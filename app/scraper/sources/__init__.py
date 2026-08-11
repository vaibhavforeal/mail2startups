from app.scraper.sources.base import Source
from app.scraper.sources.csv_file import CsvSource

SOURCES: dict[str, type] = {
    CsvSource.name: CsvSource,
}


def get_source(name: str) -> Source:
    try:
        return SOURCES[name]()
    except KeyError:
        valid = ", ".join(sorted(SOURCES))
        raise ValueError(f"Unknown source '{name}'. Valid sources: {valid}") from None
