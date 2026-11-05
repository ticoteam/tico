---
name: check-a-feasibility-claim-before-it-is-promised
description: Sort an integration or feature claim into works today, works with a workaround, not possible today, or not shipped, using a dated source, before anyone tells a buyer it works.
---
# Check a feasibility claim before it is promised

When to use: a buyer asks whether the product connects to a system, supports a workflow or will get a feature, and before any note answers yes.

Steps:
1. Write the claim exactly as the buyer asked it, with the system or feature it names.
2. Look for it in the product docs (`hub doc ask`), the public changelog and the status page. A roadmap item, a request on the board or a colleague's remark is not a source.
3. Check the source's date and whether the release it describes is in production. A changelog entry for a release not yet out is "not shipped".
4. Classify the claim: works today (documented and shipped); works with a workaround (name the steps and the limits); not possible today (only a dated source or the product owner says so); not shipped (give a date only if the product owner gave one in the record); unknown (no source answers it: say so, and do not call it impossible).
5. If a trial or sandbox test was run, record the test, its date and who ran it. A test is evidence for that setup only.
6. Write the deal note: the claim, its class, the source and date, and what the buyer may be told.
7. If the buyer needs a date, send the question to the product owner as a task. Do not estimate one.

Checks before you finish:
- Every class except unknown has a source and a date.
- "Not shipped" never appears as "coming soon" or "in the next release" unless a dated product source says so.
- A workaround is written with its steps and limits, not as a plain yes.
- Nothing from another customer's environment is quoted.

Example (invented, for illustration): "Buyer asks for two-way sync with their scheduling system. The changelog entry dated 2026-08-20 covers a one-way import. Two-way sync is not in the docs. Class: works today for one-way import (documented); unknown for two-way (no source covers it, so it is not called impossible). The buyer may be told the one-way import, with the source. Task to the product owner for the two-way question."
