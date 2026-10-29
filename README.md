# Ripple 🌊

**See which apps a Postgres change will break, before it ships.**

Every database change ripples out to the apps that use it. With AI, everyone now builds their own dashboards, bots, agents and tools on the same database, and nobody keeps a list of who depends on what. Ripple keeps that list, tests every change against it, and tells you what breaks.

```text
$ ripple check migration.sql
✘ This change breaks 3 apps (4 queries).

sales-dashboard
  ✘ SELECT customer_type, count(*) FROM customers GROUP BY customer_type
    column "customer_type" does not exist
  ...

invoice-bot
  ✘ INSERT INTO invoices (customer_id, amount) VALUES ($1, $2) RETURNING id
    doesn't set the new required column currency, so it will fail when it runs

⚠ Data warnings
  ⚠ Column public.customers.customer_type is deleted, with all its data.
    This looks like a rename done as delete + add (segment is added). To keep the data, use:
    ALTER TABLE public.customers RENAME COLUMN customer_type TO segment;
    Used by: sales-dashboard, support-bot
  ⚠ Required column public.invoices.currency is added with no default value.
    This fails on a table that already has rows. Add a DEFAULT, or add it as optional, fill it in, then make it required.
    Inserted into by: invoice-bot
```

Add `--fix` and an AI rewrites the broken queries. Ripple re-checks every suggestion against the new schema, so you only see fixes that actually work:

```text
✘ This change breaks 3 apps (4 queries). 3 fixes ready. 1 needs you.
```

## Why this exists

- **AI changes databases all day, and makes the same mistakes.** It renames a column by deleting it and adding a new one (the data is gone). It adds a required column with no default (works on your laptop, fails in production). It changes types.
- **Nobody knows who depends on what.** A company used to have 50 apps on its database. Now it has hundreds of AI-built tools, each changing every week.
- **Broken code is cheap to fix now. Not knowing it's broken is the expensive part.**

