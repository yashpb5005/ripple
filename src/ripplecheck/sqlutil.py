"""Small SQL helpers: classify statements, scrub literal values, find what a query touches."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

# Statements Postgres can validate with PREPARE (it plans them without running them).
CHECKABLE_KEYWORDS = {"SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "MERGE", "VALUES", "TABLE"}
WRITE_KINDS = {"insert", "update", "delete", "merge"}

_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_STRING_RE = re.compile(r"'(?:[^']|'')*'")
_DOLLAR_QUOTE_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*|)\$.*?\$\1\$", re.S)
_SYSTEM_RE = re.compile(
    r"\b(pg_catalog|information_schema|pg_stat_\w+|pg_settings|pg_class|pg_attribute|pg_namespace|"
    r"pg_type|pg_proc|pg_roles|pg_database|pg_extension|pg_locks|pg_indexes|pg_tables)\b",
    re.I,
)

# Literal values in logged queries are replaced with $n placeholders so no customer data
# ends up in the queries file.
_LITERAL_RE = re.compile(
    r"(?P<str>(?<![\w$])[eE]?'(?:[^']|'')*')"
    r"|(?P<num>(?<![\w$.])\d+(?:\.\d+)?(?![\w.]))"
)
_TYPED_LITERAL_RE = re.compile(r"(?i)\b(interval|date|time|timestamp|timestamptz)\s*$")
_ORDINAL_RE = re.compile(
    r"(?i)\b(?:order|group)\s+by\s+(?:[\w.\"]+(?:\s+(?:asc|desc))?\s*,\s*)*$"
)


def strip_comments(sql: str) -> str:
    return _COMMENT_RE.sub(" ", sql)


def normalize(sql: str) -> str:
    """Collapse whitespace and trailing semicolons so the same query matches itself."""
    return re.sub(r"\s+", " ", strip_comments(sql)).strip().rstrip(";").strip()


def first_keyword(sql: str) -> str:
    stripped = strip_comments(sql).lstrip(" \t\r\n(")
    match = re.match(r"[A-Za-z]+", stripped)
    return match.group(0).upper() if match else ""


def is_checkable(sql: str) -> bool:
    """True for reads and writes, which Postgres can validate with PREPARE."""
    return first_keyword(sql) in CHECKABLE_KEYWORDS


def is_system_query(sql: str) -> bool:
    """Queries against Postgres internals come from tools, not apps."""
    return bool(_SYSTEM_RE.search(sql))


def has_multiple_statements(sql: str) -> bool:
    """True if a semicolon separates two statements (ignoring strings and comments)."""
    body = _DOLLAR_QUOTE_RE.sub("''", sql)
    body = _STRING_RE.sub("''", strip_comments(body))
    return ";" in body.strip().rstrip(";")


def scrub_literals(sql: str) -> str:
    """Replace literal values with $n placeholders, keeping typed literals and ORDER BY 1."""
    counter = max((int(n) for n in re.findall(r"\$(\d+)", sql)), default=0)
    out: list[str] = []
    pos = 0
    for match in _LITERAL_RE.finditer(sql):
        start, end = match.span()
        before = sql[:start]
        keep = (match.group("str") is not None and _TYPED_LITERAL_RE.search(before)) or (
            match.group("num") is not None and _ORDINAL_RE.search(before[-120:])
        )
        out.append(sql[pos:start])
        if keep:
            out.append(match.group(0))
        else:
            counter += 1
            out.append(f"${counter}")
        pos = end
    out.append(sql[pos:])
    return "".join(out)


@dataclass
class QueryRefs:
    """What a query touches. Tables are 'schema.table' strings."""

    kind: str
    target: str | None = None
    tables: set[str] = field(default_factory=set)
    columns: set[tuple[str, str]] = field(default_factory=set)
    insert_columns: list[str] | None = None
    parsed: bool = True


def full_name(schema: str | None, table: str) -> str:
    return f"{schema or 'public'}.{table}"


def _kind_of(tree: exp.Expression, fallback: str) -> str:
    for cls, kind in (
        (exp.Insert, "insert"),
        (exp.Update, "update"),
        (exp.Delete, "delete"),
        (exp.Merge, "merge"),
        (exp.Query, "select"),
    ):
        if isinstance(tree, cls):
            return kind
    return fallback


def extract_refs(sql: str, schema: dict[str, set[str]] | None = None) -> QueryRefs:
    """Find the tables and columns a query uses.

    `schema` maps 'schema.table' to its column names. With it, unqualified columns in
    joins can be matched to the right table; without it, they are only matched when the
    query uses a single table.
    """
    fallback = first_keyword(sql).lower()
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return QueryRefs(kind=fallback, parsed=False)
    if tree is None:
        return QueryRefs(kind=fallback, parsed=False)

    refs = QueryRefs(kind=_kind_of(tree, fallback))
    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    aliases: dict[str, str] = {}

    for table in tree.find_all(exp.Table):
        if not table.name or (not table.db and table.name in cte_names):
            continue
        name = full_name(table.db, table.name)
        refs.tables.add(name)
        aliases[table.alias_or_name] = name
        aliases.setdefault(table.name, name)

    if isinstance(tree, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        node = tree.this
        if isinstance(node, exp.Schema):
            node = node.this
        if isinstance(node, exp.Table) and node.name:
            refs.target = full_name(node.db, node.name)

    if isinstance(tree, exp.Insert) and isinstance(tree.this, exp.Schema) and refs.target:
        refs.insert_columns = [col.name for col in tree.this.expressions]
        refs.columns.update((refs.target, col) for col in refs.insert_columns)

    for column in tree.find_all(exp.Column):
        name = column.name
        if not name or name == "*":
            continue
        if column.table:
            table = aliases.get(column.table)
            if table:
                refs.columns.add((table, name))
            continue
        candidates = sorted(refs.tables)
        if schema is not None:
            candidates = [t for t in candidates if name in schema.get(t, ())]
        if len(candidates) == 1:
            refs.columns.add((candidates[0], name))

    return refs
