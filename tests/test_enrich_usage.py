from datetime import datetime, timezone

from app.enrich.usage import current_period, record_call, remaining, usage_this_month


def test_current_period_format():
    fixed = datetime(2026, 8, 11, tzinfo=timezone.utc)
    assert current_period(now=fixed) == "2026-08"


def test_record_and_read_usage(session):
    period = "2026-08"
    assert usage_this_month(session, "hunter", period=period) == 0
    assert record_call(session, "hunter", period=period) == 1
    assert record_call(session, "hunter", period=period) == 2
    assert usage_this_month(session, "hunter", period=period) == 2
    assert remaining(session, "hunter", limit=25, period=period) == 23
    # other provider is independent
    assert usage_this_month(session, "apollo", period=period) == 0
