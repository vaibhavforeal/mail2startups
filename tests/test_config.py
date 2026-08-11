from pathlib import Path

from app.config import get_settings


def test_defaults():
    s = get_settings()
    assert s.db_path == Path("data/m2s.db")
    assert s.anthropic_model == "claude-opus-5"
    assert s.product_hunt_token == ""


def test_env_override(monkeypatch):
    monkeypatch.setenv("M2S_DB_PATH", "elsewhere/test.db")
    monkeypatch.setenv("M2S_ANTHROPIC_MODEL", "claude-sonnet-5")
    s = get_settings()
    assert s.db_path == Path("elsewhere/test.db")
    assert s.anthropic_model == "claude-sonnet-5"
