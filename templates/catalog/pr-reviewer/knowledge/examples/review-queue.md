A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme review queue, Tue 2026-09-29

Sample output for Acme, a fictional studio-software team. Every pull request and name is invented.
Nothing has been posted to GitHub.

**Headline: 6 open pull requests; 1 has a blocking issue (#412 changes invoice rounding with no test), 1 is too large to review well.**

## Draft reviews (top 3 of 6)
### #412 Fix invoice rounding (billing, risky path: reviewer Priya Nair), open 4 working days
Checks: 1 failing (unit tests, see log). 118 changed lines, 3 files.

> **Summary:** rounds each line item, not the invoice total. Blocking: no test covers the change.
> - issue (blocking): `billing/invoice.py:88` rounds each line, so three lines of 3.333 total 9.99 instead of 10.00.
>   Add a test for three lines of 3.333 before merge.
> - question: is the previous behaviour relied on by the export in `billing/export.py:41`? I could not tell from the diff.
> - nit: `amt` could be `amount` (line 92).
> - praise: the migration note in the description is exactly what a reviewer needs.
> - Not checked: I did not run the tests; the failing check was not read past the first error.

### #418 Refund webhook (billing, risky path), open 3 days
> **Summary:** adds a webhook handler. No blocking issue found in what I read.
> - question: what happens on a duplicate delivery of the same event? (`webhooks/refund.py:30`)
> - suggestion: verify the signature before parsing the body (`webhooks/refund.py:12`).
> - Not checked: the retry behaviour of the payment provider.

### #421 Plan upgrade copy, open 2 days
> **Summary:** text change only. Looks fine; checks failing on a lint rule unrelated to it (`lint: line length`).

## Too large to review well
- **#409 Rework reminders** (1,240 lines, 31 files, mixes a refactor and a feature). Draft to the author:
  "Thanks for this. Could you split the refactor from the new reminder rule? Then each is a quick review."

## Small and ready
- #415 fix typo in settings label (2 lines, green).

## Could not read
- #402 diff was truncated by GitHub; not reviewed.

## Sources
- gh pr list, gh pr diff, gh pr checks, 2026-09-29; knowledge/standards.md
```
