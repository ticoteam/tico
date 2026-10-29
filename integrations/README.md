# Integrations

One page per outside system, written for a bot about to touch it and for a person deciding
whether it may. Each page says what the system is, what data it holds, the exact commands a bot
runs, the rules (read/write gates, approvals, what never to do), useful recipes and the gotchas
people have already hit. The server serves them (`GET /api/v2/tools`, the
**Integrations** page) and every bot reads them with `hub tool show <service>`.

This folder ships only the outside services Tico has built-in support for: GitHub, Slack, Mail
(Gmail and Google Calendar), the Aside browser, a bot's own Chrome, Close (the call importer), and the databases
`hub db` reads (PostgreSQL, MySQL, MongoDB, SQLite). Every other service a company uses gets a
page in its own config ("Your company's own pages", below). Tico's own features (the hub
database, file storage, Credentials, decisions) are documented in `docs/`, not here.

Nothing secret goes in here: the names of environment variables and credentials are fine,
their values never.

## The page

Every page starts with YAML frontmatter, then six sections in this order, then `## Learnings`.

```yaml
---
service: posthog            # the file name, lower-case, matches `service:` in bot.yaml
title: PostHog
kind: api                   # api | sql | browser | mail | cli  (sql = any database read through `hub db`)
summary: One line for the list.
access: How a bot reaches it (the connector, CLI or API), one line.
credentials:                # env var names and where the value lives; never values
  - POSTHOG_API_KEY — a read-only key, stored in Tools → Credentials and granted to this bot
declared_as: |              # the `tools:` entry a bot carries in bot.yaml
  - service: posthog
    can: [read]
    env: POSTHOG_API_KEY
writes: never               # never | approval | allowed
owner: owner              # who decides changes to this integration
aliases: [ph]               # optional: other names `hub tool show <name>` resolves
---
```

`writes` means: `never` — no bot writes, there is no path for it; `approval` — a write needs a
Tico approval (`policies/approvals.md`) or an explicit per-bot grant from the owner named on the
page; `allowed` — a bot whose `tools:` carries the verb may write within the page's rules.

Sections: **What it is** · **What data it has** · **How a bot uses it** (exact commands) ·
**Rules** · **Recipes** · **Gotchas**. Plain sentences, present tense, no history (that goes in
the decision log or the learnings).

## Your company's own pages

This folder is what ships with a release. Your own integrations (a CRM, analytics, billing, a
company database, an internal tool) and their query catalogs belong in your private config, in
`<TICO_REGISTRY_DIR>/integrations/` (or the folder `TICO_INTEGRATIONS_DIR` names): the same page
format, layered over these, a page with the same name replacing ours. Restart the server after a
change; [docs/databases.md](../docs/databases.md), "A private company config", has the deploy steps. `postgres.md`, `mysql.md` and `sqlite.md` here document `hub db`, which
reads a company database read-only; [docs/databases.md](../docs/databases.md) sets it up.

## Queries

`queries/<service>.yaml` is the catalog of useful queries for a `kind: sql` integration: a list
of `{id, title, description, category, tags, database, sql, params}`. None ship; a company adds
its own next to its pages. A MongoDB entry has `mongo:` (op, collection, filter or pipeline, with
`{"$param": name}` for values) in place of `sql:`; see `templates/company-config/integrations/queries/atlas.yaml`. `hub tool query-search <service> [term]` searches them; `hub tool query-search <service> --id
<id>` prints one.

## Learnings

Learnings are the log; the page is the rules (`policies/writing.md`, "Rules and the log"). A bot
or a person who learns something reusable about an integration adds it with `hub tool learn <service>
"<text>"` (or the box on the Integrations page); it is stored in the hub database, shown under
the page, and folded into the page by a person over time. The page wins when they disagree.
