"""The map: which app reads or writes which table and column."""

from __future__ import annotations

import json

from .collect import AppQuery
from .sqlutil import WRITE_KINDS, extract_refs


def build_usage(queries: list[AppQuery], schema: dict[str, set[str]] | None = None) -> dict[str, dict]:
    tables: dict[str, dict] = {}

    def entry(name: str) -> dict:
        return tables.setdefault(name, {"reads": set(), "writes": set(), "columns": {}})

    for query in queries:
        refs = extract_refs(query.sql, schema)
        for table in refs.tables:
            if refs.kind in WRITE_KINDS and refs.target == table:
                entry(table)["writes"].add(query.app)
            else:
                entry(table)["reads"].add(query.app)
        for table, column in refs.columns:
            entry(table)["columns"].setdefault(column, set()).add(query.app)
    return tables


def _matches(name: str, wanted: str | None) -> bool:
    if not wanted:
        return True
    wanted = wanted.strip().lower()
    return name.lower() == wanted or name.lower() == f"public.{wanted}"


def render_usage_text(usage: dict[str, dict], table: str | None = None) -> str:
    lines: list[str] = []
    for name in sorted(usage):
        if not _matches(name, table):
            continue
        info = usage[name]
        lines.append(name)
        if info["reads"]:
            lines.append(f"  read by:    {', '.join(sorted(info['reads']))}")
        if info["writes"]:
            lines.append(f"  written by: {', '.join(sorted(info['writes']))}")
        for column in sorted(info["columns"]):
            lines.append(f"    {column:<20} {', '.join(sorted(info['columns'][column]))}")
        lines.append("")
    if not lines:
        return f"No recorded app uses {table}.\n" if table else "No recorded queries.\n"
    return "\n".join(lines)


def usage_as_json(usage: dict[str, dict], table: str | None = None) -> str:
    data = {
        name: {
            "reads": sorted(info["reads"]),
            "writes": sorted(info["writes"]),
            "columns": {col: sorted(apps) for col, apps in sorted(info["columns"].items())},
        }
        for name, info in sorted(usage.items())
        if _matches(name, table)
    }
    return json.dumps(data, indent=2) + "\n"
