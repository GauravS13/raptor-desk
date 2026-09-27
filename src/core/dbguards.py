"""Database-level guards used from migrations.

Append-only tables (audit trail, score ledger, published snapshots) are
protected by triggers, so even a bug or a hand-written SQL statement cannot
rewrite history. Supports SQLite (default) and PostgreSQL (documented path).
"""

from collections.abc import Callable
from typing import Any

from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def _sqlite(table: str) -> list[str]:
    return [
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;",
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;",
    ]


def _postgres(table: str) -> list[str]:
    return [
        "CREATE OR REPLACE FUNCTION raptor_append_only() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END; $$ LANGUAGE plpgsql;",
        f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};",
        f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION raptor_append_only();",
    ]


def _drop_sqlite(table: str) -> list[str]:
    return [
        f"DROP TRIGGER IF EXISTS {table}_no_update;",
        f"DROP TRIGGER IF EXISTS {table}_no_delete;",
    ]


def _drop_postgres(table: str) -> list[str]:
    return [f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};"]


def append_only(table: str) -> tuple[Callable[..., None], Callable[..., None]]:
    """Forward and reverse functions for ``migrations.RunPython``."""

    def forward(apps: Any, schema_editor: BaseDatabaseSchemaEditor) -> None:
        vendor = schema_editor.connection.vendor
        statements = _sqlite(table) if vendor == "sqlite" else _postgres(table)
        for statement in statements:
            schema_editor.execute(statement)

    def reverse(apps: Any, schema_editor: BaseDatabaseSchemaEditor) -> None:
        vendor = schema_editor.connection.vendor
        statements = _drop_sqlite(table) if vendor == "sqlite" else _drop_postgres(table)
        for statement in statements:
            schema_editor.execute(statement)

    return forward, reverse


def frozen_after(table: str, column: str) -> tuple[Callable[..., None], Callable[..., None]]:
    """Reject UPDATEs to rows whose ``column`` is set (e.g. a submitted version)."""
    name = f"{table}_frozen_after_{column}"

    def forward(apps: Any, schema_editor: BaseDatabaseSchemaEditor) -> None:
        if schema_editor.connection.vendor == "sqlite":
            schema_editor.execute(
                f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE UPDATE ON {table} "
                f"WHEN OLD.{column} IS NOT NULL "
                f"BEGIN SELECT RAISE(ABORT, '{table} rows are immutable once {column} is set'); "
                "END;"
            )
        else:
            schema_editor.execute(
                f"CREATE OR REPLACE FUNCTION {name}_fn() RETURNS trigger AS $$ "
                f"BEGIN IF OLD.{column} IS NOT NULL THEN "
                f"RAISE EXCEPTION '% rows are immutable once {column} is set', TG_TABLE_NAME; "
                "END IF; RETURN NEW; END; $$ LANGUAGE plpgsql;"
            )
            schema_editor.execute(
                f"CREATE TRIGGER {name} BEFORE UPDATE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {name}_fn();"
            )

    def reverse(apps: Any, schema_editor: BaseDatabaseSchemaEditor) -> None:
        if schema_editor.connection.vendor == "sqlite":
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {name};")
        else:
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {name} ON {table};")

    return forward, reverse
