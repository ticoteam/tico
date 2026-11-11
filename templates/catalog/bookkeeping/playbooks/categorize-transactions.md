# Categorise transactions

Triggered by a task that attaches an export and asks for categories, and used by step 4 of
`playbooks/monthly-close-checklist.md`. Budget 20 minutes for a month of a small team. The outcome
is a proposed category for every uncategorised row and a short list of questions. Nothing is posted.

---

## 1. Read the export and the rules

    tico task show <id>

Read the export, `knowledge/categories.md` and `knowledge/vendors.md`. Note the date range and row
count. Work only from rows that have no category, or that the task names.

## 2. Propose, by confidence

For each row, in this order:
1. **Same vendor, same category as before** in `knowledge/vendors.md` or the export's own history:
   propose it, "high", and cite the earlier row.
2. **A rule in `knowledge/categories.md` applies** (for example "all cloud hosting is Software"):
   propose it, "high", and name the rule.
3. **The line text says what it is** (a known vendor, a clear description) but there is no history:
   propose it, "medium", and say what in the text you relied on.
4. **Anything else** is a question, not a guess: transfers with no memo, round amounts to a human,
   owner spending, refunds, loans, first-time vendors above the ask-first amount, and anything on the
   ask-first list from setup.

## 3. Check for what looks wrong

Flag, with the two rows: a probable duplicate (same date, amount and vendor), a charge that is much
larger than the vendor's usual, a gap of more than two days in the feed, a credit that has no matching
charge. Say "looks like", never "is".

## 4. Write it down

A table of date, line, amount, proposed category, confidence, reason. Amounts are copied from the export
row, never recomputed by hand. Add confirmed patterns to `knowledge/vendors.md` when evidence or the requester establishes them; a proposal you made is not a fact.

## 5. Finish

Attach the table to the task. `tico task ask <id> --to <person> "<question>" --version N` once with the numbered questions. Commit, then
`tico task done <id> -m "..." --version N`: rows worked, rows proposed, rows asked about, and any
export you could not read. When the file would not open, say so; an unread export is not an empty one.
