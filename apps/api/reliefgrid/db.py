from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from reliefgrid.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)


def transaction() -> Generator[Connection, None, None]:
    with engine.begin() as connection:
        yield connection
