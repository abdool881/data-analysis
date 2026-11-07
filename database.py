"""STEP 1 (part 2): Connecting to the database.

SQLite = a database that lives in a single file. Perfect while building.
Later we only change the URL to a PostgreSQL address; nothing else changes.
"""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from models import Base


def _upgrade_schema(engine) -> None:
    with engine.begin() as connection:
        inspector = inspect(connection)
        product_columns = {column["name"] for column in inspector.get_columns("products")}
        if "unit_cost_as_of" not in product_columns:
            connection.execute(text("ALTER TABLE products ADD COLUMN unit_cost_as_of DATE"))
        connection.execute(text(
            "UPDATE products SET unit_cost_as_of = CURRENT_DATE WHERE unit_cost_as_of IS NULL"
        ))

        sale_columns = {column["name"] for column in inspector.get_columns("sales")}
        if "return_of_sale_id" not in sale_columns:
            connection.execute(text(
                "ALTER TABLE sales ADD COLUMN return_of_sale_id INTEGER REFERENCES sales(id)"
            ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_sales_return_of_sale_id ON sales (return_of_sale_id)"
        ))


def init_db(url: str = "sqlite:///bizengine.db") -> sessionmaker[Session]:
    engine = create_engine(url)
    Base.metadata.create_all(engine)       # creates any missing tables; leaves existing data alone
    _upgrade_schema(engine)
    return sessionmaker(engine)            # a factory: call it to get a Session (a "conversation" with the DB)