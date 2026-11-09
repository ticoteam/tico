# Stock question

Triggered by a task: "Can we run a promotion on the cork blocks next month?", "How much of the travel
mat should we order for the holidays?", "Why did we run out of straps?". Budget 15 minutes. The outcome
is a short answer with the numbers behind it.

---

## 1. Frame it

    hub task show <id>

Which items, which period, and what decision the human is about to make. If a promotion or season is
involved, ask for the expected uplift once; do not invent one.

## 2. Run the numbers

From `knowledge/items.md` and the latest exports, use `skills/work-out-the-last-order-day/SKILL.md`: current stock,
the clean daily sales, the observed lead time, the date stock would reach zero at the normal rate and at the expected
rate, and the latest date an order could be placed to land before that.

## 3. For "why did we run out"

Walk back: when stock crossed the reorder point, whether it was on that week's list, whether the order
was requested and when, when the supplier delivered against the lead time. Name the step that failed,
without blaming a person; propose one change (a higher safety stock, an earlier order day).

## 4. Answer

First line: the answer ("Yes, if you order 300 by 12 October"). Then the numbers and the export dates.
Record anything learned in `knowledge/items.md` or `knowledge/suppliers.md`, and finish the task. Done when the first
line answers the question, every date carries its stock count date, and the task is closed with that answer.
