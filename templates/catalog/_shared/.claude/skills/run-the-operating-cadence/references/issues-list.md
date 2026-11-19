# The issues list

One list of everything in the way. It exists so problems are raised early, kept in one place, and closed.

## Where it lives

Every issue is a Tico task with the label `issue`. Tasks already carry an owner, a history and a place to ask for an
OK, so the list needs no separate file.

- Create: `tico task create --owner <teammate> --title "Issue: <the problem in one sentence>"`, then
  `tico task label <id> --add issue`.
- Read the list: `tico task list --label issue`.
- The owner is the teammate who can solve it, not the one who noticed it.

## What counts as an issue

- A scorecard number red or gray two weeks running.
- A commitment missed twice.
- A goal off pace, or with no check-in.
- A task or an ask stalled past the limit in the settings.
- Two teammates who both think they own something, or a thing nobody owns.
- A customer problem seen more than once.
- A risk with a date: a renewal, a deadline, a dependency.
- An idea that needs a yes or no.

Not an issue: a task that is simply not done yet and is on track; a status update; a complaint with no decision in it.

## Write it so it can be solved

The title is the problem in one sentence, stated as what is wrong, not as a fix.

- Weak: "Checkout". Weak: "We should redo checkout".
- Good: "Issue: trial-to-paid fell from 9% to 6% after the 24 Feb checkout change".

In the task body, three lines: the evidence with links, what it costs each week it stays open, and which goal it
blocks.

## Ranking

Each week, sort the open issues by harm to a goal and take the top three. Use this order to break ties:

1. Anything hurting a customer or losing money now.
2. Anything blocking the North Star or a quarterly goal.
3. Anything with a deadline inside two weeks.
4. Anything open for three reviews or more.
5. The rest, oldest first.

Three is a rule, not a target. Solving three properly beats touching ten.

## Working one issue

1. **Say the real problem.** Restate it in one sentence with the evidence. Often the listed issue is a symptom. Ask
   "why" until the answer is something a teammate can change. Stop when the next "why" would be a guess.
2. **Get the facts you lack.** Read the records. If a fact needs a person, ask one question on the task. If the answer
   will take days, give the fact-finding itself an owner and a date and move to the next issue.
3. **Lay out the options.** Two or three, including "do nothing", each with what it costs and what it risks.
4. **Decide by reversibility.** See `decisions-by-reversibility.md`. A two-way door is decided now by the owner of the
   work. A one-way door gets a decision page and an OK.
5. **End it one of three ways.**

   | Ending | What is recorded | What happens to the issue task |
   |--------|------------------|--------------------------------|
   | Decided | The decision, why, and how to undo it, in `memory/decisions.md` and as a comment | Closed |
   | To do | One linked task with one owner and one date | Stays open until the to-do is done and the number confirms it |
   | Dropped | The reason | Closed |

6. **Confirm the fix.** An issue raised from a number closes when the number is back inside tolerance, not when the
   to-do is ticked.

## Keeping the list short

- **Search before adding.** Add evidence to an existing issue rather than filing a twin.
- **Three reviews.** An issue open at its third weekly review moves to the top of the ranking with the note "third
  review". If it still cannot be moved, it goes to the person above as one ask with a recommended answer and a default
  date.
- **Count it.** Show the number of open issues on each weekly page beside last week's. If it rose, say why.
- **Long-range items.** An issue that truly cannot be worked this quarter is closed with "parked until <date>" and a
  snooze on the task for that date, so the weekly list holds only what can move.
