# Monthly IP watch

Schedule: the 1st of each month at 09:00 team time (routine `monthly-ip-watch`), after setup. Also run by hand. Budget 40 minutes. The outcome is one page: deadlines, expiring domains,
look-alikes worth a lawyer's look, and missing assignments. A summary for a human, not legal advice. Nothing is
filed, renewed or sent.

---

## 1. Deadlines

For each mark in `knowledge/register.md`, compute the next deadline and its window from the registration date
and the office's rule (cite the office's page and the date read), and the end of any grace period. For each
domain: expiry, auto-renew on or off, and whether the account holder named at setup is still at the
team (`hub team show`). Re-check each mark's status on the office's public record every six months.

## 2. Look-alikes

For each term in `knowledge/watch-terms.md`, search the public trademark database for filings since the last
watch, in the team's classes and the ones next to them, for identical, similar-sounding, similar-looking and
same-meaning words. Then a public web search for the term with the product words, for uses by others. Keep a hit
when both the mark and the goods are close; count the rest. For each kept hit: mark, owner, office, number,
classes, status, filing date, the opposition window if it is published, and one line on why it may matter.

## 3. Assignments

From `knowledge/assignments.md` and `hub team show`: anyone new who created code, designs or content since last month,
and anyone still "does not" or "not seen".

## 4. Write and hand over

`reports/YYYY-MM-DD-ip-watch.md` in the shape of `knowledge/examples/ip-watch.md`: the headline, deadlines inside
12 months, domains inside 90 days, look-alikes, assignments, what you could not search, then the not-legal-advice
line. `hub file publish` it, commit, and `hub task update <id> --status done --note` with the nearest deadline first.
A deadline inside 60 days becomes a task for the decision-maker, with `hub task create --owner <person>`.

## Done when
- Every mark and domain in `knowledge/register.md` has its next deadline, window and rule, cited to the office page with the date read.
- Every look-alike kept has its mark, owner, number, status and one line on why it may matter, and the rest are counted.
- Every person in `knowledge/assignments.md` has "assigns", "does not" or "not seen".
- The watch is published, and the task is closed with the nearest deadline first.
