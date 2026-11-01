# Monthly brand consistency audit

Schedule: the 1st of each month at 10:00 team time (routine `monthly-brand-audit`), after setup. Also run by hand. Budget 45 minutes. The outcome is one page: how
consistent last month's public work was, and the three fixes that matter most. Nothing is edited.

---

## 1. Read where things stand

    hub task show <id>

Then the brand card (`hub brand show`), `knowledge/rulings.md` and last month's audit. Check whether last month's
three fixes were made.

## 2. Take the sample

Twelve to fifteen items published last month, spread across the surfaces in the brand card:
the pages that changed, emails sent (from the Email Marketing Manager's reports), posts, app store or
marketplace listings, and any sales deck a human attached. Weight toward what most people see.

## 3. Score each item

Against four checks, each pass, minor or miss: **voice** (the traits), **words** (the word list, the
product and plan names spelled right), **claims** (matches the product and prices today), **visual**
(logo, colours and type per the visual rules, when you can see them). Quote the line for every miss.

## 4. Choose three fixes

Rank misses by audience size, then by harm (a wrong price outranks a tone slip). For each: the item,
the line as it is and as it should be, the rule, and the owner of the item.

## 5. Find gaps in the book

A question the audit could not answer from the brand card (how to write a price change, whether
to use emoji in posts) becomes a proposed rule with an example, for the owner.

## 6. Write and hand over

Write `reports/YYYY-MM-brand-audit.md` in the shape of `knowledge/examples/brand-audit.md`, then
`hub file publish reports/YYYY-MM-brand-audit.md`. Commit, and `hub task update <id> --status done
--note`. Create requested fix tasks for the owners with your Tools.
