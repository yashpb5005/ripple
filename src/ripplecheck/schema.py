"""Snapshot the database structure and describe what a change did to it.

Columns are tracked by their internal id (table oid + attnum), so Ripple can tell a real
RENAME apart from a delete-and-add, which silently throws the column's data away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

DATA_KINDS = {"r", "p"}  # ordinary and partitioned tables hold data; views do not

SNAPSHOT_SQL = """
SELECT c.oid::bigint, a.attnum, n.nspname, c.relname, a.attname,
       format_type(a.atttypid, a.atttypmod),
       a.attnotnull,
       (a.atthasdef OR a.attidentity <> '' OR a.attgenerated <> ''),
       a.attisdropped,
       c.relkind::text
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\\_%'
  AND a.attnum > 0
"""

ROW_ESTIMATE_SQL = """
SELECT coalesce(sum(greatest(c.reltuples, 0)), 0)::bigint
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\\_%'
"""


@dataclass(frozen=True)
class Column:
    relid: int
    attnum: int
    schema: str
    table: str
    name: str
    type: str
    not_null: bool
    has_default: bool
    dropped: bool
    relkind: str

    @property
    def table_name(self) -> str:
        return f"{self.schema}.{self.table}"


@dataclass
class Snapshot:
    columns: dict[tuple[int, int], Column] = field(default_factory=dict)
    tables: dict[int, tuple[str, str, str]] = field(default_factory=dict)  # relid -> (schema, table, relkind)

    def live_columns(self) -> dict[str, set[str]]:
        """'schema.table' -> names of columns that still exist."""
        result: dict[str, set[str]] = {f"{s}.{t}": set() for s, t, _ in self.tables.values()}
        for col in self.columns.values():
            if not col.dropped:
                result.setdefault(col.table_name, set()).add(col.name)
        return result

    def describe(self, table: str) -> str:
        """One line per table, used to give an AI the new structure."""
        cols = sorted(
            (c for c in self.columns.values() if c.table_name == table and not c.dropped),
            key=lambda c: c.attnum,
        )
        parts = [f"{c.name} {c.type}{' NOT NULL' if c.not_null else ''}" for c in cols]
        return f"{table}({', '.join(parts)})"


def take_snapshot(cur) -> Snapshot:
    cur.execute(SNAPSHOT_SQL)
    snap = Snapshot()
    for relid, attnum, schema, table, name, type_, not_null, has_default, dropped, relkind in cur.fetchall():
        snap.tables[relid] = (schema, table, relkind)
        snap.columns[(relid, attnum)] = Column(
            relid, attnum, schema, table, name, type_, not_null, has_default, dropped, relkind
        )
    return snap


def estimate_rows(cur) -> int:
    cur.execute(ROW_ESTIMATE_SQL)
    return int(cur.fetchone()[0])


@dataclass
class Change:
    """One thing the change did. `danger` means data can be lost or the change can fail in production."""

    kind: str
    table: str
    column: str | None = None
    new_name: str | None = None
    detail: str = ""
    danger: bool = False
    has_default: bool = False


def diff(before: Snapshot, after: Snapshot) -> list[Change]:
    changes: list[Change] = []
    before_by_name = {(s, t) for s, t, _ in before.tables.values()}
    after_by_name = {(s, t) for s, t, _ in after.tables.values()}

    for relid, (schema, table, relkind) in before.tables.items():
        name = f"{schema}.{table}"
        holds_data = relkind in DATA_KINDS
        if relid not in after.tables:
            kind = "table_recreated" if (schema, table) in after_by_name else "table_dropped"
            changes.append(Change(kind, name, danger=holds_data))
            continue

        new_schema, new_table, _ = after.tables[relid]
        if (new_schema, new_table) != (schema, table):
            changes.append(Change("table_renamed", name, new_name=f"{new_schema}.{new_table}"))

        dropped: list[Column] = []
        for key, col in before.columns.items():
            if key[0] != relid or col.dropped:
                continue
            now = after.columns.get(key)
            if now is None or now.dropped:
                dropped.append(col)
                continue
            if now.name != col.name:
                changes.append(Change("column_renamed", name, col.name, new_name=now.name))
            if now.type != col.type:
                changes.append(
                    Change("type_changed", name, now.name, detail=f"{col.type} → {now.type}", danger=holds_data)
                )
            if now.not_null and not col.not_null:
                changes.append(Change("now_required", name, now.name, danger=holds_data, has_default=now.has_default))

        added = [
            col
            for key, col in after.columns.items()
            if key[0] == relid and not col.dropped and key not in before.columns
        ]
        for col in sorted(dropped, key=lambda c: c.attnum):
            twin = next((a for a in added if a.type == col.type), None)
            changes.append(
                Change("column_dropped", name, col.name, new_name=twin.name if twin else None, danger=holds_data)
            )
        for col in sorted(added, key=lambda c: c.attnum):
            if col.not_null and not col.has_default:
                changes.append(Change("required_no_default", name, col.name, danger=holds_data))
            else:
                changes.append(Change("column_added", name, col.name, has_default=col.has_default))

    for relid, (schema, table, _) in after.tables.items():
        if relid not in before.tables and (schema, table) not in before_by_name:
            changes.append(Change("table_added", f"{schema}.{table}"))
    return changes


# --- Reading the migration text ------------------------------------------------------

_TX_RE = re.compile(r"^\s*(?:BEGIN|COMMIT|END|START\s+TRANSACTION|ROLLBACK)\b[^;]*;", re.I | re.M)
_CONCURRENTLY_RE = re.compile(r"\bCONCURRENTLY\b", re.I)
_DOLLAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*|)\$.*?\$\1\$", re.S)
_TRUNCATE_RE = re.compile(r"^\s*TRUNCATE\s+(?:TABLE\s+)?(?:ONLY\s+)?(.+)$", re.I | re.S)
_DELETE_RE = re.compile(r"^\s*DELETE\s+FROM\s+(?:ONLY\s+)?([\w.\"]+)", re.I)
_UPDATE_RE = re.compile(r"^\s*UPDATE\s+(?:ONLY\s+)?([\w.\"]+)", re.I)
_WHERE_RE = re.compile(r"\bWHERE\b", re.I)


def _mask_dollar_quotes(sql: str) -> tuple[str, list[str]]:
    blocks: list[str] = []

    def keep(match: re.Match) -> str:
        blocks.append(match.group(0))
        return f"\x00{len(blocks) - 1}\x00"

    return _DOLLAR_RE.sub(keep, sql), blocks


def _unmask(sql: str, blocks: list[str]) -> str:
    return re.sub(r"\x00(\d+)\x00", lambda m: blocks[int(m.group(1))], sql)


def prepare_migration(sql: str) -> tuple[str, list[str]]:
    """Make a migration runnable inside one transaction that Ripple can undo."""
    notes: list[str] = []
    masked, blocks = _mask_dollar_quotes(sql)
    masked, removed = _TX_RE.subn("", masked)
    if removed:
        notes.append("Removed BEGIN/COMMIT so the whole check runs in one transaction and is undone at the end.")
    masked, concurrently = _CONCURRENTLY_RE.subn("", masked)
    if concurrently:
        notes.append("Ignored CONCURRENTLY for the check (it can't run inside a transaction).")
    return _unmask(masked, blocks), notes


def _table_name(raw: str) -> str:
    raw = raw.replace('"', "").strip()
    return raw if "." in raw else f"public.{raw}"


def scan_statements(sql: str) -> list[Change]:
    """Find data-changing statements an empty test database can't reveal."""
    masked, _ = _mask_dollar_quotes(sql)
    changes: list[Change] = []
    for statement in masked.split(";"):
        statement = re.sub(r"--[^\n]*", " ", statement).strip()
        if not statement:
            continue
        if match := _TRUNCATE_RE.match(statement):
            for raw in re.split(r",", re.split(r"\b(?:RESTART|CONTINUE|CASCADE|RESTRICT)\b", match.group(1), flags=re.I)[0]):
                if raw.strip():
                    changes.append(Change("rows_deleted", _table_name(raw), detail="TRUNCATE", danger=True))
        elif (match := _DELETE_RE.match(statement)) and not _WHERE_RE.search(statement):
            changes.append(Change("rows_deleted", _table_name(match.group(1)), detail="DELETE without WHERE", danger=True))
        elif (match := _UPDATE_RE.match(statement)) and not _WHERE_RE.search(statement):
            changes.append(Change("rows_updated", _table_name(match.group(1)), detail="UPDATE without WHERE", danger=True))
    return changes