## Installation

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) — only needed to run the included demo database; not required once you point Ripple at your own Postgres
- [Python 3.10+](https://www.python.org/downloads/)
- `pip` (comes with Python)

### 1. Clone the repository

```bash
git clone https://github.com/yashpb5005/ripple.git
cd ripple
```

### 2. Install Ripple

```bash
pip install ripplecheck
```

Or, to work on Ripple itself from a clone:

```bash
pip install -e .
```

Check it installed correctly:

```bash
ripple --help
```

### 3. Try the included demo

This spins up a throwaway, local-only Postgres container with a sample schema, so you can see Ripple catch a breaking change without touching any real database:

```bash
cd examples/demo
docker compose up -d                  # starts a throwaway Postgres on localhost:5433

ripple check migration.sql --dsn postgresql://postgres:postgres@localhost:5433/postgres --queries queries.json
ripple check migration-safe.sql --dsn postgresql://postgres:postgres@localhost:5433/postgres --queries queries.json
ripple map --queries queries.json
```

`migration.sql` is what an AI wrote for "rename customer type to segment, and track invoice currency" — it breaks things. `migration-safe.sql` does the same job safely. Run both and compare the output.

When you're done, remove the demo database:

```bash
docker compose down -v
```

## Use it on your own database

**1. Give each app its own database user.** That's how Ripple tells apps apart. (Using log files instead? Set `application_name` per app; see below.)

**2. Record what each app runs.** Turn on [`pg_stat_statements`](https://www.postgresql.org/docs/current/pgstatstatements.html) (many hosts include it, so you may only need `CREATE EXTENSION pg_stat_statements;`), let your apps run for a while, then:

```bash
ripple collect --dsn "$READ_ONLY_PROD_URL" --exclude-role postgres
# Saved 42 queries from 5 apps to ripple-queries.json.
```

The user you collect with needs to see other users' queries: `GRANT pg_read_all_stats TO your_read_only_user;`

`collect` only reads. `pg_stat_statements` stores queries without their values, so no customer data ends up in the file. Commit `ripple-queries.json` and run `collect --merge` now and then to keep it fresh.

**3. Check changes against an empty copy of your schema.** Never against production:

```bash
pg_dump --schema-only "$PROD_URL" | psql "$SCRATCH_URL"     # one-time copy, no data
ripple check new_migration.sql --dsn "$SCRATCH_URL"
```

Any plain SQL migration works, including Prisma, Supabase and Flyway migration files.

## Let your AI coding agent check first (MCP)

Ripple runs as an [MCP](https://modelcontextprotocol.io) server, so agents like Claude Code and Cursor can test a change before they make it.

**Claude Code** (run in your project folder):

```bash
pip install "ripplecheck[mcp]"
claude mcp add --transport stdio ripple -- ripple mcp --dsn "$SCRATCH_URL" --queries ripple-queries.json
```

**Any MCP client** (`.mcp.json` in your project, or your client's config file):

```json
{
  "mcpServers": {
    "ripple": {
      "type": "stdio",
      "command": "ripple",
      "args": ["mcp", "--queries", "ripple-queries.json"],
      "env": { "RIPPLE_DATABASE_URL": "postgresql://postgres:postgres@localhost:5433/postgres" }
    }
  }
}
```

The agent gets two tools: `check_migration` (test a change, nothing is saved) and `who_uses` (which apps read or write a table).

## Check every pull request (GitHub Action)

Copy [`examples/github-workflow.yml`](examples/github-workflow.yml) into your repo. Every pull request that adds a migration gets a comment like the one above, and the check fails if apps would break.

```yaml
- uses: yashpb5005/ripple@v0.1.0
  with:
    migration: db/migrations/0042_new.sql
    database-url: ${{ env.DATABASE_URL }}   # a Postgres service with today's schema
```

## AI fixes

```bash
export ANTHROPIC_API_KEY=...        # or OPENAI_API_KEY (+ OPENAI_BASE_URL for Ollama, vLLM, ...) and RIPPLE_MODEL
pip install "ripplecheck[ai]"
ripple check migration.sql --fix
```

The AI only suggests. Ripple checks each suggestion against the changed schema and marks it **ready** only if Postgres accepts it. Anything that needs a business decision (like which currency old invoices should get) is left for you.

## How it works

1. **Record.** `ripple collect` reads `pg_stat_statements` or your Postgres logs and saves each app's queries.
2. **Test.** `ripple check` opens one transaction on the scratch database, asks Postgres to validate every query (`PREPARE` plans a query without running it), applies your change, and validates them all again.
3. **Compare.** It snapshots the structure before and after. Columns are tracked by their internal id, so a real `RENAME` is told apart from a delete-and-add.
4. **Undo.** The transaction is always rolled back. Nothing is saved, even if your file says `COMMIT`.

## Commands

| Command | What it does |
| --- | --- |
| `ripple collect` | Record each app's queries (`--dsn` for pg_stat_statements, or `--log` for a csvlog/jsonlog file) |
| `ripple map` | Show which app reads or writes which table and column |
| `ripple check FILE...` | Test a change. `--fix` for AI fixes, `--format markdown\|json`, `--fail-on breaks\|danger\|never` |
| `ripple mcp` | Run as an MCP server for AI coding agents |

Exit codes: `0` safe, `1` breaks (or warnings with `--fail-on danger`), `2` error.

**Using log files instead of pg_stat_statements:** set `log_destination = 'csvlog'` (or `jsonlog`), `log_statement = 'all'` and a different `application_name` per app, then `ripple collect --log postgresql.csv`. Values are replaced with `$1, $2...` before saving, but review the file before you commit it.

## What it can't catch (yet)

- **Problems that depend on your data**, like duplicates blocking a new `UNIQUE` index. Ripple warns where it can (required columns, type changes), but the scratch copy is empty.
- **Queries it never saw.** Only queries that actually ran get recorded, so collect over a normal week.
- **Changes in meaning.** If a column keeps its name but means something new, every query still "works".
- **Other databases.** Postgres only, for now.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release notes. Published on [PyPI](https://pypi.org/project/ripplecheck/).

## Roadmap

- [ ] Open pull requests with the fixes in each app's repo
- [ ] MySQL and SQLite
- [ ] Detect queries built by ORMs from source code, not just from logs
- [ ] A hosted dashboard of who uses what, across teams

Ideas and pull requests are very welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing

Ripple is open source and open to contributions — bug fixes, new warnings, docs, support for other log formats, anything in the [Roadmap](#roadmap) above. Fork the repo, make your change, and open a pull request. See [CONTRIBUTING.md](CONTRIBUTING.md) for how the codebase is laid out and how to run the tests.

## Disclaimer

Ripple is an independent, personal open-source project created and maintained by **Yash Bhargav**. It is not affiliated with or endorsed by PostgreSQL, any cloud provider, or any company, and is provided **"as is", without warranty of any kind**, express or implied.

Ripple is designed to run every check inside a database transaction that is always rolled back, against a **scratch or non-production copy of your schema** — it is never meant to run against production, and never commits anything. That said, **you use this software entirely at your own risk**. By downloading, installing, or using Ripple, you agree that **Yash Bhargav and any contributors are not responsible or liable for any damage, data loss, downtime, financial loss, or other harm** arising from its use, misuse, or from any bug it may contain, including if it is ever pointed at a real/production database or credentials.

Before using Ripple on anything that matters:

- Test it first against a throwaway or non-production database.
- Review the commands and configuration before running them.
- Keep independent backups of anything you can't afford to lose.

See [LICENSE](LICENSE) for the full MIT license text.

## License

MIT — see [LICENSE](LICENSE).

## Author

Created and maintained by [Yash Bhargav](https://github.com/yashpb5005).
