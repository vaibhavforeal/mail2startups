import typer
from datetime import datetime, timezone
from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_engine, init_db as _init_db, make_session
from app.draft.claude_draft import draft_plan
from app.draft.resume_schema import load_resume
from app.draft.service import draft_all, draft_one, select_primary_contact
from app.enrich.hunter import HunterClient
from app.models import Contact, Draft, Startup, StartupStatus
from app.scraper.hunt import hunt_all, hunt_startup
from app.scraper.ingest import ingest_records
from app.scraper.sources import get_source
from app.send.preflight import check_dns
from app.send.service import (
    approve_drafts, reject_drafts, send_batch, send_test_emails,
)
from app.send.smtp_client import SmtpTransport
from app.send import state as send_state

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
        by_found_via = session.execute(
            select(Contact.found_via, func.count()).group_by(Contact.found_via)).all()
    typer.echo("By status:")
    for status, count in by_status:
        typer.echo(f"  {status.value}: {count}")
    typer.echo("By source:")
    for source, count in by_source:
        typer.echo(f"  {source}: {count}")
    typer.echo("Contacts by source:")
    for found_via, count in by_found_via:
        typer.echo(f"  {found_via}: {count}")


def _build_enricher(settings):
    if settings.hunter_api_key:
        return HunterClient(api_key=settings.hunter_api_key)
    return None


@app.command()
def hunt(
    limit: int = typer.Option(50, help="Max discovered startups to process"),
    domain: str = typer.Option(None, help="Only hunt this single domain"),
    no_enrich: bool = typer.Option(False, help="Skip paid free-tier API enrichment"),
):
    """Find contact emails for discovered startups (crawl → guess → verify → enrich)."""
    settings = get_settings()
    enricher = None if no_enrich else _build_enricher(settings)
    with _session() as session:
        if domain:
            startup = session.scalars(
                select(Startup).where(Startup.domain == domain)
            ).first()
            if startup is None:
                typer.echo(f"No startup with domain {domain}", err=True)
                raise typer.Exit(code=1)
            results = [hunt_startup(session, startup, enricher=enricher,
                                    monthly_limit=settings.hunter_monthly_limit)]
        else:
            results = hunt_all(session, limit=limit, enricher=enricher,
                               monthly_limit=settings.hunter_monthly_limit)
    enriched = sum(1 for r in results if r.enriched)
    contacts = sum(r.contacts_added for r in results)
    for r in results:
        typer.echo(f"  startup {r.startup_id}: +{r.contacts_added} contacts "
                   f"({'enriched' if r.enriched else 'no contacts'})")
    typer.echo(f"hunt: processed={len(results)} enriched={enriched} contacts={contacts}")


