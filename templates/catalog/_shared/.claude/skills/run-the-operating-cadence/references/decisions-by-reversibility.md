# Decisions by reversibility

Most decisions can be undone cheaply. Those should be made today, by whoever owns the work. A few cannot. Those deserve
care and one OK. The mistake to avoid is treating every decision like the second kind, so that the company waits.

## Sort the decision

Ask one question: **what does it cost to undo this next month?**

| | Two-way door | One-way door |
|-|--------------|--------------|
| Undo cost | Small and known: a setting, a revert, an apology to nobody | Large, unknown, or impossible |
| Who decides | The owner of the work, bot or person | The person who owns the outcome, by an OK |
| How fast | Now | After the decision page; days, not weeks |
| Record | One line in `memory/decisions.md` | A decision page, the OK, and the line |
| If wrong | Undo it, log what was learned | Costly; so think first |

When unsure, split it. Many big decisions have a small reversible first step: a test on 5% of customers, a one-month
contract instead of a year, a draft instead of a send. Take the reversible step now and bring the evidence to the
one-way decision.

## The default one-way list

Use the list in `knowledge/operating-cadence.md` if the owner has set one. Otherwise:

1. Spending above the budget set for the teammate or the team, and any contract longer than a month or with a
   cancellation cost.
2. Anything leaving the team that cannot be recalled, above the limit set for it: a public post, a mass email, a
   message to a regulator, an investor or the press.
3. A legal commitment: signing, accepting terms, admitting fault, settling.
4. A change to what customers pay, or to what they were promised.
5. Deleting data, or changing records that others rely on with no way back.
6. Removing or archiving a teammate who has live work, and any decision about a person's job or pay.
7. A change to the strategy: its bets, what they rule out, the North Star.
8. Anything touching security or access for the whole team.

Everything else is two-way until shown otherwise.

## Two-way door: decide and log

Add one line to `memory/decisions.md`:

```text
<date> · <decision> · why: <one clause> · undo: <how> · look again: <date or trigger> · task <id>
```

Then act. Tell the teammates it affects on the task, not in a separate message. If the "look again" date arrives and
the number did not move the way the decision expected, undo it and log that too.

## One-way door: the decision page

One page. Publish it with `tico page publish`, then ask for the OK with the page attached.

1. **The question**, as one sentence that can be answered yes, no, or with one of the options.
2. **Why now.** What happens if nobody decides, and by when.
3. **Options.** Two or three real ones, including the cheapest and "do nothing". For each: what it costs, what it
   gains, what it risks, how long it takes, and whether any part of it is reversible.
4. **Evidence.** The numbers and records, each with source and date. Mark what is not known.
5. **Pre-mortem.** "It is six months from now and this went badly. What happened?" Write the three most likely
   causes, and for each, the early sign and what would be done.
6. **Recommendation.** One option, and the one reason that decides it.
7. **Default.** What happens on which date if nobody answers. The default must be the safest reversible choice, never
   the one-way act itself.
8. **What would reopen it.** The fact that, if it changed, means decide again.

Ask:

```text
tico task ask <task id> --to <person> --kind ok "<the decision in one line>" --show page:<n> --version N
```

One open OK per task. Do not act on the one-way part until it is given. If the answer is no, log that as the decision.

## After the decision

- Log it, including decisions not to do something. A "no" that is not written down gets asked again.
- Put the "look again" date on a task.
- Do not reopen a decision because someone disagrees. Reopen it when the fact named under "what would reopen it"
  changes.
- Once a quarter, read the log: which two-way decisions were undone, and which one-way decisions could have been
  split. Adjust the one-way list.

## Disagreement

When two teammates disagree on a two-way door, the owner of the work decides and the other's view is written into the
log line. Then both act on it. Raising it again without new evidence is not allowed; raising it with new evidence is
expected.
