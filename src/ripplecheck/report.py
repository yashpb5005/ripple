"""Turn a check result into words: terminal text, Markdown (pull requests), or JSON."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

from .check import CheckResult, describe_change
from .schema import Change


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def summary(result: CheckResult) -> str:
    if result.migration_error:
        return f"The change itself fails on this database: {result.migration_error}"
    checked = [q for q in result.queries if q.status != "skipped"]
    breaks = result.breaks
    if not result.queries:
        return "No app queries recorded yet, so there was nothing to check. Run `ripple collect` first."
    if not breaks:
        if result.dangers:
            return f"No app queries break, but there {'is' if len(result.dangers) == 1 else 'are'} {_plural(len(result.dangers), 'data warning', 'data warnings')}."
        return (
            f"Safe. None of the {_plural(len(checked), 'query', 'queries')} from "
            f"{_plural(len(result.checked_apps), 'app', 'apps')} break, and no data is at risk."
        )
    text = (
        f"This change breaks {_plural(len(result.broken_apps), 'app', 'apps')} "
        f"({_plural(len(breaks), 'query', 'queries')})."
    )
    if result.fixes_requested:
        ready = result.fixes_ready
        need = len(breaks) - ready
        text += f" {_plural(ready, 'fix', 'fixes')} ready. {need} {'needs' if need == 1 else 'need'} you."
    return text


def _users_of(change: Change, result: CheckResult) -> tuple[str, list[str]]:
    if change.kind in ("required_no_default", "now_required"):
        return "Inserted into by", result.inserters.get(change.table, [])
    if change.column and result.column_users.get(f"{change.table}.{change.column}"):
        return "Used by", result.column_users[f"{change.table}.{change.column}"]
    return "Used by", result.users.get(change.table, [])


def warning_text(change: Change) -> list[str]:
    """Plain-English lines explaining one risky change and what to do instead."""
    col = f"{change.table}.{change.column}"
    match change.kind:
        case "table_dropped":
            return [f"Table {change.table} is deleted, with all its data."]
        case "table_recreated":
            return [f"Table {change.table} is deleted and created again. All existing rows are lost."]
        case "column_dropped":
            lines = [f"Column {col} is deleted, with all its data."]
            if change.new_name:
                lines += [
                    f"This looks like a rename done as delete + add ({change.new_name} is added). To keep the data, use:",
                    f"ALTER TABLE {change.table} RENAME COLUMN {change.column} TO {change.new_name};",
                ]
            return lines
        case "type_changed":
            return [
                f"Column {col} changes type ({change.detail}).",
                "Existing values get converted. On real data this can fail or lose detail.",
            ]
        case "now_required":
            return [f"Column {col} becomes required. This fails if any existing row has no value."]
        case "required_no_default":
            return [
                f"Required column {col} is added with no default value.",
                "This fails on a table that already has rows. Add a DEFAULT, or add it as optional, fill it in, then make it required.",
            ]
        case "rows_deleted" | "rows_updated":
            return [describe_change(change)]
    return [describe_change(change)]


def _symbols() -> dict[str, str]:
    fancy = {"bad": "✘", "good": "✔", "warn": "⚠", "dot": "•"}
    try:
        "".join(fancy.values()).encode(sys.stdout.encoding or "utf-8")
        return fancy
    except (UnicodeEncodeError, LookupError):
        return {"bad": "x", "good": "ok", "warn": "!", "dot": "-"}


def render_text(result: CheckResult, verbose: bool = False) -> str:
    s = _symbols()
    lines: list[str] = []
    headline = summary(result)
    ok = not result.breaks and not result.dangers and not result.migration_error
    lines.append(f"{s['good'] if ok else s['bad']} {headline}")

    by_app: dict[str, list] = {}
    for item in result.breaks:
        by_app.setdefault(item.app, []).append(item)
    for app, items in by_app.items():
        lines.append("")
        lines.append(app)
        for item in items:
            lines.append(f"  {s['bad']} {item.sql}")
            lines.append(f"    {item.error}")
            if item.fix_status == "verified":
                lines.append(f"    fix {s['good']} {item.fix}")
            elif item.fix_status == "failed":
                lines.append(f"    suggested fix didn't pass the check: {item.fix}")
            elif result.fixes_requested:
                lines.append("    needs you")

    if result.dangers:
        lines.append("")
        lines.append(f"{s['warn']} Data warnings")
        for change in result.dangers:
            text = warning_text(change)
            lines.append(f"  {s['warn']} {text[0]}")
            lines.extend(f"    {extra}" for extra in text[1:])
            label, apps = _users_of(change, result)
            if apps:
                lines.append(f"    {label}: {', '.join(apps)}")

    if result.changes and verbose:
        lines.append("")
        lines.append("What the change does")
        lines.extend(f"  {s['dot']} {describe_change(c)}" for c in result.changes)

    if result.skipped:
        lines.append("")
        lines.append(
            f"{_plural(len(result.skipped), 'query', 'queries')} couldn't be checked because "
            f"{'it already fails' if len(result.skipped) == 1 else 'they already fail'} on the current schema."
            + ("" if verbose else " Use --verbose to see them.")
        )
        if verbose:
            for item in result.skipped:
                lines.append(f"  {s['dot']} [{item.app}] {item.sql}")
                lines.append(f"    {item.error}")

    for note in result.notes:
        lines.append(f"{s['dot']} {note}")
    return "\n".join(lines) + "\n"


def render_markdown(result: CheckResult) -> str:
    ok = not result.breaks and not result.dangers and not result.migration_error
    icon = "✅" if ok else "❌" if (result.breaks or result.migration_error) else "⚠️"
    out = [f"### {icon} Ripple · {summary(result)}", ""]

    by_app: dict[str, list] = {}
    for item in result.breaks:
        by_app.setdefault(item.app, []).append(item)
    for app, items in by_app.items():
        out.append(f"#### `{app}`")
        for item in items:
            out += ["```sql", item.sql, "```", f"**Problem:** {item.error}  "]
            if item.fix_status == "verified":
                out += ["**Fix (checked against the new schema ✅):**", "```sql", item.fix or "", "```"]
            elif item.fix_status == "failed":
                out += ["**Suggested fix (did not pass the check):**", "```sql", item.fix or "", "```"]
            elif result.fixes_requested:
                out.append("**Needs you.**")
            out.append("")

    if result.dangers:
        out.append("#### ⚠️ Data warnings")
        for change in result.dangers:
            text = warning_text(change)
            label, apps = _users_of(change, result)
            details = [f"`{extra}`" if extra.upper().startswith("ALTER ") else extra for extra in text[1:]]
            if apps:
                details.append(f"{label}: {', '.join(f'`{a}`' for a in apps)}")
            out.append(f"- **{text[0]}**" + ("  " if details else ""))
            out.extend(f"  {line}  " for line in details)
        out.append("")

    if result.changes:
        out += ["<details><summary>What the change does</summary>", ""]
        out += [f"- {describe_change(c)}" for c in result.changes]
        out += ["", "</details>", ""]

    if result.skipped:
        out += [
            f"<details><summary>{_plural(len(result.skipped), 'query', 'queries')} couldn't be checked</summary>",
            "",
        ]
        for item in result.skipped:
            out.append(f"- `{item.app}`: {item.error}")
        out += ["", "</details>", ""]

    for note in result.notes:
        out.append(f"> {note}")
    checked = [q for q in result.queries if q.status != "skipped"]
    out.append("")
    out.append(
        f"<sub>Checked {_plural(len(checked), 'query', 'queries')} from {_plural(len(result.checked_apps), 'app', 'apps')} "
        "on a scratch database. Nothing was saved.</sub>"
    )
    return "\n".join(out) + "\n"


def render_json(result: CheckResult) -> str:
    payload = {
        "summary": summary(result),
        "broken_apps": result.broken_apps,
        "fixes_ready": result.fixes_ready,
        "migration_error": result.migration_error,
        "queries": [asdict(q) for q in result.queries],
        "changes": [asdict(c) | {"description": describe_change(c)} for c in result.changes],
        "users": result.users,
        "notes": result.notes,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
