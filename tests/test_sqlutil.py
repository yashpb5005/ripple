from ripplecheck.sqlutil import (
    extract_refs,
    has_multiple_statements,
    is_checkable,
    is_system_query,
    normalize,
    scrub_literals,
)


def test_checkable_statements():
    assert is_checkable("  -- hi\n select 1")
    assert is_checkable("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x")
    assert not is_checkable("SET search_path TO public")
    assert not is_checkable("BEGIN")


def test_system_queries_are_ignored():
    assert is_system_query("SELECT * FROM pg_catalog.pg_class")
    assert not is_system_query("SELECT * FROM customers")


def test_normalize_matches_formatting_differences():
    assert normalize("SELECT  1 ;\n") == "SELECT 1"
    assert normalize("SELECT 1 -- note") == "SELECT 1"


def test_scrub_literals_hides_values_but_keeps_shape():
    sql = "SELECT * FROM users WHERE email = 'a@b.com' AND age > 30 ORDER BY 1 LIMIT 5"
    assert scrub_literals(sql) == "SELECT * FROM users WHERE email = $1 AND age > $2 ORDER BY 1 LIMIT $3"


def test_scrub_literals_keeps_typed_literals_and_existing_params():
    sql = "SELECT * FROM t WHERE created > now() - interval '7 days' AND id = $1 AND x = 'y'"
    assert scrub_literals(sql) == "SELECT * FROM t WHERE created > now() - interval '7 days' AND id = $1 AND x = $2"


def test_scrub_literals_ignores_numbers_inside_names():
    assert scrub_literals("SELECT col1 FROM t2 WHERE a = 7") == "SELECT col1 FROM t2 WHERE a = $1"


def test_multiple_statements_detected_outside_strings():
    assert has_multiple_statements("SELECT 1; DROP TABLE x")
    assert not has_multiple_statements("SELECT ';' AS semi")
    assert not has_multiple_statements("SELECT 1;")


def test_extract_refs_resolves_aliases_in_joins():
    refs = extract_refs("SELECT c.email, i.total FROM customers c JOIN invoices i ON i.customer_id = c.id")
    assert refs.kind == "select"
    assert refs.tables == {"public.customers", "public.invoices"}
    assert ("public.customers", "email") in refs.columns
    assert ("public.invoices", "customer_id") in refs.columns


def test_extract_refs_uses_schema_for_unqualified_columns():
    schema = {"public.customers": {"id", "email"}, "public.invoices": {"customer_id", "total"}}
    refs = extract_refs(
        "SELECT email FROM customers WHERE id IN (SELECT customer_id FROM invoices WHERE total > $1)", schema
    )
    assert ("public.customers", "email") in refs.columns
    assert ("public.invoices", "total") in refs.columns


def test_extract_refs_for_inserts():
    refs = extract_refs("INSERT INTO invoices (customer_id, amount) VALUES ($1, $2) RETURNING id")
    assert refs.kind == "insert"
    assert refs.target == "public.invoices"
    assert refs.insert_columns == ["customer_id", "amount"]
    positional = extract_refs("INSERT INTO billing.invoices VALUES ($1, $2)")
    assert positional.target == "billing.invoices"
    assert positional.insert_columns is None


def test_extract_refs_skips_ctes():
    refs = extract_refs("WITH recent AS (SELECT * FROM invoices) SELECT count(*) FROM recent")
    assert refs.tables == {"public.invoices"}


def test_unparseable_sql_is_reported_not_raised():
    refs = extract_refs("SELECT FROM WHERE (((")
    assert refs.parsed is False or refs.tables == set()
