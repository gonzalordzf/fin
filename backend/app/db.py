import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.models import Base

load_dotenv()

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "finanzas.db"
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

engine = create_engine(DATABASE_URL)

# No migration framework (Alembic etc.) — this is a single-developer project
# and `create_all()` only creates tables that don't exist yet, it never adds
# a column to a table that's already there. Since the real database can't be
# safely dropped and re-seeded (its source statements aren't all available
# in every environment — some only ever existed transiently during an
# earlier import), new nullable columns get added here instead: idempotent,
# checked against the live schema every startup, safe to run on a database
# that already has the column (a no-op) or one that doesn't (ALTER TABLE).
_COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "accounts": [
        ("is_credit_card", "BOOLEAN DEFAULT 0"),
        ("statement_cutoff_day", "INTEGER"),
        ("payment_due_offset_days", "INTEGER"),
        ("payment_due_offset_type", "VARCHAR"),
    ],
    "transactions": [
        ("spend_frequency", "VARCHAR"),
    ],
}


def _run_column_migrations() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _COLUMN_MIGRATIONS.items():
            if table not in existing_tables:
                continue  # create_all() will create it with the full schema
            existing_columns = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl_type in columns:
                if name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _run_column_migrations()


def get_session() -> Session:
    return Session(engine)
