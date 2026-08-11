from app.scraper.sources.base import Source
from app.scraper.sources.csv_file import CsvSource
from app.scraper.sources.listicle import ListicleSource
from app.scraper.sources.product_hunt import ProductHuntSource
from app.scraper.sources.startup_india import StartupIndiaSource
from app.scraper.sources.yc import YcSource

SOURCES: dict[str, type] = {
    CsvSource.name: CsvSource,
    ListicleSource.name: ListicleSource,
    ProductHuntSource.name: ProductHuntSource,
    StartupIndiaSource.name: StartupIndiaSource,
    YcSource.name: YcSource,
}


def get_source(name: str) -> Source:
    try:
        return SOURCES[name]()
    except KeyError:
        valid = ", ".join(sorted(SOURCES))
        raise ValueError(f"Unknown source '{name}'. Valid sources: {valid}") from None
