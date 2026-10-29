"""MCP server: lets AI coding agents (Claude Code, Cursor, ...) check a database change
before they apply it, and see which apps depend on a table."""

from __future__ import annotations

from pathlib import Path

INSTRUCTIONS = (
    "Before you change a PostgreSQL schema (ALTER, DROP, CREATE, RENAME, migrations), call "
    "check_migration with the exact SQL. It tests the change on a scratch database, lists the apps "
    "and queries it would break, warns about data loss, and undoes everything afterwards. "
    "Use who_uses to see which apps read or write a table before you touch it."
)


def _server_class():
    try:
        from mcp.server.mcpserver import MCPServer  # mcp >= 2

        return MCPServer
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP  # mcp 1.x

            return FastMCP
        except ImportError as error:  # pragma: no cover - depends on the environment
            raise RuntimeError("The MCP server needs the extra package: pip install 'ripplecheck[mcp]'") from error


def create_server(dsn: str, queries_path: str):
    from .check import run_check
    from .collect import load_queries
    from .report import render_markdown
    from .usage import build_usage, usage_as_json

    server = _server_class()("ripple", instructions=INSTRUCTIONS)

    def queries():
        path = Path(queries_path)
        return load_queries(path) if path.exists() else []

    @server.tool()
    def check_migration(sql: str, suggest_fixes: bool = False) -> str:
        """Test a schema change on a scratch copy of the database before applying it.

        Returns which apps and queries would break, plus data-loss warnings. Nothing is
        saved: the change is always rolled back. Set suggest_fixes to also get AI-suggested
        query fixes (needs an API key in the server's environment).
        """
        fixer = None
        if suggest_fixes:
            from .fixer import make_fixer

            try:
                fixer = make_fixer()
            except RuntimeError as error:
                return f"Can't suggest fixes: {error}"
        return render_markdown(run_check(dsn, sql, queries(), fixer=fixer))

    @server.tool()
    def who_uses(table: str = "") -> str:
        """Show which apps read or write a table and which columns they use. Leave table empty for all tables."""
        return usage_as_json(build_usage(queries()), table or None)

    return server


def serve(dsn: str, queries_path: str) -> None:
    create_server(dsn, queries_path).run("stdio")
