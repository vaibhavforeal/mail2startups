from typer.testing import CliRunner

from app.cli import app
from app.scraper.sources.base import StartupRecord

runner = CliRunner()


def _use_tmp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("M2S_DB_PATH", str(tmp_path / "test.db"))


def test_init_db(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    result = runner.invoke(app, ["init-db"])
    assert result.exit_code == 0
    assert (tmp_path / "test.db").exists()


def test_discover_and_stats(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    runner.invoke(app, ["init-db"])

    class FakeSource:
        name = "fake"
        def fetch(self, limit=100, **filters):
            return [StartupRecord(name="Acme", website="https://acme.com", source="fake")]

    import app.cli as cli_mod
    monkeypatch.setattr(cli_mod, "get_source", lambda name: FakeSource())

    result = runner.invoke(app, ["discover", "fake", "--limit", "5"])
    assert result.exit_code == 0
    assert "added=1" in result.output

    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    assert "discovered" in result.output and "fake" in result.output


def test_discover_unknown_source(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    runner.invoke(app, ["init-db"])
    result = runner.invoke(app, ["discover", "nope"])
    assert result.exit_code != 0
