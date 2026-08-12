from sqlalchemy.orm import Session

from app.models import CampaignState, utcnow


def ensure_state(session: Session) -> CampaignState:
    """Return the singleton campaign_state row (id=1), creating it if absent."""
    state = session.get(CampaignState, 1)
    if state is None:
        state = CampaignState(id=1)
        session.add(state)
        session.commit()
    return state


def pause(session: Session, reason: str) -> CampaignState:
    state = ensure_state(session)
    state.paused = True
    state.paused_reason = reason
    session.commit()
    return state


def resume(session: Session) -> CampaignState:
    state = ensure_state(session)
    state.paused = False
    state.paused_reason = ""
    state.consecutive_failures = 0
    session.commit()
    return state


def record_success(session: Session) -> CampaignState:
    """Reset the consecutive-failure counter; stamp first_send_at once."""
    state = ensure_state(session)
    state.consecutive_failures = 0
    if state.first_send_at is None:
        state.first_send_at = utcnow()
    session.commit()
    return state


def record_failure(session: Session) -> int:
    """Increment and return the consecutive-failure count."""
    state = ensure_state(session)
    state.consecutive_failures += 1
    session.commit()
    return state.consecutive_failures
