---
name: check-a-doc-before-you-cite-it
description: "Before a price, policy, limit, date or process from a doc goes into an answer or the map, check its date, its last change, who stands behind it and whether another source disagrees, then label it current, to confirm or out of date."
---
# Check a doc before you cite it

When to use: before a fact from an internal doc or a linked page goes into an answer, a map entry, the FAQ or a market page, when the fact is a price, a policy, a limit, a date, a process step or a named owner. A definition that does not change needs no check.

Steps:
1. Read the whole doc this run (`hub doc read <id or path>`). Note its `version`, its `updated` date and `updated_by_name`. A fact you saw only in a search excerpt is not checked yet.
2. If the doc changed in the last 30 days, read `hub doc history <id or path>`. Cite the newest version and give its date.
3. Name who stands behind it: the person who last changed it (`updated_by_name`) and any owner the doc's own text names. If neither exists, the age clause says "no owner named".
4. Age: a doc not updated in a year that states a price, a policy or a process is named by its date in the answer. A linked page is dated by the date its page shows, or by its walk date in `_librarian/where-things-live.md` when it shows none. Say which one you used.
5. Contradiction: search once for the exact figure or name (`hub doc search "<figure or name>"`). If another doc or page gives a different value, follow `skills/report-a-doc-conflict/SKILL.md` before you answer.
6. Label the claim:
   - **current**: read this run, dated within the year or changed by its owner in that year, and no source disagrees. It goes in the answer as a plain sentence with its citation.
   - **to confirm**: old, undated, or with no named owner. It goes in with its date in the same sentence: "as of the refund policy, updated 2025-03-04; not confirmed since."
   - **out of date**: the doc says it is replaced, retired or superseded, or a newer doc says so. Do not state it as the answer. Name the doc that replaces it, cite the line that says so, and list the old doc under "Out of date" in `_librarian/missing.md`.

Checks before you finish:
- Every price, policy, limit or date in the answer has its date beside its citation.
- No claim labelled out of date is the main sentence of the answer.
- A value taken from a search excerpt was read in full first.
- Each "to confirm" claim says its date in the same sentence.

Example (invented, for illustration): A person asks how long a refund takes. The refund policy, updated 2025-03-04 by a finance lead, says 14 days. A search for "refund 14 days" finds no other value. The policy states a rule and is more than a year old, so the claim is "to confirm". The answer: "Refunds take 14 days, as of the refund policy, updated 2025-03-04; not confirmed since. [Internal doc · Refund policy](doc:ab12)."
