"""Layer 1a — SQLAlchemy Core schema, engine factory, and init_db."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    event,
    insert,
    select,
)
from sqlalchemy.engine import Engine

from finance_copilot.truelayer.errors import SchemaVersionError

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "SchemaVersionError",
    "accounts_table",
    "check_schema_version",
    "init_db",
    "make_engine",
    "metadata",
    "oauth_tokens_table",
    "schema_version_table",
    "sync_runs_table",
    "transactions_table",
]

CURRENT_SCHEMA_VERSION = 1

metadata = MetaData()

accounts_table = Table(
    "accounts",
    metadata,
    Column("account_id", Text, primary_key=True),
    Column("provider_id", Text, nullable=False),
    Column("account_type", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("currency", Text, nullable=False),
    Column("first_seen_at", Text, nullable=False),
    Column("last_seen_at", Text, nullable=False),
    Column("raw_payload", Text, nullable=False),
)

transactions_table = Table(
    "transactions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", Text, ForeignKey("accounts.account_id"), nullable=False),
    Column("dedup_key", Text, nullable=False, unique=True),
    Column("source_transaction_id", Text, nullable=False),
    Column("booking_date", Text, nullable=False),
    Column("value_date", Text, nullable=True),
    Column("amount", Text, nullable=False),
    Column("currency", Text, nullable=False),
    Column("transaction_type", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("provider_category", Text, nullable=True),
    Column("raw_payload", Text, nullable=False),
    Column("ingested_at", Text, nullable=False),
)

Index(
    "ix_transactions_account_booking",
    transactions_table.c.account_id,
    transactions_table.c.booking_date,
)

sync_runs_table = Table(
    "sync_runs",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text, nullable=True),
    Column("status", Text, nullable=False),
    Column("accounts_attempted", Integer, nullable=False, default=0),
    Column("accounts_succeeded", Integer, nullable=False, default=0),
    Column("transactions_inserted", Integer, nullable=False, default=0),
    Column("transactions_skipped_duplicate", Integer, nullable=False, default=0),
    Column("error_summary", Text, nullable=True),
)

oauth_tokens_table = Table(
    "oauth_tokens",
    metadata,
    Column("provider", Text, primary_key=True),
    Column("access_token", Text, nullable=False),
    Column("refresh_token", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("obtained_at", Text, nullable=False),
)

schema_version_table = Table(
    "schema_version",
    metadata,
    Column("version", Integer, primary_key=True),
    Column("applied_at", Text, nullable=False),
)


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
    """Enable SQLite foreign-key enforcement for every new connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(db_path: str = "finance.db") -> Engine:
    """Create and return a SQLAlchemy engine for the given SQLite DB path.

    Pass ``":memory:"`` for an in-memory database (test use).
    """
    return create_engine(f"sqlite:///{db_path}")


def init_db(engine: Engine) -> None:
    """Create all tables and seed the ``schema_version`` row if not already present."""
    metadata.create_all(engine)
    with engine.begin() as conn:
        existing = conn.execute(select(schema_version_table)).first()
        if existing is None:
            conn.execute(
                insert(schema_version_table).values(
                    version=CURRENT_SCHEMA_VERSION,
                    applied_at=datetime.now(UTC).isoformat(),
                )
            )


def check_schema_version(engine: Engine) -> None:
    """Raise ``SchemaVersionError`` if the DB schema version exceeds the code version.

    This protects against running older code against a database migrated by a newer
    version of the application.
    """
    with engine.connect() as conn:
        row = conn.execute(
            select(schema_version_table.c.version).order_by(
                schema_version_table.c.version.desc()
            )
        ).first()
    if row is None:
        return
    db_version: int = row[0]
    if db_version > CURRENT_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"DB schema version {db_version} is newer than code version "
            f"{CURRENT_SCHEMA_VERSION}. Please upgrade finance-copilot."
        )
