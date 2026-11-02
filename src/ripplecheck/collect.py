"""Collect the queries each app runs: from pg_stat_statements, Postgres logs, or a file you write."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .sqlutil import is_checkable, is_system_query, normalize, scrub_literals

FILE_VERSION = 2

# Postgres log messages that carry a statement, with or without a duration prefix.
_MESSAGE_RE = re.compile(r"^(?:duration: [\d.]+ ms\s+)?(?:statement|execute [^:]*):\s*(.+)$", re.S)

# Column positions in Postgres csvlog (stable since 9.0).
_CSV_USER, _CSV_MESSAGE, _CSV_APP = 1, 13, 22


@dataclass
class AppQuery:
    app: str
    sql: str
    calls: int = 0


def load_queries(path: str | Path) -> list[AppQuery]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["queries"] if isinstance(data, dict) else data
    return [AppQuery(app=str(i["app"]), sql=str(i["sql"]), calls=int(i.get("calls", 0))) for i in items]


def save_queries(queries: list[AppQuery], path: str | Path, source: str, stats_since=None) -> None:
    payload = {
        "version": FILE_VERSION,
        "source": source,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats_since": stats_since.isoformat() if stats_since else None,
        "queries": [{"app": q.app, "sql": q.sql, "calls": q.calls} for q in queries],
    }
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_metadata(path: str | Path) -> dict:
    """Collection metadata (source, when it was collected, how much pg_stat_statements history it covers).

    Older files (version 1) simply lack `stats_since`; `.get` returns None for them rather than failing.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {
        "source": data.get("source"),
        "collected_at": data.get("collected_at"),
        "stats_since": data.get("stats_since"),
    }


def clean(queries: Iterable[AppQuery], scrub: bool) -> list[AppQuery]:
    """Keep app reads and writes, drop internals, merge duplicates."""
    merged: dict[tuple[str, str], AppQuery] = {}
    for q in queries:
        sql = q.sql.strip()
        if not sql or not is_checkable(sql) or is_system_query(sql):
            continue
        if scrub:
            sql = scrub_literals(sql)
        sql = sql.rstrip(";").strip()
        key = (q.app, normalize(sql))
        if key in merged:
            merged[key].calls += q.calls
        else:
            merged[key] = AppQuery(app=q.app, sql=sql, calls=q.calls)
    return sorted(merged.values(), key=lambda q: (q.app, -q.calls, q.sql))


HIDDEN_QUERY = "<insufficient privilege>"


def _stats_reset_time(conn):
    """When pg_stat_statements was last reset, so Ripple can say how much history it covers. Best-effort.

    Each attempt rolls back on failure before the next: a failed query (e.g. pg_stat_statements_info
    doesn't exist before PG14) aborts the transaction, and every later query on this connection would
    otherwise fail too, not because it's actually unavailable.
    """
    try:
        row = conn.execute("SELECT stats_reset FROM pg_stat_statements_info").fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        conn.rollback()
    try:
        row = conn.execute("SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()").fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        conn.rollback()
    return None


def _eviction_warning(conn) -> str | None:
    """pg_stat_statements is a bounded cache; warn when it's near full, since less-used queries get evicted first."""
    try:
        cap = conn.execute("SELECT current_setting('pg_stat_statements.max')::int").fetchone()[0]
        tracked = conn.execute("SELECT count(*) FROM pg_stat_statements").fetchone()[0]
    except Exception:
        conn.rollback()
        return None
    if cap and tracked >= 0.95 * cap:
        return (
            f"You're near the pg_stat_statements.max limit ({tracked}/{cap} tracked) — "
            "less-used queries may already be getting evicted."
        )
    return None


def _shared_role_warnings(conn, excluded: set[str]) -> list[str]:
    """pg_stat_statements groups by role, not app. Flag roles with more than one app connected right now."""
    try:
        rows = conn.execute(
            """
            SELECT usename, array_agg(DISTINCT application_name)
            FROM pg_stat_activity
            WHERE datname = current_database() AND application_name != ''
            GROUP BY usename
            HAVING count(DISTINCT application_name) > 1
            """
        ).fetchall()
    except Exception:
        conn.rollback()
        return []
    return [
        f"Role {usename} currently has multiple app connections ({', '.join(sorted(apps))}) — Ripple will "
        "attribute all its queries to one app name. (Point-in-time snapshot; only connections live right now are visible.)"
        for usename, apps in rows
        if usename not in excluded
    ]


def from_pg_stat_statements(
    dsn: str,
    min_calls: int = 1,
    exclude_roles: Iterable[str] = (),
    warnings: list[str] | None = None,
    stats_since: list | None = None,
) -> list[AppQuery]:
    """One app per database user. pg_stat_statements already hides literal values."""
    import psycopg

    sql = """
        SELECT r.rolname, s.query, s.calls
        FROM pg_stat_statements s
        JOIN pg_roles r ON r.oid = s.userid
        JOIN pg_database d ON d.oid = s.dbid
        WHERE d.datname = current_database() AND s.calls >= %s
    """
    excluded = set(exclude_roles)
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(sql, (min_calls,)).fetchall()
        if stats_since is not None:
            reset = _stats_reset_time(conn)
            if reset is not None:
                stats_since.append(reset)
        if warnings is not None:
            evict = _eviction_warning(conn)
            if evict:
                warnings.append(evict)
            warnings.extend(_shared_role_warnings(conn, excluded))
    hidden = sorted({role for role, query, _ in rows if query == HIDDEN_QUERY and role not in excluded})
    if hidden and warnings is not None:
        warnings.append(
            f"Postgres hid the queries of {', '.join(hidden)} from this user. "
            "Grant it pg_read_all_stats to see every app's queries."
        )
    return clean(
        (AppQuery(app=role, sql=query, calls=int(calls)) for role, query, calls in rows if role not in excluded),
        scrub=False,
    )


def _from_message(app: str, message: str) -> AppQuery | None:
    match = _MESSAGE_RE.match(message or "")
    return AppQuery(app=app, sql=match.group(1), calls=1) if match else None


def from_csvlog(path: str | Path, by: str = "application_name") -> list[AppQuery]:
    """Read a Postgres csvlog. Apps are told apart by application_name (or by user)."""
    found: list[AppQuery] = []
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.reader(fh):
            if len(row) <= _CSV_APP:
                continue
            app = (row[_CSV_APP] if by == "application_name" else row[_CSV_USER]) or row[_CSV_USER] or "unknown"
            query = _from_message(app, row[_CSV_MESSAGE])
            if query:
                found.append(query)
    return clean(found, scrub=True)


def from_jsonlog(path: str | Path, by: str = "application_name") -> list[AppQuery]:
    """Read a Postgres jsonlog (Postgres 15+), one JSON object per line."""
    found: list[AppQuery] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            app = (entry.get("application_name") if by == "application_name" else entry.get("user")) or entry.get(
                "user"
            ) or "unknown"
            query = _from_message(app, entry.get("message", ""))
            if query:
                found.append(query)
    return clean(found, scrub=True)
