from typer.testing import CliRunner

import app.cli as cli_mod
from app.cli import app
from app.scraper.hunt import HuntResult

runner = CliRunner()


def test_hunt_command_reports_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("M2S_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("M2S_HUNTER_API_KEY", "")  # no enrichment
    runner.invoke(app, ["init-db"])

    captured = {}

    def fake_hunt_all(session, **kwargs):
        captured.update(kwargs)
        return [
            HuntResult(startup_id=1, contacts_added=2, enriched=True),
            HuntResult(startup_id=2, contacts_added=0, enriched=False),
        ]

    monkeypatch.setattr(cli_mod, "hunt_all", fake_hunt_all)
    result = runner.invoke(app, ["hunt", "--limit", "10", "--no-enrich"])
    assert result.exit_code == 0
    assert "processed=2" in result.output
    assert "enriched=1" in result.output
    assert "contacts=2" in result.output
    assert captured["enricher"] is None  # --no-enrich honored
