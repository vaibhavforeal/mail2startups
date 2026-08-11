import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Contact, Draft, Event, Message, Startup, StartupStatus


def test_startup_defaults(session):
    s = Startup(name="Acme", domain="acme.com", source="csv")
    session.add(s)
    session.commit()
    assert s.id is not None
    assert s.status == StartupStatus.DISCOVERED
    assert s.created_at is not None


def test_domain_unique(session):
    session.add(Startup(name="A", domain="acme.com", source="csv"))
    session.commit()
    session.add(Startup(name="B", domain="acme.com", source="yc"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_contact_links_startup(session):
    s = Startup(name="Acme", domain="acme.com", source="csv")
    session.add(s)
    session.flush()
    c = Contact(startup_id=s.id, name="Jane Roe", role="founder",
                email="jane@acme.com", found_via="scraped", confidence=0.9)
    session.add(c)
    session.commit()
    assert s.contacts[0].email == "jane@acme.com"


def test_all_tables_exist(session):
    # Draft, Message, Event are created now as the schema contract for later phases
    session.add(Startup(name="X", domain="x.io", source="csv"))
    session.flush()
    for model in (Draft, Message, Event):
        assert model.__tablename__
