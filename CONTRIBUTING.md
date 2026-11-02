# Contributing to Ripple

Thanks for helping! Small, focused pull requests are easiest to review.

## Set up

```bash
git clone https://github.com/yashpb5005/ripple
cd ripple
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Run the tests

Unit tests run anywhere. Database tests need a Postgres you can create databases on:

```bash
docker run -d --name ripple-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16 -c shared_preload_libraries=pg_stat_statements
RIPPLE_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres pytest -q
```

Each database test creates its own throwaway database and drops it afterwards. `-c shared_preload_libraries=pg_stat_statements` is only needed for `tests/test_collect_stats.py`; without it, that file's tests skip themselves instead of failing.

## Test data provenance

`tests/fixtures/pagila_subset.sql` is a trimmed, simplified subset of the [Pagila](https://github.com/devrimgunduz/pagila) sample database (PostgreSQL License — permissive, MIT-style), used by `tests/test_dependencies.py` because it needs real views, a trigger function, and a foreign key, which the custom `examples/demo` schema intentionally doesn't have. It's kept separate from `examples/demo`, which stays exactly as documented in the README quickstart. Any future third-party fixture should carry the same kind of source + license header at the top of the file.

## Where things live

| File | What it does |
| --- | --- |
| `src/ripplecheck/collect.py` | Reads each app's queries from pg_stat_statements or Postgres logs |
| `src/ripplecheck/sqlutil.py` | Finds which tables and columns a query uses |
| `src/ripplecheck/schema.py` | Snapshots the structure and describes what a change did |
| `src/ripplecheck/check.py` | The check: validate, apply, validate again, roll back |
| `src/ripplecheck/report.py` | Turns results into text, Markdown and JSON |
| `src/ripplecheck/fixer.py` | Optional AI fixes |
| `src/ripplecheck/mcp_server.py` | MCP server for AI coding agents |
| `action.yml` | The GitHub Action |

## Good first issues

- A new warning: say what it catches, add a test in `tests/test_schema.py` or `tests/test_check.py`.
- Better wording in reports. Reports should be plain English that anyone on the team understands.
- Support for another log format.

## Rules of thumb

- Ripple must never change a real database. Everything runs inside a transaction that is rolled back.
- No customer data in files Ripple writes.
- A fix only counts as "ready" if Postgres accepted it.
