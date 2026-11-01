---
name: map-the-risk-in-a-diff
description: List which risky surfaces a pull request touches (sign-in and permissions, money, personal data, deletion and migrations, public interfaces, configuration, concurrency, new dependencies, CI and secrets), and the one check each surface needs before the review can say anything about it.
---
# Map the risk in a diff

When to use: at the start of every review, before reading line by line. The map decides how deep the review goes and who
must look. It also tells you what you could not check.

Steps:
1. Read the changed file names and the description. Mark each file that matches a surface in the table below. A file can
   match more than one.
2. For each marked surface, write the question the change must answer and the evidence that answers it:

   | Surface | Question the change must answer | Evidence to look for |
   |---|---|---|
   | Sign-in, roles, permissions | Can a user reach data or an action they should not? | The check runs on the server, on every path, including the new one |
   | Money, prices, refunds, tax | Is the amount right to the cent, and is each change logged? | Rounding rule, a test with a boundary amount, an audit record |
   | Personal data | Does new data reach a log, an analytics event or a third party? | The fields logged, the retention, the redaction |
   | Deletion, migrations, backfills | Can the change be reversed, and what happens to rows already written? | A reversible migration or a stated rollback; a batch size; a dry run |
   | Public API, webhooks, file formats | Does an existing client still work? | Old and new shapes both handled; a version or a deprecation note |
   | Configuration, feature flags | What is the default in production, and who can change it? | The default value; the flag's owner and removal date |
   | Concurrency, transactions, queues | What happens when two requests or two workers race? | A lock, a unique constraint, an idempotency key |
   | New dependency | Is it maintained, licensed for the product, and needed? | Its size, its last release, and what it can read |
   | CI, deploy, secrets | Can the change expose a secret or widen what a job can do? | Permissions of the workflow, secret use, pinned versions |
3. Put the marked surfaces at the top of the review, with the owner named in `knowledge/standards.md`. A surface with no
   evidence in the diff is a blocking question, not a nit.
4. Mark the surfaces you could not check (for example, a migration that runs on a database you cannot see). Say so in the
   review's "Not checked" line.

Checks before you finish:
- Every risky file is in a surface, or the review says why none applies.
- Each surface has its question and its evidence, or a blocking question.
- Nothing in the map is called safe; it says which checks were read.
- A secret found in the diff is reported to a human at once and never copied into the review.

Example (invented, for illustration): "Change touches `billing/refund.py` (money) and `migrations/0142_refund_status.py`
(migration). Money: the rounding is per refund, tested at 0.005 (the boundary). Migration: adds a nullable column, so the
old code still runs; reversible. Both surfaces have evidence. The change is not in a permissions path."
