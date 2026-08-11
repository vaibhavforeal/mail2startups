import pytest

from app.db import get_engine, init_db, make_session


@pytest.fixture()
def session():
    engine = get_engine(":memory:")
    init_db(engine)
    s = make_session(engine)
    yield s
    s.close()
