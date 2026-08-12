from app.config import Settings
from app.models import Draft, DraftStatus, MessageType


def test_draft_defaults_to_initial_type(session):
    d = Draft(startup_id=1, subject="Hi", body="Hello",
              status=DraftStatus.PENDING_REVIEW)
    session.add(d)
    session.commit()
    assert d.type == MessageType.INITIAL


def test_draft_can_be_followup_type(session):
    d = Draft(startup_id=1, type=MessageType.FOLLOWUP, subject="Re: Hi",
              body="Bump", status=DraftStatus.PENDING_REVIEW)
    session.add(d)
    session.commit()
    assert d.type == MessageType.FOLLOWUP


def test_settings_followup_delay_default(monkeypatch):
    monkeypatch.delenv("M2S_FOLLOWUP_DELAY_DAYS", raising=False)
    assert Settings(_env_file=None).followup_delay_days == 5