@app.command()
def draft(
    limit: int = typer.Option(50, help="Max enriched startups to draft"),
    startup: str = typer.Option(None, "--startup", help="Only draft this single domain"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview plans; write nothing"),
):
    """Draft tailored emails (+ resume PDFs) for enriched startups."""
    settings = get_settings()
    try:
        resume = load_resume(settings.resume_path)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    with _session() as session:
        if dry_run:
            query = select(Startup).where(Startup.status == StartupStatus.ENRICHED)
            if startup:
                query = query.where(Startup.domain == startup)
            for s in session.scalars(query.limit(limit)).all():
                contacts = session.scalars(
                    select(Contact).where(Contact.startup_id == s.id)).all()
                contact = select_primary_contact(list(contacts))
                if contact is None:
                    typer.echo(f"  {s.name}: no usable contact")
                    continue
                plan = draft_plan(s, contact, resume)
                typer.echo(f"  {s.name} [{plan.mode}/{plan.angle}] {plan.subject}")
            return
        if startup:
            s = session.scalars(select(Startup).where(Startup.domain == startup)).first()
            if s is None:
                typer.echo(f"No startup with domain {startup}", err=True)
                raise typer.Exit(code=1)
            results = [draft_one(session, s, resume=resume)]
        else:
            results = draft_all(session, limit=limit, resume=resume)

    drafted = sum(1 for r in results if r.drafted)
    typer.echo(f"draft: processed={len(results)} drafted={drafted}")


drafts_app = typer.Typer(help="Inspect generated drafts")
app.add_typer(drafts_app, name="drafts")


@drafts_app.command("list")
def drafts_list():
    """List generated drafts."""
    with _session() as session:
        rows = session.execute(
            select(Draft, Startup.name)
            .join(Startup, Draft.startup_id == Startup.id)
        ).all()
    if not rows:
        typer.echo("No drafts.")
        return
    for draft_row, startup_name in rows:
        has_pdf = "y" if draft_row.resume_pdf_path else "n"
        typer.echo(f"  #{draft_row.id} {startup_name} [{draft_row.mode.value}] "
                   f"pdf={has_pdf} — {draft_row.subject}")


@drafts_app.command("show")
def drafts_show(draft_id: int = typer.Argument(..., help="Draft id")):
    """Print a draft's subject, body, and resume PDF path."""
    with _session() as session:
        draft_row = session.get(Draft, draft_id)
        if draft_row is None:
            typer.echo(f"No draft #{draft_id}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"Draft #{draft_row.id}  mode={draft_row.mode.value}  "
                   f"status={draft_row.status.value}")
        typer.echo(f"Subject: {draft_row.subject}")
        typer.echo(f"PDF: {draft_row.resume_pdf_path or '(none)'}")
        typer.echo("")
        typer.echo(draft_row.body)


def _build_transport(settings):
    return SmtpTransport(settings.smtp_host, settings.smtp_port,
                         settings.smtp_user, settings.smtp_password)


def _dns_resolve(name: str, rtype: str) -> list[str]:
    import dns.exception
    import dns.resolver
    try:
        answers = dns.resolver.resolve(name, rtype)
    except dns.exception.DNSException:
        return []
    return [b"".join(r.strings).decode("utf-8", "ignore") for r in answers]


@app.command()
def approve(
    ids: list[int] = typer.Argument(None, help="Draft ids to approve"),
    all_pending: bool = typer.Option(False, "--all", help="Approve all pending_review drafts"),
):
    """Approve drafts for sending (moves them to the send queue)."""
    if not ids and not all_pending:
        typer.echo("Error: give draft ids or --all", err=True)
        raise typer.Exit(code=1)
    with _session() as session:
        n = approve_drafts(session, ids, all_pending=all_pending)
    typer.echo(f"approved {n} draft(s)")


@app.command()
def reject(ids: list[int] = typer.Argument(..., help="Draft ids to reject")):
    """Reject drafts (marks their startups dead)."""
    with _session() as session:
        n = reject_drafts(session, ids)
    typer.echo(f"rejected {n} draft(s)")


@app.command()
def send(
    limit: int = typer.Option(1, help="Max emails to send this run"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Send to your own address; change nothing"),
    force: bool = typer.Option(False, "--force", help="Bypass the send window and daily cap"),
):
    """Send approved drafts (one-shot; respects pause, window, cap, ramp)."""
    settings = get_settings()
    if not settings.smtp_user or not settings.smtp_password:
        typer.echo("Error: set M2S_SMTP_USER and M2S_SMTP_PASSWORD in .env", err=True)
        raise typer.Exit(code=1)
    transport = _build_transport(settings)
    now = datetime.now(timezone.utc)
    with _session() as session:
        results = send_batch(session, now=now, transport=transport, settings=settings,
                             limit=limit, dry_run=dry_run, force=force)
    sent = sum(1 for r in results if r.sent)
    for r in results:
        typer.echo(f"  draft {r.draft_id}: {'sent' if r.sent else 'skip'} ({r.reason or 'ok'})")
    typer.echo(f"send: attempted={len(results)} sent={sent}")


@app.command()
def pause():
    """Pause sending."""
    with _session() as session:
        send_state.pause(session, "manual")
    typer.echo("sending paused")


@app.command()
def resume():
    """Resume sending and clear the failure counter."""
    with _session() as session:
        send_state.resume(session)
    typer.echo("sending resumed")


@app.command("test-send")
def test_send_cmd(count: int = typer.Option(5, help="How many test emails to send")):
    """Send test emails to M2S_TEST_RECIPIENT to check deliverability."""
    settings = get_settings()
    if not settings.test_recipient:
        typer.echo("Error: set M2S_TEST_RECIPIENT in .env", err=True)
        raise typer.Exit(code=1)
    transport = _build_transport(settings)
    with _session() as session:
        n = send_test_emails(session, transport=transport, settings=settings, count=count)
    typer.echo(f"test-send: sent {n}/{count} to {settings.test_recipient}")


@app.command()
def preflight(domain: str = typer.Option(None, help="Domain to check (default: from M2S_FROM_EMAIL)")):
    """Check SPF / DKIM / DMARC DNS records before the first send."""
    settings = get_settings()
    dom = domain or (settings.from_email or settings.smtp_user).split("@")[-1]
    if not dom:
        typer.echo("Error: no domain (set M2S_FROM_EMAIL or pass --domain)", err=True)
        raise typer.Exit(code=1)
    report = check_dns(dom, selector=settings.dkim_selector, resolve=_dns_resolve)
    for label, (ok, detail) in [("SPF", report.spf), ("DKIM", report.dkim),
                                ("DMARC", report.dmarc)]:
        typer.echo(f"  {label}: {'OK' if ok else 'FAIL'} — {detail}")
    typer.echo(f"preflight: {'all checks passed' if report.ok else 'issues found'}")


if __name__ == "__main__":
    app()
