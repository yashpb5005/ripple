import json
from datetime import datetime, timezone

import psycopg
import pytest

from ripplecheck.collect import AppQuery, from_pg_stat_statements, load_metadata, load_queries, save_queries


def test_stats_since_and_warnings_are_best_effort(scratch_dsn):
    with psycopg.connect(scratch_dsn, autocommit=True) as conn:
        try:
            conn.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
        except psycopg.Error:
            pytest.skip("pg_stat_statements isn't preloaded on this server")
        conn.execute("SELECT * FROM customers")

    warnings: list[str] = []
    stats_since: list = []
    queries = from_pg_stat_statements(scratch_dsn, warnings=warnings, stats_since=stats_since)
    assert isinstance(queries, list)
    assert not stats_since or stats_since[0] is not None
    assert all(isinstance(w, str) for w in warnings)


def test_stats_since_round_trips_through_saved_file(tmp_path):
    path = tmp_path / "queries.json"
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    save_queries([AppQuery("a", "SELECT 1")], path, "stats", stats_since=when)
    assert load_metadata(path)["stats_since"] == when.isoformat()
    assert load_queries(path) == [AppQuery("a", "SELECT 1", 0)]


def test_old_queries_file_without_stats_since_still_loads(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "source": "stats",
                "collected_at": "2026-01-01T00:00:00+00:00",
                "queries": [{"app": "a", "sql": "SELECT 1", "calls": 1}],
            }
        )
    )
    assert load_metadata(path)["stats_since"] is None
    assert load_queries(path) == [AppQuery("a", "SELECT 1", 1)]
