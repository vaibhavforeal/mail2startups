import typer
from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_engine, init_db as _init_db, make_session
from app.models import Startup
from app.scraper.ingest import ingest_records
from app.scraper.sources import get_source

app = typer.Typer(help="Mail2Startups — automated internship outreach")


def _session():
    engine = get_engine(get_settings().db_path)
    _init_db(engine)
    return make_session(engine)


@app.command("init-db")
def init_db_cmd():
    """Create the database and all tables."""
    engine = get_engine(get_settings().db_path)
    _init_db(engine)
    typer.echo(f"Database ready at {get_settings().db_path}")


@app.command()
def discover(
    source: str = typer.Argument(..., help="csv | yc | startup_india | product_hunt | listicle"),
    limit: int = typer.Option(100, help="Max startups to fetch"),
    region: str = typer.Option(None, help="yc: region filter, e.g. 'india' or 'remote'"),
    list_name: str = typer.Option("hiring", help="yc: all | top | hiring"),
    topic: str = typer.Option(None, help="product_hunt: topic slug"),
    url: str = typer.Option(None, help="listicle: article URL"),
    path: str = typer.Option(None, help="csv: file path"),
    no_profiles: bool = typer.Option(False, help="startup_india: skip per-profile fetches"),
):
    """Fetch startups from a directory source and ingest them."""
    filters = {}
    if region: filters["region"] = region
    if list_name: filters["list_name"] = list_name
    if topic: filters["topic"] = topic
    if url: filters["url"] = url
    if path: filters["path"] = path
    if no_profiles: filters["fetch_profiles"] = False
    try:
        src = get_source(source)
        records = src.fetch(limit=limit, **filters)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    with _session() as session:
        result = ingest_records(session, records)
    typer.echo(f"{source}: fetched={len(records)} added={result.added} skipped={result.skipped}")


@app.command()
def stats():
    """Show startup counts by status and source."""
    with _session() as session:
        by_status = session.execute(
            select(Startup.status, func.count()).group_by(Startup.status)).all()
        by_source = session.execute(
            select(Startup.source, func.count()).group_by(Startup.source)).all()
    typer.echo("By status:")
    for status, count in by_status:
        typer.echo(f"  {status.value}: {count}")
    typer.echo("By source:")
    for source, count in by_source:
        typer.echo(f"  {source}: {count}")


if __name__ == "__main__":
    app()
