# Variance commentary and forecast accuracy

This is the note that goes with budget against actual, and with forecast against actual. `build-the-pnl` owns the
arithmetic that splits a difference into drivers (volume, rate, mix, price). This file owns what is said about it:
what it means for the rest of the year and what should be done.

## 1. Which lines get a note

Read `tico finance report budget_vs_actual` for the closed month. State at the top: the basis (cash or accrual), the
as-of time, the limit in use, and whether any budget changed during the month.

A line gets a note when it passes **both** tests of the limit in `knowledge/pnl-design.md` (default: 10% of the line
and 1% of the month's revenue). A line that passes one test is listed without a note. If more than five lines
qualify, write up the five largest by money and list the rest.

Other comparisons use their own percentage with the same money floor. Defaults:

| Comparison | Percent test | What it tells you |
|---|---|---|
| Actual against budget | 10% | Are we on the plan the owner agreed |
| Actual against last forecast | 5% | How good the forecast was a month ago |
| Actual against prior month | 15% | What changed in the business |
| Weekly cash line, forecast against actual | 10% | Whether the cash method works |

A favourable difference gets the same attention as an unfavourable one. Revenue over forecast or cost under budget
can be a timing slip that will reverse, or work that did not happen.

## 2. The four parts

Each note is four short sentences. Write them in this order.

1. **Headline.** The line, actual, budget, the difference in money and percent, and whether it helps or hurts
   profit. "AI services 10,950.00 against 9,000.00: 1,950.00 over (22%)."
2. **Driver.** The cause, found in a source, with the split from the P&L. "A model change on 6 October raised cost
   per task from 0.41 to 0.48 (rate, 1,650.00); tasks rose 2% (volume, 300.00)." The parts add up to the difference.
3. **Outlook.** What this does to the rest of the year, as a number. "At this rate the line runs 11,000.00 a month:
   4,000.00 more over budget by December." Say whether the rolling forecast already includes it.
4. **Action.** Who does what by when, or the decision asked for, or "none: timing, reverses in November (bill 512)".

Keep the whole note to about 120 words. A reader should be able to stop after any sentence and still have the truth.

## 3. Labels

Give each difference one label.

| Label | Use it when | Outlook |
|---|---|---|
| Timing | You found the item that reverses it: an accrual, a bill dated next month, a renewal that moved | Reverses in the named month; no change to the year |
| One-off | It happened once and will not repeat: a set-up fee, a refund, a legal bill | The year moves by this amount once |
| Run-rate | The level has changed and will stay: a price change, a hire, a new vendor, a unit cost | The year moves by the monthly amount x months left |
| Budget error | The budget was wrong from the start: a line left out, a wrong date | Fix the budget with the owner; say so once, not monthly |
| Unexplained | No cause found in a source yet | Open, with an owner and a date |

"Timing" is the label most often used to avoid an answer. It needs the reversing item's id.

## 4. What a cause is

- A cause is something found: a vendor, a bill, an entry, a contract, a usage report, a dated change.
- A cause is not something likely. "Probably seasonal" goes under unexplained until last year's figures show it.
- Follow the money to its driver: from the line to the vendor (`tico finance report spend_by --by vendor`), to the
  bill or entry (`tico finance search --kind bill --q "<vendor>"`), to the decision behind it (the task).
- For AI services, read use by bot (`tico api GET "usage?from=<day>&to=<day>&group=bot"`): which bot, which model,
  how many tasks.
- When two causes interact, do not argue about the split. Use the method in `build-the-pnl` and move on.
- If cause and action belong to a teammate or bot, file them a task with the numbers: `tico task file --parent <id>
  --owner <name> "<title>"`.

## 5. Tone

- Numbers first. No adjectives for size: write "22%", not "significant".
- No blame and no defence. "Travel was 2,400.00 under: two trips moved to November" is enough.
- Do not explain a difference by restating it. "Software was over budget because software costs were higher" says
  nothing.
- If the news is bad, it goes in the first line of the page.

## 6. Forecast accuracy

Score the forecast every period. The point is to learn the size and direction of its errors.

**Per line, per period**

- Error = actual - forecast. Percent error = error / forecast.
- Keep the sign. Whether the forecast was too high or too low matters more than the size.

**Over the last six periods**

- **Typical size**: the average of the absolute percent errors.
- **Lean**: the average of the signed percent errors. Near zero means errors cancel. Clearly positive or negative
  means the method leans one way.
- **Streak**: the same sign four periods running is a method problem even if each miss was small.

**What to do with it**

| Finding | Fix |
|---|---|
| Collections always late against forecast | Use each customer's real payment history, not terms; lengthen the lag |
| Payables always under | A missing line: card payments, annual renewals, usage bills |
| One line swings both ways | It is lumpy: forecast it by named items, not an average |
| Revenue forecast always above actual | The growth assumption is a hope: use the trailing average |
| Costs always under budget | Padding, or work not happening: ask which |
| Errors shrank after a change | Keep the change; note it in `knowledge/cash-forecast.md` |

Show accuracy on the page as a small table: line, last error, typical size, lean. Use the typical size to set the
low and high cases (`thirteen-week-cash.md`, section 4). A forecast that reports its own record is trusted more than
one that does not.

## 7. Worked note (invented)

Basis: accrual, as of 2026-11-04 09:00. Limit: 10% and 708.00 (1% of October revenue). No budget changed in October.

- **AI services** 10,950.00 against 9,000.00: 1,950.00 over (22%). A model change on 6 October raised cost per task
  (rate, 1,650.00); tasks rose 2% (volume, 300.00). Run-rate: 11,000.00 a month from here, 4,000.00 more over by
  December; in the forecast from this month. Decision asked of the owner: raise the budget from December or move two
  bots to the cheaper model (saves about 1,400.00 a month; quality test attached).
- **Travel** 1,100.00 against 3,500.00: 2,400.00 under (69%). Timing: two trips moved to November (tasks linked).
  Reverses in November; no change to the year.
- **Unexplained**: Office supplies 2,050.00 over (61%); no bill or entry found yet. With the Controller, due
  2026-11-08.