# --- Naming what blocks a failed drop/rename --------------------------------------------


def extract_ddl_targets(sql: str) -> list[tuple[str, str | None]]:
    """Tables/columns a migration drops, or renames away from (the old name is what breaks dependents).

    Best-effort: statements it can't parse are skipped, never raised.
    """
    masked, blocks = _mask_dollar_quotes(sql)
    targets: list[tuple[str, str | None]] = []
    for statement in masked.split(";"):
        statement = _unmask(statement, blocks).strip()
        if not statement:
            continue
        try:
            tree = sqlglot.parse_one(statement, read="postgres")
        except Exception:
            continue
        if isinstance(tree, exp.Drop) and tree.args.get("kind") == "TABLE":
            for table in tree.args.get("tables") or []:
                if isinstance(table, exp.Table) and table.name:
                    raw = f"{table.db}.{table.name}" if table.db else table.name
                    targets.append((_table_name(raw), None))
        elif isinstance(tree, exp.Alter):
            table_node = tree.this
            if not isinstance(table_node, exp.Table) or not table_node.name:
                continue
            raw_table = f"{table_node.db}.{table_node.name}" if table_node.db else table_node.name
            full_table = _table_name(raw_table)
            for action in tree.args.get("actions") or []:
                if isinstance(action, exp.Drop) and action.args.get("kind") == "COLUMN":
                    for col in action.args.get("tables") or []:
                        if isinstance(col, exp.Column) and col.name:
                            targets.append((full_table, col.name))
                elif isinstance(action, exp.RenameColumn):
                    old = action.this
                    if isinstance(old, exp.Column) and old.name:
                        targets.append((full_table, old.name))
    return targets


