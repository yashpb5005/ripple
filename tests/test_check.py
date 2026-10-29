import psycopg

from ripplecheck.check import run_check
from ripplecheck.collect import AppQuery, load_queries
from ripplecheck.fixer import Fixer
from ripplecheck.report import render_json, render_markdown, render_text, summary


def test_risky_ai_migration(scratch_dsn, demo_dir):
    queries = load_queries(demo_dir / "queries.json")
    result = run_check(scratch_dsn, (demo_dir / "migration.sql").read_text(), queries)

    assert result.broken_apps == ["invoice-bot", "sales-dashboard", "support-bot"]
    assert len(result.breaks) == 4
    assert {q.status for q in result.breaks} == {"broken", "will_fail"}
    danger = {(c.kind, c.column) for c in result.dangers}
    assert ("column_dropped", "customer_type") in danger
    assert ("required_no_default", "currency") in danger
    assert summary(result) == "This change breaks 3 apps (4 queries)."
    assert "RENAME COLUMN customer_type TO segment" in render_text(result)


def test_safe_migration(scratch_dsn, demo_dir):
    queries = load_queries(demo_dir / "queries.json")
    result = run_check(scratch_dsn, (demo_dir / "migration-safe.sql").read_text(), queries)
    assert not result.breaks and not result.dangers
    assert summary(result).startswith("Safe.")


def test_everything_is_rolled_back(scratch_dsn, demo_dir):
    run_check(scratch_dsn, "DROP TABLE invoices; ALTER TABLE customers DROP COLUMN email;", [])
    with psycopg.connect(scratch_dsn) as conn:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'customers'"
        )}
        tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert "email" in cols and "invoices" in tables


def test_migration_that_fails_is_reported(scratch_dsn):
    result = run_check(scratch_dsn, "ALTER TABLE nope ADD COLUMN x int;", [AppQuery("a", "SELECT 1")])
    assert result.migration_error and "nope" in result.migration_error
    assert summary(result).startswith("The change itself fails")


def test_queries_that_already_fail_are_skipped(scratch_dsn):
    queries = [AppQuery("old-app", "SELECT missing_column FROM customers"), AppQuery("app", "SELECT id FROM customers")]
    result = run_check(scratch_dsn, "ALTER TABLE customers ADD COLUMN x int;", queries)
    assert [q.status for q in result.queries] == ["skipped", "ok"]


def test_recreated_table_is_flagged(scratch_dsn):
    sql = "DROP TABLE invoices; CREATE TABLE invoices (id bigserial PRIMARY KEY, customer_id bigint, amount numeric, status text, created_at timestamptz);"
    result = run_check(scratch_dsn, sql, [AppQuery("a", "SELECT id FROM invoices")])
    assert ("table_recreated", None) in {(c.kind, c.column) for c in result.dangers}
    assert not result.breaks


def test_transaction_lines_in_migration_files_are_handled(scratch_dsn):
    result = run_check(scratch_dsn, "BEGIN;\nALTER TABLE customers ADD COLUMN x int;\nCOMMIT;\n", [])
    assert result.migration_error is None
    with psycopg.connect(scratch_dsn) as conn:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'customers'"
        )}
    assert "x" not in cols  # still rolled back, even though the file said COMMIT


class FakeFixer(Fixer):
    def __init__(self, answers):
        super().__init__(lambda system, user: "")
        self.answers = answers

    def suggest(self, sql, error, changes, schema):
        assert "segment" in schema  # the model is shown the new structure
        return self.answers.get(sql)


def test_fixes_are_verified_before_being_called_ready(scratch_dsn, demo_dir):
    queries = load_queries(demo_dir / "queries.json")
    good = "SELECT segment, count(*) FROM customers GROUP BY segment"
    bad = "SELECT nonsense FROM customers"
    fixer = FakeFixer({
        "SELECT customer_type, count(*) FROM customers GROUP BY customer_type": good,
        "SELECT name, email, customer_type FROM customers WHERE email = $1": bad,
    })
    result = run_check(scratch_dsn, (demo_dir / "migration.sql").read_text(), queries, fixer=fixer)
    by_sql = {q.sql: q for q in result.breaks}
    assert by_sql["SELECT customer_type, count(*) FROM customers GROUP BY customer_type"].fix_status == "verified"
    assert by_sql["SELECT name, email, customer_type FROM customers WHERE email = $1"].fix_status == "failed"
    assert result.fixes_ready == 1
    assert summary(result) == "This change breaks 3 apps (4 queries). 1 fix ready. 3 need you."
    assert "checked against the new schema" in render_markdown(result)
    assert '"fixes_ready": 1' in render_json(result)
