# Growth

Growth (`#/growth`, under Strategy in the sidebar) is one page of numbers: what each channel spent, how many customers
it brought, how many stayed, and what a customer was worth. It has no tabs. **Sources**, the systems each number comes
from, sits behind a link on the page. Marketing work is not on this page: marketing ideas are tasks, and the KPIs the
numbers feed are read on Strategy's Goals tab.

Examples use Acme, a home-cleaning company.

## The page

The page reads top to bottom in this order. Each section is a few words; a section with no data is hidden.

- **North Star**: the one KPI the company is judged on.
- **Growth**: new customers.
- **Cost**: spend and bot cost.
- **Retention**: the share kept at 30, 90 and 180 days.
- **Value**: revenue, profit, LTV, LTV:CAC and payback.
- **Channels**: one row per channel, and Unknown.
- **Tried**: the marketing tasks that started or finished. A task's mark (Testing, Won, Lost and so on) shows when it
  moved, not what caused the numbers to move.

## The numbers

Each month has one row per channel and one **Unknown** row. A row holds:

- spend and bot cost, in dollars;
- new customers;
- the share still customers at 30, 90 and 180 days, blank until the cohort is that old;
- revenue per customer per month and margin (`--revenue`, `--margin`, with the margin as a percent);
- a quality mark: `actual` when every input came from a source, `estimate` when one did not.

Tico computes the channel numbers from these inputs, so nobody types a computed number:

- **CAC** = (spend + bot cost) ÷ new customers. Blank with no new customers.
- **Profit per month** = revenue per month × margin ÷ 100.
- **Expected months** is the area under the retention curve through the share kept at 30, 90 and 180 days, with the
  last monthly decay carried on, capped at 60 months.
- **LTV** = profit per month × expected months.
- **LTV:CAC** = LTV ÷ CAC.
- **Payback** = CAC ÷ profit per month, in months.

**Who enters them.** The Head of Marketing (the `marketing-lead` template) enters each month's rows and the KPI readings
it owns, or that a goal it owns uses, in its weekly numbers routine. The routine ships off like every template routine: the bot's onboarding turns it on. It
runs on Mondays at 07:00, before the growth meeting. Turn it off or on with `hub automation update <id> --disable` or
`--enable`.

**Unknown.** A customer with no source, or from a source that is not one of the team's channels, goes to Unknown. Nobody
decides which channel a customer came from. Unknown has no CAC, LTV or payback.

**Blank is not zero.** A channel with no inputs yet is blank. A retention share the cohort is too young for is left out,
not written as 0.

**KPI readings.** Each measurement a KPI tracks is a reading, logged with the period it covers. A correction supersedes
the earlier reading. The KPI views (Tree, Loops, Journey) are on Strategy's Goals tab; see
[Goals and KPIs](goals-and-kpis.md).

## Sources

**Sources** lists the systems that hold each number (spend, customers, retention) and which number each one is the truth
for. The numbers are entered from them each week; the rows stay in those systems.

## Marketing tasks

Marketing ideas are tasks of type **Marketing**. They live on the Tasks page, on its Marketing board.

- **Steps.** Idea, then Testing, then Scaling. Paused sits to one side. A task finishes on Won, Lost, Inconclusive or
  Dropped.
- **Channel.** A `channel:<slug>` label on the task or its subtask. The department is the task's team.
- **Closing.** When a test ends, the task moves to its outcome, with a comment saying what was learned and the numbers
  behind it. A follow-up is a new Marketing task, linked to the one it follows.
- **What a task needs.** A title, a channel and a body. There is no prediction, hypothesis, score or bet.

Existing idea tasks stay as tasks, with their history.

Bets are company claims and live on the Strategy page. A marketing task does not link to a bet, so this page has no bet
view.

## Who may do what

A channel's stage and budget are its owner's, and the owning bot may change its why, stage and budget. Anyone else
proposes the change. The owner and admins may change any channel, source or setting.

## Commands

```
hub growth numbers                       the page: each month's rows, Unknown and the totals
hub growth channel list | show SLUG | add | update SLUG | retire SLUG
hub growth source list | add | update ID | retire ID
hub growth cohort list [--channel SLUG] [--month YYYY-MM]
hub growth cohort set --channel SLUG|unknown --month YYYY-MM [--spend N] [--bot-cost N] [--new N]
                 [--d30 N] [--d90 N] [--d180 N] [--revenue N] [--margin N]
                 [--quality actual|estimate] [--source FIELD=TEXT]
hub growth settings [--set KEY=VALUE]
hub kpi log <kpi id> <value> [note] --period-start YYYY-MM-DD --period-end YYYY-MM-DD
hub task create --owner <who> --type idea --label channel:<slug> --title "..." --body "..."
hub task list --type idea
```

## Related

[Strategy](strategy.md), [Goals and KPIs](goals-and-kpis.md), [Routines](routines.md), [Tasks](tasks.md).
</content>
