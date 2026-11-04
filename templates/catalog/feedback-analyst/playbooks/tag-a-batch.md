# Tag a batch of feedback

Triggered by a task that attaches feedback (an export, survey responses, call notes), and used in
`playbooks/weekly-feedback-report.md`. Budget 20 minutes for a hundred items. The outcome is a tagged log
and the counts.

Inputs: the batch, `knowledge/themes.md`, `knowledge/segments.md`.
Done when: every item is in the log with one theme and one severity, the cold re-tag check is recorded, and the counts
are in `knowledge/trends.md`.

---

## 1. Read the batch and the theme list

Read `knowledge/themes.md` first. Read the whole batch before tagging, so a new theme is noticed once and named
consistently.

## 2. Tag each item

For each item, follow `skills/tag-one-feedback-item/SKILL.md`: one primary theme, a severity (blocked, annoyed,
suggestion), a sentiment, a segment from `knowledge/segments.md` (or "unknown"), the source and the date. Praise and
empty items are logged as "no issue", so the total still adds up. Remove names, emails, account ids and any
credential.

## 3. Handle what does not fit

- Three or more items on one new subject: add a theme with a definition and an example, dated today.
- Fewer: tag them "unclassified" and keep the count.
- Anything on the exclusion list, or that names safety, security or a legal threat: do not cluster it. Make a task for
  the person named in `knowledge/segments.md` the same day, and leave a count only.

## 4. Check yourself

Re-tag ten items chosen at random, cold, without looking at the first tags. If two or more differ, tighten the
definition in `knowledge/themes.md` before counting, and say which definition changed. Record the ten and the
result in the log's header.

## 5. Count

Add the counts to `knowledge/trends.md` and the item log to `knowledge/log/`. Put the totals on the task: items read,
by source and by theme.
