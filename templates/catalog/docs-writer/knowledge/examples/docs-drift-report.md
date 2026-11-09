A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme docs drift report, Wed 2026-09-30

Sample output for Acme, a fictional studio-software team. Every page and pull request is invented.
No docs were changed; drafts are for the docs owner to commit.

**Headline: 3 pages now contradict merged changes; the worst is "Set up reminders", which still describes email only after text reminders shipped (#398).**

## Drifted pages
### 1. Set up reminders (how-to) is wrong. Caused by #398, merged 2026-09-22
- Wrong: "Reminders are sent by email 24 hours before class." Now clients can also choose text.
- Draft fix: `reports/drafts/2026-09-30-set-up-reminders.md` (old text, new text, two new steps, the expected result).
- To verify: whether text reminders are on by default. Question for the docs owner is on the task.

### 2. Reset your booking link (how-to) is missing. Caused by #405, merged 2026-09-23
- No page covers the new Settings option. Draft: `reports/drafts/2026-09-30-reset-booking-link.md`, a how-to of 5 steps.

### 3. Invoice fields (reference) is incomplete. Caused by #412
- Rounding now happens per line item. Draft: a one-line addition under "Total". Source: diff of billing/invoice.py.

## Fine
- 9 other pages checked against the 14 user-visible changes: no drift found.

## Could not read
- The mobile docs site source (no access to that repository). 6 pages not compared.

## Sources
- gh pr list (merged since 2026-09-16), gh pr diff #398, #405, #412, 2026-09-30; knowledge/docs-map.md
```
