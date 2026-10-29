import csv
import json

from ripplecheck.collect import AppQuery, clean, from_csvlog, from_jsonlog, load_queries, save_queries


def test_clean_merges_duplicates_and_drops_noise():
    rows = [
        AppQuery("a", "SELECT 1 FROM t", 2),
        AppQuery("a", "select 1 from t;", 0),  # different case is a different query text, kept
        AppQuery("a", "SELECT 1 FROM t ;", 3),
        AppQuery("a", "SET search_path TO x", 9),
        AppQuery("a", "SELECT * FROM pg_catalog.pg_class", 9),
    ]
    cleaned = clean(rows, scrub=False)
    assert [(q.sql, q.calls) for q in cleaned] == [("SELECT 1 FROM t", 5), ("select 1 from t", 0)]


def _csv_row(app, user, message):
    row = [""] * 26
    row[1], row[13], row[22] = user, message, app
    return row


def test_csvlog_by_application_name_hides_values(tmp_path):
    path = tmp_path / "pg.csv"
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_csv_row("billing", "app_user", "statement: SELECT * FROM invoices WHERE email = 'x@y.com'"))
        writer.writerow(_csv_row("billing", "app_user", "duration: 0.42 ms  statement: SELECT * FROM invoices WHERE email = 'z@y.com'"))
        writer.writerow(_csv_row("", "reporter", "execute <unnamed>: SELECT id FROM customers WHERE id = $1"))
        writer.writerow(_csv_row("billing", "app_user", "connection authorized: user=app_user"))
    found = from_csvlog(path)
    assert [(q.app, q.sql, q.calls) for q in found] == [
        ("billing", "SELECT * FROM invoices WHERE email = $1", 2),
        ("reporter", "SELECT id FROM customers WHERE id = $1", 1),
    ]


def test_jsonlog(tmp_path):
    path = tmp_path / "pg.json"
    path.write_text(
        json.dumps({"user": "u", "application_name": "crm", "message": "statement: UPDATE t SET a = 5 WHERE id = 7"})
        + "\nnot json\n"
    )
    assert [(q.app, q.sql) for q in from_jsonlog(path)] == [("crm", "UPDATE t SET a = $1 WHERE id = $2")]


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "q.json"
    save_queries([AppQuery("a", "SELECT 1", 4)], path, "test")
    assert load_queries(path) == [AppQuery("a", "SELECT 1", 4)]
