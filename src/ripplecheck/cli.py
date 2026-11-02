"""Command line: `ripple collect`, `ripple map`, `ripple check`, `ripple mcp`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .collect import AppQuery, from_csvlog, from_jsonlog, from_pg_stat_statements, load_metadata, load_queries, save_queries

DEFAULT_QUERIES = "ripple-queries.json"
ENV_DSN = "RIPPLE_DATABASE_URL"


def _fail(message: str, code: int = 2) -> int:
    print(f"ripple: {message}", file=sys.stderr)
    return code


def _scratch_dsn(args) -> str | None:
    return args.dsn or os.environ.get(ENV_DSN)


def cmd_collect(args) -> int:
    warnings: list[str] = []
    stats_since: list = []
    source = args.source
    if not source:
        source = "stats"
        if args.log:
            source = "jsonlog" if args.log.endswith((".json", ".jsonl")) else "csvlog"
    try:
        if source == "stats":
            if not args.dsn:
                return _fail("collect needs --dsn (a read-only connection is enough).")
            queries = from_pg_stat_statements(args.dsn, args.min_calls, args.exclude_role, warnings, stats_since)
        elif source in ("csvlog", "jsonlog"):
            if not args.log:
                return _fail(f"--source {source} needs --log PATH.")
            reader = from_csvlog if source == "csvlog" else from_jsonlog
            queries = reader(args.log, by=args.by)
        else:
            return _fail(f"unknown source {source!r}.")
    except Exception as error:
        text = str(error)
        if "pg_stat_statements" in text:
            return _fail(
                "pg_stat_statements isn't enabled on this database. Turn it on "
                "(CREATE EXTENSION pg_stat_statements; it must also be in shared_preload_libraries), "
                "or read a Postgres log file with --log."
            )
        return _fail(text.strip().splitlines()[0] if text.strip() else repr(error))

    output = Path(args.output)
    if args.merge and output.exists():
        existing = load_queries(output)
        seen = {(q.app, q.sql) for q in queries}
        queries = queries + [q for q in existing if (q.app, q.sql) not in seen]

    since = stats_since[0] if stats_since else None
    save_queries(queries, output, source, stats_since=since)
    apps = sorted({q.app for q in queries})
    print(f"Saved {len(queries)} queries from {len(apps)} apps to {output}.")
    if apps:
        print("Apps: " + ", ".join(apps))
    if since:
        print(f"Covers queries since {since} (pg_stat_statements' last reset).")
    for warning in warnings:
        print(f"Warning: {warning}")
    if len(apps) == 1 and source == "stats":
        print(
            "Tip: every query came from one database user, so all apps look like one. "
            "Give each app its own database user to tell them apart."
        )
    return 0


def _load(path: str) -> list[AppQuery] | None:
    if not Path(path).exists():
        print(
            f"ripple: {path} not found. Record your apps' queries first with `ripple collect`, "
            "or write the file by hand (see the README).",
            file=sys.stderr,
        )
        return None
    return load_queries(path)


def cmd_map(args) -> int:
    from .usage import build_usage, render_usage_text, usage_as_json

    queries = _load(args.queries)
    if queries is None:
        return 2
    schema = None
    dsn = _scratch_dsn(args)
    if dsn:
        import psycopg

        from .schema import take_snapshot

        with psycopg.connect(dsn) as conn:
            schema = take_snapshot(conn.cursor()).live_columns()
    usage = build_usage(queries, schema)
    print(usage_as_json(usage, args.table) if args.json else render_usage_text(usage, args.table), end="")
    return 0


def _read_migrations(paths: list[str]) -> str:
    parts = []
    for path in paths:
        if path == "-":
            parts.append(sys.stdin.read())
        else:
            parts.append(Path(path).read_text(encoding="utf-8"))
    return "\n;\n".join(part.strip().rstrip(";") for part in parts if part.strip()) + "\n"


def cmd_check(args) -> int:
    from .check import run_check
    from .report import render_json, render_markdown, render_text

    dsn = _scratch_dsn(args)
    if not dsn:
        return _fail(f"check needs --dsn (or {ENV_DSN}) pointing at a scratch database with your current schema.")
    queries = _load(args.queries)
    if queries is None:
        return 2
    try:
        migration = _read_migrations(args.migration)
    except OSError as error:
        return _fail(str(error))

    fixer = None
    if args.fix:
        from .fixer import make_fixer

        try:
            fixer = make_fixer(args.llm, args.model)
        except RuntimeError as error:
            return _fail(str(error))

    try:
        result = run_check(dsn, migration, queries, fixer=fixer)
    except Exception as error:
        text = str(error).strip()
        return _fail(f"couldn't run the check: {text.splitlines()[0] if text else repr(error)}")

    try:
        since = load_metadata(args.queries).get("stats_since")
    except Exception:
        since = None
    if since:
        result.notes.append(
            f"Based on queries seen since {since} — collect again after a full traffic cycle if your apps have changed."
        )

    if args.format == "json":
        print(render_json(result), end="")
    elif args.format == "markdown":
        print(render_markdown(result), end="")
    else:
        print(render_text(result, verbose=args.verbose), end="")

    failed = bool(result.breaks or result.migration_error)
    if args.fail_on == "danger":
        failed = failed or bool(result.dangers)
    return 1 if failed and args.fail_on != "never" else 0


def cmd_mcp(args) -> int:
    from .mcp_server import serve

    dsn = _scratch_dsn(args)
    if not dsn:
        return _fail(f"mcp needs --dsn (or {ENV_DSN}) pointing at a scratch database with your current schema.")
    try:
        serve(dsn, args.queries)
    except RuntimeError as error:
        return _fail(str(error))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ripple", description="See which apps a Postgres change will break, before it ships."
    )
    parser.add_argument("--version", action="version", version=f"ripple {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="record which queries each app runs")
    p.add_argument("--dsn", help="database to read pg_stat_statements from (read-only is fine)")
    p.add_argument("--source", choices=["stats", "csvlog", "jsonlog"], help="where to read queries from")
    p.add_argument("--log", help="Postgres csvlog or jsonlog file")
    p.add_argument("--by", choices=["application_name", "user"], default="application_name",
                   help="how to tell apps apart in log files")
    p.add_argument("--min-calls", type=int, default=1, help="ignore queries run fewer times than this")
    p.add_argument("--exclude-role", action="append", default=[], help="database user to ignore (repeatable)")
    p.add_argument("--merge", action="store_true", help="add to the existing file instead of replacing it")
    p.add_argument("-o", "--output", default=DEFAULT_QUERIES)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("map", help="show which app uses which table and column")
    p.add_argument("--queries", default=DEFAULT_QUERIES)
    p.add_argument("--dsn", help="scratch database, used to match columns to tables more precisely")
    p.add_argument("--table", help="only show this table")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_map)

    p = sub.add_parser("check", help="test a change against every recorded app query")
    p.add_argument("migration", nargs="+", help="SQL file(s) with the change, applied in order ('-' for stdin)")
    p.add_argument("--dsn", help=f"scratch database with your current schema (or set {ENV_DSN})")
    p.add_argument("--queries", default=DEFAULT_QUERIES)
    p.add_argument("--fix", action="store_true", help="ask an AI to suggest fixes, then re-check them")
    p.add_argument("--llm", choices=["anthropic", "openai"], help="AI provider for --fix")
    p.add_argument("--model", help="model name for --fix")
    p.add_argument("--format", choices=["text", "markdown", "json"], default="text")
    p.add_argument("--fail-on", choices=["breaks", "danger", "never"], default="breaks",
                   help="when to exit with an error code (default: breaks)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("mcp", help="run as an MCP server so AI coding agents check before changing the database")
    p.add_argument("--dsn", help=f"scratch database with your current schema (or set {ENV_DSN})")
    p.add_argument("--queries", default=DEFAULT_QUERIES)
    p.set_defaults(func=cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