def find_blocking_objects(cur, table: str, column: str | None) -> list[str]:
    """Views, materialized views, and foreign keys that depend on a table/column. Best-effort: never raises."""
    schema, _, name = table.rpartition(".")
    schema = schema or "public"
    found: list[str] = []
    try:
        if column:
            cur.execute(
                """
                SELECT DISTINCT dependent_ns.nspname, dependent_view.relname, dependent_view.relkind
                FROM pg_depend
                JOIN pg_rewrite ON pg_depend.objid = pg_rewrite.oid
                JOIN pg_class dependent_view ON pg_rewrite.ev_class = dependent_view.oid
                JOIN pg_namespace dependent_ns ON dependent_view.relnamespace = dependent_ns.oid
                JOIN pg_class source_table ON pg_depend.refobjid = source_table.oid
                JOIN pg_namespace source_ns ON source_table.relnamespace = source_ns.oid
                JOIN pg_attribute ON pg_attribute.attrelid = pg_depend.refobjid
                                 AND pg_attribute.attnum = pg_depend.refobjsubid
                WHERE source_ns.nspname = %s AND source_table.relname = %s AND pg_attribute.attname = %s
                  AND dependent_view.relkind IN ('v', 'm') AND dependent_view.relname != source_table.relname
                """,
                (schema, name, column),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT dependent_ns.nspname, dependent_view.relname, dependent_view.relkind
                FROM pg_depend
                JOIN pg_rewrite ON pg_depend.objid = pg_rewrite.oid
                JOIN pg_class dependent_view ON pg_rewrite.ev_class = dependent_view.oid
                JOIN pg_namespace dependent_ns ON dependent_view.relnamespace = dependent_ns.oid
                JOIN pg_class source_table ON pg_depend.refobjid = source_table.oid
                JOIN pg_namespace source_ns ON source_table.relnamespace = source_ns.oid
                WHERE source_ns.nspname = %s AND source_table.relname = %s
                  AND dependent_view.relkind IN ('v', 'm') AND dependent_view.relname != source_table.relname
                """,
                (schema, name),
            )
        for view_schema, view_name, relkind in cur.fetchall():
            kind = "materialized view" if relkind == "m" else "view"
            where = f" (uses {table}.{column})" if column else f" (uses {table})"
            found.append(f"Blocked by: {kind} {view_schema}.{view_name}{where}")

        fk_sql = """
            SELECT con.conname, ns.nspname, cls.relname
            FROM pg_constraint con
            JOIN pg_class cls ON cls.oid = con.conrelid
            JOIN pg_namespace ns ON ns.oid = cls.relnamespace
            JOIN pg_class reftable ON reftable.oid = con.confrelid
            JOIN pg_namespace refns ON refns.oid = reftable.relnamespace
            WHERE con.contype = 'f' AND refns.nspname = %s AND reftable.relname = %s
        """
        cur.execute(fk_sql, (schema, name))
        for conname, fk_schema, fk_table in cur.fetchall():
            where = f" (uses {table}.{column})" if column else f" (uses {table})"
            found.append(f"Blocked by: foreign key {conname} on {fk_schema}.{fk_table}{where}")
    except Exception:
        return found
    return found


def scan_function_bodies(cur, columns: list[tuple[str, str]]) -> list[str]:
    """Functions whose body text mentions a dropped/renamed column's name.

    Best-effort and low-confidence: a name match in function source isn't proof the function
    actually breaks (plpgsql bodies aren't dependency-tracked by Postgres at the column level),
    so this is reported as a note to check manually, never as a `danger`.
    """
    if not columns:
        return []
    try:
        cur.execute(
            """
            SELECT n.nspname, p.proname, p.prosrc
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE p.prosrc IS NOT NULL AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            """
        )
        rows = cur.fetchall()
    except Exception:
        return []
    found: list[str] = []
    for func_schema, func_name, body in rows:
        if not body:
            continue
        for table, column in columns:
            if re.search(rf"\b{re.escape(column)}\b", body):
                found.append(
                    f"function {func_schema}.{func_name}() may reference {table}.{column} — "
                    "verify manually (matched by name only, not confirmed)."
                )
                break
    return found
