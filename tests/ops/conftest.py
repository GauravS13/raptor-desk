"""Backups use SQLite's backup API, which needs a connection outside any
transaction, so these tests run with ``transaction=True``. Django then empties
the tables after each test, and the append-only triggers rightly refuse to
delete audit rows. These fixtures lift those triggers for the flush only and
put them back afterwards.
"""

import pytest
from django.db import connection

_lifted: list[str] = []


@pytest.fixture
def _put_guards_back(django_db_blocker):
    yield
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        for sql in _lifted:
            cursor.execute(sql)
    _lifted.clear()


@pytest.fixture
def ops_db(_put_guards_back, transactional_db):
    yield
    with connection.cursor() as cursor:
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
        for name, sql in cursor.fetchall():
            if name.endswith("_no_delete"):
                _lifted.append(sql)
                cursor.execute(f'DROP TRIGGER "{name}"')
