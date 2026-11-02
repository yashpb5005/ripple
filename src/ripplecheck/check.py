"""The core check.

1. Open one transaction on a scratch database that has your current schema.
2. Ask Postgres to validate every recorded app query (PREPARE plans a query without running it).
3. Apply the change, snapshot the structure again, and re-validate every query.
4. Roll everything back. Nothing is ever saved.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field

import psycopg

from .collect import AppQuery
from .schema import (
    Change,
    Snapshot,
    diff,
    estimate_rows,
    extract_ddl_targets,
    find_blocking_objects,
    prepare_migration,
    scan_function_bodies,
    scan_statements,
    take_snapshot,
)
from .sqlutil import QueryRefs, extract_refs, has_multiple_statements

REAL_DATA_ROWS = 10_000
_names = itertools.count()


@dataclass
class QueryResult:
    app: str
    sql: str
    status: str  # ok | broken | will_fail | skipped
    error: str | None = None
    fix: str | None = None
    fix_status: str | None = None  # verified | failed | none

    @property
    def is_break(self) -> bool:
        return self.status in ("broken", "will_fail")


@dataclass
class CheckResult:
    queries: list[QueryResult] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    users: dict[str, list[str]] = field(default_factory=dict)  # table -> apps that use it
    column_users: dict[str, list[str]] = field(default_factory=dict)  # "table.column" -> apps that use it
    inserters: dict[str, list[str]] = field(default_factory=dict)  # table -> apps that insert into it
    migration_error: str | None = None
    notes: list[str] = field(default_factory=list)
    fixes_requested: bool = False

    @property
    def breaks(self) -> list[QueryResult]:
        return [q for q in self.queries if q.is_break]

    @property
    def broken_apps(self) -> list[str]:
        return sorted({q.app for q in self.breaks})

    @property
    def dangers(self) -> list[Change]:
        return [c for c in self.changes if c.danger]

    @property
    def fixes_ready(self) -> int:
        return sum(1 for q in self.breaks if q.fix_status == "verified")

    @property
    def checked_apps(self) -> list[str]:
        return sorted({q.app for q in self.queries if q.status != "skipped"})

    @property
    def skipped(self) -> list[QueryResult]:
        return [q for q in self.queries if q.status == "skipped"]


def _error_text(error: psycopg.Error) -> str:
    diag = getattr(error, "diag", None)
    message = (diag.message_primary if diag else None) or str(error).strip().splitlines()[0]
    detail = diag.message_detail if diag else None
    hint = diag.message_hint if diag else None
    if detail:
        message = f"{message} ({detail})"
    return f"{message} (hint: {hint})" if hint else message


def try_prepare(cur, sql: str) -> str | None:
    """Return None if Postgres accepts the query, otherwise the error message."""
    if has_multiple_statements(sql):
        return "contains more than one statement"
    name = f"ripple_q{next(_names)}"
    cur.execute("SAVEPOINT ripple_q")
    try:
        cur.execute(f"PREPARE {name} AS {sql.strip().rstrip(';')}")
        cur.execute(f"DEALLOCATE {name}")
        cur.execute("RELEASE SAVEPOINT ripple_q")
        return None
    except psycopg.Error as error:
        cur.execute("ROLLBACK TO SAVEPOINT ripple_q")
        return _error_text(error)


def _missing_required(refs: QueryRefs, changes: list[Change]) -> list[str]:
    """Columns an INSERT doesn't set but now must. Postgres only notices this at run time."""
    if refs.kind != "insert" or not refs.target or not refs.parsed:
        return []
    missing = []
    for change in changes:
        if change.table != refs.target or not change.column:
            continue
        listed = refs.insert_columns
        if change.kind == "required_no_default" and (listed is None or change.column not in listed):
            missing.append(change.column)
        elif change.kind == "now_required" and not change.has_default and listed is not None and change.column not in listed:
            missing.append(change.column)
    return missing


def _usage(queries: list[AppQuery], refs: list[QueryRefs]) -> tuple[dict, dict, dict]:
    users: dict[str, set[str]] = defaultdict(set)
    column_users: dict[str, set[str]] = defaultdict(set)
    inserters: dict[str, set[str]] = defaultdict(set)
    for query, ref in zip(queries, refs):
        for table in ref.tables:
            users[table].add(query.app)
        for table, column in ref.columns:
            column_users[f"{table}.{column}"].add(query.app)
        if ref.kind == "insert" and ref.target:
            inserters[ref.target].add(query.app)

    def as_lists(mapping: dict[str, set[str]]) -> dict[str, list[str]]:
        return {key: sorted(apps) for key, apps in mapping.items()}

    return as_lists(users), as_lists(column_users), as_lists(inserters)


def _context_for(refs: QueryRefs, changes: list[Change], after: Snapshot) -> tuple[str, str]:
    tables = set(refs.tables)
    for change in changes:
        if change.table in tables and change.new_name and change.kind == "table_renamed":
            tables.add(change.new_name)
    relevant = [c for c in changes if c.table in tables] or changes
    change_lines = "\n".join(f"- {describe_change(c)}" for c in relevant) or "- (none detected)"
    live = after.live_columns()
    schema_lines = "\n".join(after.describe(t) for t in sorted(tables) if t in live) or "(unknown)"
    return change_lines, schema_lines


