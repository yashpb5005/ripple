from ripplecheck.schema import Column, Snapshot, diff, prepare_migration, scan_statements


def snap(tables, columns):
    s = Snapshot()
    for relid, (schema, table, kind) in tables.items():
        s.tables[relid] = (schema, table, kind)
    for relid, attnum, name, type_, not_null, has_default, dropped in columns:
        schema, table, kind = tables[relid]
        s.columns[(relid, attnum)] = Column(relid, attnum, schema, table, name, type_, not_null, has_default, dropped, kind)
    return s


TABLES = {1: ("public", "customers", "r")}
BASE = [(1, 1, "id", "bigint", True, True, False), (1, 2, "kind", "text", False, False, False)]


def kinds(changes):
    return [(c.kind, c.column, c.danger) for c in changes]


def test_real_rename_is_not_data_loss():
    after = snap(TABLES, [BASE[0], (1, 2, "segment", "text", False, False, False)])
    changes = diff(snap(TABLES, BASE), after)
    assert kinds(changes) == [("column_renamed", "kind", False)]
    assert changes[0].new_name == "segment"


def test_drop_and_add_looks_like_a_rename_and_is_dangerous():
    after = snap(TABLES, [BASE[0], (1, 2, "....dropped....", "-", False, False, True), (1, 3, "segment", "text", False, False, False)])
    changes = diff(snap(TABLES, BASE), after)
    dropped = next(c for c in changes if c.kind == "column_dropped")
    assert dropped.danger and dropped.column == "kind" and dropped.new_name == "segment"


def test_new_required_column_without_default():
    after = snap(TABLES, BASE + [(1, 3, "currency", "text", True, False, False)])
    assert ("required_no_default", "currency", True) in kinds(diff(snap(TABLES, BASE), after))


def test_new_required_column_with_default_is_fine():
    after = snap(TABLES, BASE + [(1, 3, "currency", "text", True, True, False)])
    assert kinds(diff(snap(TABLES, BASE), after)) == [("column_added", "currency", False)]


def test_type_change_and_now_required():
    after = snap(TABLES, [BASE[0], (1, 2, "kind", "integer", True, False, False)])
    found = kinds(diff(snap(TABLES, BASE), after))
    assert ("type_changed", "kind", True) in found
    assert ("now_required", "kind", True) in found


def test_table_dropped_and_recreated():
    assert kinds(diff(snap(TABLES, BASE), snap({}, []))) == [("table_dropped", None, True)]
    recreated = snap({2: ("public", "customers", "r")}, [(2, 1, "id", "bigint", True, True, False)])
    assert kinds(diff(snap(TABLES, BASE), recreated)) == [("table_recreated", None, True)]


def test_views_are_not_data_loss():
    views = {1: ("public", "report", "v")}
    cols = [(1, 1, "id", "bigint", False, False, False)]
    assert kinds(diff(snap(views, cols), snap({}, []))) == [("table_dropped", None, False)]


def test_prepare_migration_strips_transactions_but_not_function_bodies():
    sql = (
        "BEGIN;\nCREATE FUNCTION f() RETURNS int AS $$\nBEGIN\n  RETURN 1;\nEND;\n$$ LANGUAGE plpgsql;\n"
        "CREATE INDEX CONCURRENTLY i ON t (a);\nCOMMIT;\n"
    )
    cleaned, notes = prepare_migration(sql)
    assert "COMMIT;" not in cleaned and not cleaned.lstrip().startswith("BEGIN;")
    assert "BEGIN\n  RETURN 1;\nEND;" in cleaned
    assert "CONCURRENTLY" not in cleaned
    assert len(notes) == 2


def test_scan_statements_flags_mass_deletes_and_updates():
    found = scan_statements("TRUNCATE a, b; DELETE FROM c; DELETE FROM d WHERE id = 1; UPDATE e SET x = 1;")
    assert [(c.kind, c.table) for c in found] == [
        ("rows_deleted", "public.a"),
        ("rows_deleted", "public.b"),
        ("rows_deleted", "public.c"),
        ("rows_updated", "public.e"),
    ]
