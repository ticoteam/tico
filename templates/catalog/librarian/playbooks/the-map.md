# The map

The map is a set of internal docs under `_librarian/`, kept in {{app_name}} so people can read and correct
them and so a fresh session starts from what was learned, not from nothing. It is what makes the
hundredth question cheaper than the first. You are the only one who writes it; anyone may read it. It is
a cache: when it disagrees with a doc, the doc wins and the map is fixed.

Use team wording in titles: "Team glossary", "Docs index" and "Where things live". Keep `_librarian/` paths unchanged.
An empty glossary says "No team terms are defined yet"; do not invent terms to fill it. Use `manual:glossary` for Tico's
product words and its "Instead of" mappings.

Write each with `hub doc write <path> --title "<title>" --body-file <file> --note "<what changed>"`.
That creates or updates; a version conflict is re-read and retried once. Read one with `hub doc read`.

## `_librarian/index.md`: the table of contents and a summary of every doc

One line per internal doc, grouped by folder, in path order:

    ## sales/
    - `sales/pricing.md` (v4, 2026-09-12): Plans, prices and discount limits. Read for any "how much" question.
    - `sales/objections.md` (v2, 2026-08-30): The five objections and the approved reply to each.

- The summary says **what the doc answers**, not what it is called, in one sentence, from its text: the topics it answers, not its figures.
- Include the version and the updated date from `hub doc list`, so a refresh can tell which docs
  changed since it last read them: a line whose version matches the list needs no reread.
- Skip `_librarian/` itself and `FAQ.md`. The server removes an archived source
  from the index immediately; verify availability before following any older cached entry.
- At the top, record `Last refreshed: <UTC date and time>`.
- Add a `## Linked docs` section listing each linked doc: title, address, and its one-line description.

## `_librarian/glossary.md`: the team's words

A term, one line, and the doc that defines it. Terms the team uses that an outsider would not know
(product and plan names, internal names, acronyms, people's roles) and the everyday words people use for
them ("the CRM", "the portal"). Alphabetical.

    - **Net 30**: payment due 30 days after the invoice. [Internal doc · Billing](doc:<id>)
    - **Concierge plan**: the top tier; also called "enterprise" by sales. [Internal doc · Pricing](doc:<id>)

Only what a doc says. If a term is used but never defined, list it under `## Used but not defined` (that
is also a gap: add it to `missing.md`).

## `_librarian/where-things-live.md`: topic to place

Two parts.

**Topics.** A short list, one line each: the topic, then where to look first, in order.

    - Refunds and credits: `finance/refunds.md`, then the help site's "Billing" section.
    - How to deploy: the GitHub repository's `docs/deploy.md`.

**Linked sources.** One block per linked doc, from walking it (`refresh-the-map.md`). Its structure, so a
question can go straight to the right page:

    ### Help centre: https://help.example.com   (walked 2026-09-28)
    - Sections: Getting started, Billing, Tools, API, Troubleshooting.
    - Has a sitemap: yes (240 pages). Most useful for: how-to steps, plan limits, error messages.
    - Not covered there: pricing (see `sales/pricing.md`), internal process.
    - Known: the page "/billing/refunds" is the one to read for refunds. Old /faq is out of date.

Say plainly what a source is not for. Say when it was last walked and what could not be read.
Every unreadable source also belongs in `missing.md` under **Sources I could not read**, in the same pass.

## `_librarian/missing.md` and `_librarian/faq-log.md`

Their shape is in `faq-and-gaps.md`.

## Keeping it small

The map exists to save reading. If `index.md` grows past a few hundred lines, keep one line per folder
plus a line per doc that is often needed, and say so at the top. Never copy a doc's text into the map.
