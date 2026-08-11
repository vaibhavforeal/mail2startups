from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.models import Base


def get_engine(db_path: Path | str) -> Engine:
    if str(db_path) == ":memory:":
        return create_engine("sqlite+pysqlite:///:memory:")
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite+pysqlite:///{p}")


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session(engine: Engine) -> Session:
    return Session(engine)