def describe_change(change: Change) -> str:
    """Short plain-English description of one change (used for reports and AI prompts)."""
    col = f"{change.table}.{change.column}" if change.column else change.table
    match change.kind:
        case "table_dropped":
            return f"Table {change.table} is deleted."
        case "table_recreated":
            return f"Table {change.table} is deleted and created again."
        case "table_renamed":
            return f"Table {change.table} is renamed to {change.new_name}."
        case "column_renamed":
            return f"Column {col} is renamed to {change.new_name}."
        case "column_dropped":
            extra = f" (and {change.new_name} is added)" if change.new_name else ""
            return f"Column {col} is deleted{extra}."
        case "column_added":
            return f"Column {col} is added."
        case "required_no_default":
            return f"Required column {col} is added with no default value."
        case "now_required":
            return f"Column {col} becomes required (NOT NULL)."
        case "type_changed":
            return f"Column {col} changes type: {change.detail}."
        case "table_added":
            return f"Table {change.table} is added."
        case "rows_deleted":
            return f"Rows in {change.table} are deleted ({change.detail})."
        case "rows_updated":
            return f"Every row in {change.table} is changed ({change.detail})."
    return f"{change.kind}: {col}"


def run_check(
    dsn: str,
    migration_sql: str,
    queries: list[AppQuery],
    fixer=None,
    lock_timeout: str = "5s",
    statement_timeout: str = "60s",
) -> CheckResult:
    """Check a change against every recorded query. Always rolls back."""
    migration_sql, notes = prepare_migration(migration_sql)
    result = CheckResult(notes=notes, fixes_requested=fixer is not None)

    with psycopg.connect(dsn, autocommit=False) as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
            cur.execute(f"SET LOCAL statement_timeout = '{statement_timeout}'")

            rows = estimate_rows(cur)
            if rows > REAL_DATA_ROWS:
                result.notes.append(
                    f"This database seems to hold real data (about {rows:,} rows). Ripple only needs an "
                    "empty copy of your schema. Point it at a scratch database, not production."
                )

            cur.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
            others = cur.fetchone()[0]
            if others:
                result.notes.append(
                    f"{others} other connection(s) are active on this database right now. Applying a migration "
                    "here takes exclusive locks until the check finishes — point --dsn at a copy nothing else queries."
                )

            before = take_snapshot(cur)
            schema = before.live_columns()
            refs = [extract_refs(q.sql, schema) for q in queries]
            result.users, result.column_users, result.inserters = _usage(queries, refs)
            baseline = [try_prepare(cur, q.sql) for q in queries]

            cur.execute("SAVEPOINT ripple_migration")
            try:
                cur.execute(migration_sql)
            except psycopg.Error as error:
                result.migration_error = _error_text(error)
                try:
                    cur.execute("ROLLBACK TO SAVEPOINT ripple_migration")
                    for table, column in extract_ddl_targets(migration_sql):
                        result.notes.extend(find_blocking_objects(cur, table, column))
                except Exception:
                    pass
                return result

            after = take_snapshot(cur)
            result.changes = diff(before, after) + scan_statements(migration_sql)
            renamed_or_dropped = [
                (c.table, c.column) for c in result.changes if c.kind in ("column_dropped", "column_renamed") and c.column
            ]
            result.notes.extend(scan_function_bodies(cur, renamed_or_dropped))

            for query, ref, before_error in zip(queries, refs, baseline):
                if before_error:
                    result.queries.append(
                        QueryResult(query.app, query.sql, "skipped", error=f"fails even before the change: {before_error}")
                    )
                    continue
                error = try_prepare(cur, query.sql)
                if error:
                    result.queries.append(QueryResult(query.app, query.sql, "broken", error=error))
                    continue
                missing = _missing_required(ref, result.changes)
                if missing:
                    what = f"column {missing[0]}" if len(missing) == 1 else f"columns {', '.join(missing)}"
                    result.queries.append(
                        QueryResult(
                            query.app,
                            query.sql,
                            "will_fail",
                            error=f"doesn't set the new required {what}, so it will fail when it runs",
                        )
                    )
                    continue
                result.queries.append(QueryResult(query.app, query.sql, "ok"))

            if fixer is not None:
                for item, ref in zip(result.queries, refs):
                    if item.status != "broken":
                        continue
                    changes_text, schema_text = _context_for(ref, result.changes, after)
                    try:
                        suggestion = fixer.suggest(item.sql, item.error or "", changes_text, schema_text)
                    except Exception as error:  # network or provider problems shouldn't hide the report
                        result.notes.append(f"Couldn't get an AI fix: {error}")
                        suggestion = None
                    if not suggestion:
                        item.fix_status = "none"
                        continue
                    item.fix = suggestion
                    item.fix_status = "verified" if try_prepare(cur, suggestion) is None else "failed"
        finally:
            conn.rollback()
    return result
