---
service: atlas
title: Acme Atlas database
kind: sql
summary: Acme's product MongoDB Atlas cluster (accounts, events, sessions), read through `tico db atlas`.
access: "`tico db atlas find|aggregate|count|distinct|collections ...` or `tico db atlas --query <id>`; read-only, audited."
credentials:
  - DB_ATLAS_URL — mongodb+srv connection string of the Atlas user with the `read` role on database `app`, stored in Tools > Credentials and granted to the bot
declared_as: |
  - service: mongodb
    identity: Atlas database user with the read role on app
    database: atlas
    can: [read]
    env: DB_ATLAS_URL
    read_preference: secondaryPreferred   # optional; this is the default
writes: never
owner: ana
aliases: [acme-mongo]
---

## What it is

Acme's product database on MongoDB Atlas (M10, replica set): database `app`, read through a
database user that only holds the built-in `read` role. The generic rules are in
`tico tool show mongodb`.

## What data it has

| Collection | Fields worth knowing | Notes |
|---|---|---|
| `accounts` | `_id, plan, region, created_at, email` | `email` is personal data: count and group, never list |
| `events` | `_id, account_id, type, at` | large; always filter on `at` |

`tico db atlas collections` lists them with a sample of their field names.

## How a bot uses it

```bash
tico db atlas collections
tico db atlas count accounts '{"plan": "team"}'
tico db atlas --query signups-since --param since=2026-09-01
tico tool query-search atlas signups
```

## Rules

- Read-only, always. No `$out`, `$merge` or JavaScript operators; never ask for a writer URL.
- Filter events by `at` and cap with `--limit`.
- Counts, plans and regions in reports, never the accounts' emails.

## Recipes

- Signups per region since a date: the `signups-by-region` named query.

## Gotchas

- Dates are UTC; pass `--param since=2026-09-01` and the tool sends a real date, not a string.
- A named query's values are literals: a value cannot add operators to the filter.

## Learnings

What bots and humans learn about this tool is added with `tico tool learn atlas "..."` and
shown under this page; a human folds it into the page over time. The page is the rule.
