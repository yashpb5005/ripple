import asyncio
import json

import pytest

from ripplecheck.cli import main


def test_check_exit_codes_and_json(scratch_dsn, demo_dir, capsys):
    queries = str(demo_dir / "queries.json")
    assert main(["check", str(demo_dir / "migration.sql"), "--dsn", scratch_dsn, "--queries", queries]) == 1
    capsys.readouterr()
    assert main(["check", str(demo_dir / "migration-safe.sql"), "--dsn", scratch_dsn, "--queries", queries,
                 "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["broken_apps"] == []
    assert main(["check", str(demo_dir / "migration.sql"), "--dsn", scratch_dsn, "--queries", queries,
                 "--fail-on", "never"]) == 0


def test_fail_on_danger(scratch_dsn, demo_dir, tmp_path):
    # Emptying a table breaks no query, but it is a data warning.
    change = tmp_path / "empty_invoices.sql"
    change.write_text("TRUNCATE invoices;")
    args = ["check", str(change), "--dsn", scratch_dsn, "--queries", str(demo_dir / "queries.json")]
    assert main(args) == 0
    assert main(args + ["--fail-on", "danger"]) == 1


def test_missing_queries_file(capsys, tmp_path):
    code = main(["check", "x.sql", "--dsn", "postgresql://unused", "--queries", str(tmp_path / "none.json")])
    assert code == 2
    assert "ripple collect" in capsys.readouterr().err


def test_map_command(demo_dir, capsys):
    assert main(["map", "--queries", str(demo_dir / "queries.json"), "--table", "invoices"]) == 0
    out = capsys.readouterr().out
    assert "written by: invoice-bot" in out


def test_mcp_tools(scratch_dsn, demo_dir):
    pytest.importorskip("mcp")
    from ripplecheck.mcp_server import create_server

    server = create_server(scratch_dsn, str(demo_dir / "queries.json"))

    async def go():
        names = {t.name for t in await server.list_tools()}
        assert names == {"check_migration", "who_uses"}
        result = await server.call_tool("check_migration", {"sql": "ALTER TABLE invoices DROP COLUMN status"})
        return str(result)

    text = asyncio.run(go())
    assert "invoice-bot" in text and "deleted" in text
