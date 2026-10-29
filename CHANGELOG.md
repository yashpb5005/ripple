# Changelog

All notable changes to Ripple are documented here.

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
