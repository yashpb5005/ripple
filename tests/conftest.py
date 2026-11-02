"""Shared fixtures. Database tests need RIPPLE_TEST_DATABASE_URL (any Postgres you can create
databases on); without it they are skipped."""

import os
import uuid
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo"
PAGILA = Path(__file__).resolve().parent / "fixtures" / "pagila_subset.sql"


def _admin_url() -> str | None:
    return os.environ.get("RIPPLE_TEST_DATABASE_URL")


@pytest.fixture
def demo_dir() -> Path:
    return DEMO


@pytest.fixture
def scratch_dsn():
    """A brand-new database with the demo schema, dropped after the test."""
    admin = _admin_url()
    if not admin:
        pytest.skip("set RIPPLE_TEST_DATABASE_URL to run database tests")
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    name = f"ripple_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    params = conninfo_to_dict(admin)
    params["dbname"] = name
    dsn = make_conninfo(**params)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute((DEMO / "schema.sql").read_text())
    try:
        yield dsn
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def pagila_dsn():
    """A brand-new database with the trimmed Pagila-subset schema (views, a trigger function,
    and a foreign key), dropped after the test. See tests/fixtures/pagila_subset.sql."""
    admin = _admin_url()
    if not admin:
        pytest.skip("set RIPPLE_TEST_DATABASE_URL to run database tests")
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    name = f"ripple_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    params = conninfo_to_dict(admin)
    params["dbname"] = name
    dsn = make_conninfo(**params)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(PAGILA.read_text())
    try:
        yield dsn
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
