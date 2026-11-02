# Changelog

All notable changes to Ripple are documented here.

## [v0.2.0] — 2026-11-02

### Added

- `ripple check` now shows the full Postgres error detail on a failing migration, and best-effort names the specific view, materialized view, or foreign key blocking a `DROP`/`RENAME COLUMN`.
- `ripple check` adds a low-confidence note when a function or trigger body may reference a dropped or renamed column, clearly hedged as a name match and excluded from `--fail-on danger`.
- `ripple check` now warns when other connections are active on the target database before applying a migration, since the exclusive locks it takes for the check's duration can affect them.
- `ripple collect` records the `pg_stat_statements` coverage window (`stats_since`, its last reset time) in the saved queries file and prints it; `ripple check` surfaces it as a note. Queries files saved by v0.1.0 still load fine without it.
- `ripple collect` warns when `pg_stat_statements.max` is near its tracked-query cap, since older queries may already be evicted.
- `ripple collect` warns when a single database role has multiple distinct, currently-connected application names, since Ripple will attribute all of its queries to one app.
- Documented in the README that application-side field unpacking (JSON payloads, ORM models) is outside what Ripple can see, since it only reads SQL.

### Project setup

- Added `tests/fixtures/pagila_subset.sql`, a trimmed, attributed subset of the Pagila sample database (PostgreSQL License), used by new dependency-tracking tests, kept separate from `examples/demo`.

## [v0.1.0] — 2026-10-29

Initial public release.

### Added

- `ripple check` — test a migration against a scratch database before it ships: validates every recorded app query, applies the change, validates again, then rolls everything back. Supports `--fix` (AI-suggested query fixes, re-checked before being shown), `--format text|markdown|json`, and `--fail-on breaks|danger|never`.
- `ripple collect` — record each app's queries from `pg_stat_statements` (`--dsn`) or a Postgres csvlog/jsonlog file (`--log`).
- `ripple map` — show which app reads or writes which table and column.
- `ripple mcp` — run as an [MCP](https://modelcontextprotocol.io) server exposing `check_migration` and `who_uses`, so AI coding agents (Claude Code, Cursor, etc.) can test a change before making it.
- A ready-to-use GitHub Action (`action.yml`) that comments on pull requests and fails the check if a migration would break a dependent app.
- A runnable demo (`examples/demo`) with a throwaway Postgres schema and both a breaking and a safe migration, used in the README quickstart and in CI.
- Full test suite (`tests/`) and GitHub Actions CI, including a self-test of the Action against the demo.
- MIT license, `CONTRIBUTING.md`, and a usage disclaimer.

### Project setup

- Published to GitHub at [github.com/yashpb5005/ripple](https://github.com/yashpb5005/ripple), tagged `v0.1.0`, with a matching GitHub Release.
- README rewritten with a numbered **Installation** section (prerequisites, clone, install, verify, run the demo, tear it down) so the project can be downloaded and run without guesswork.
- Added a **Disclaimer** section: Ripple is an independent personal project (Yash Bhargav), provided as-is, meant to run only against scratch/non-production databases, used entirely at the user's own risk.
- `pip install git+https://github.com/yashpb5005/ripple.git@v0.1.0` and the GitHub Action's `uses: yashpb5005/ripple@v0.1.0` reference both resolve against the `v0.1.0` tag.
