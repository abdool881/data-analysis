"""STEP 1 (part 2): Connecting to the database.

SQLite = a database that lives in a single file. Perfect while building.
Later we only change the URL to a PostgreSQL address; nothing else changes.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from models import Base


def init_db(url: str = "sqlite:///bizengine.db") -> sessionmaker[Session]:
    engine = create_engine(url)
    Base.metadata.create_all(engine)       # creates any missing tables; leaves existing data alone
    return sessionmaker(engine)            # a factory: call it to get a Session (a "conversation" with the DB)