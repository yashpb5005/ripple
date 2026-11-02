from ripplecheck.check import run_check
from ripplecheck.collect import AppQuery


def test_blocked_drop_shows_postgres_detail(pagila_dsn):
    result = run_check(pagila_dsn, "ALTER TABLE customer DROP COLUMN email;", [AppQuery("a", "SELECT 1")])
    assert result.migration_error
    assert "depend" in result.migration_error.lower()


def test_blocked_drop_names_the_view(pagila_dsn):
    result = run_check(pagila_dsn, "ALTER TABLE customer DROP COLUMN email;", [AppQuery("a", "SELECT 1")])
    assert any("customer_list" in note for note in result.notes)


def test_blocked_table_drop_names_the_foreign_key(pagila_dsn):
    result = run_check(pagila_dsn, "DROP TABLE customer;", [AppQuery("a", "SELECT 1")])
    assert result.migration_error
    assert any("foreign key" in note.lower() and "payment" in note for note in result.notes)


def test_function_body_scan_flags_possible_reference(pagila_dsn):
    sql = "ALTER TABLE customer RENAME COLUMN last_update TO updated_at;"
    result = run_check(pagila_dsn, sql, [AppQuery("a", "SELECT customer_id FROM customer")])
    assert any("last_updated" in note and "verify manually" in note for note in result.notes)
    assert not result.dangers or all("last_update" not in (c.detail or "") for c in result.dangers)


def test_function_body_scan_note_is_never_a_danger(pagila_dsn):
    sql = "ALTER TABLE customer RENAME COLUMN last_update TO updated_at;"
    result = run_check(pagila_dsn, sql, [])
    kinds = {c.kind for c in result.dangers}
    assert "function_may_reference" not in kinds
