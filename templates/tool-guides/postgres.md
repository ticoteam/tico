---
name: PostgreSQL
category: data
summary: "Query a Postgres database with psql"
commands: [psql]
---
## What it is for
Exact answers from a Postgres database: counts, sums, lists and checks, instead of estimating.

## How to use it
`psql` reads the connection from `DATABASE_URL` (or `PGHOST`, `PGUSER` and the rest) as Tools gives them.

    psql "$DATABASE_URL" -c "select count(*) from orders where created_at > now() - interval '7 days'"
    psql "$DATABASE_URL" --csv -f report.sql > report.csv

## Good to know
- Read with a read-only role where there is one; write only what the task asks.
- Put a query you run twice in a `.sql` file in a repository.
