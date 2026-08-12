from app.send.state import (
    ensure_state, pause, record_failure, record_success, resume,
)


def test_ensure_state_creates_singleton(session):
    a = ensure_state(session)
    b = ensure_state(session)
    assert a.id == 1 and b.id == 1
    assert not a.paused and a.consecutive_failures == 0
    assert a.first_send_at is None


def test_pause_and_resume(session):
    pause(session, "manual")
    s = ensure_state(session)
    assert s.paused and s.paused_reason == "manual"
    resume(session)
    s = ensure_state(session)
    assert not s.paused and s.paused_reason == "" and s.consecutive_failures == 0


def test_record_failure_increments_then_success_resets(session):
    assert record_failure(session) == 1
    assert record_failure(session) == 2
    state = record_success(session)
    assert state.consecutive_failures == 0
    assert state.first_send_at is not None


def test_resume_clears_failures(session):
    record_failure(session)
    record_failure(session)
    resume(session)
    assert ensure_state(session).consecutive_failures == 0
