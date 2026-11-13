# Growth

Growth (`#/growth`, under Company in the sidebar) is a place of cards, like the Dashboard: what each channel spent, how
many customers it brought, how many stayed, what a customer was worth, the funnel, the channels, and the marketing tests.
Anyone may read it; the owner and admins arrange it, and a bot acting as itself does too, pinning only the cards it
made. **Sources**, the systems each number comes from, is linked from the page (the owner and admins find it in
**Settings**). Marketing work is not on this page: marketing ideas are tasks,
and the KPIs the numbers feed are read on
Strategy's Goals tab.

Examples use Acme, a home-cleaning company.

## The page

The head shows the North Star line (when one is set), a **Growth board** link and, when two or more months have numbers, a
month picker. The Growth board link, and each "+N more" on the cards, opens Tasks on the company's Growth Experiments
pipeline (the one the growth packs file ideas into; a channel's keeps its `channel:<slug>` tag); with no such pipeline
there is no link. The owner and admins get **Settings**, which holds the channels, the numbers the maths uses and a **Where
numbers come from** link to Sources; anyone else gets a **Sources** link.

The page is the place `growth` (`backend/cards.py`, the same grid and card forms as the Dashboard). A Growth page nobody
arranged shows these cards, each a template below:

- **Funnel**, large: the five stages (Reach, Acquire, Activate, Retain, Refer), each with its KPI's reading and the share
  of the stage before, and the North Star.
- **Number**, small, six of them: new customers, CAC, spend, LTV:CAC, payback and the share kept at 30 days.
- **Channels**, large: one row per channel, and Unknown.
- **Retention**, medium: the share kept at 30, 90 and 180 days, cohort by cohort.
- **Growth board**, medium: the open marketing tests by stage.

The first change saves them as real pins. A **month** picker reads the cards for one month: the page offers the newest 24
months that have cohort rows, and the newest one is the default. A month with no cohorts yet shows the last month that
has one for the figures that carry back (retention, LTV, LTV:CAC), saying which month they are from.

## Cards

| Template | Shows | Settings |
|---|---|---|
| Number | One metric for the month, its change from the month before, and a sparkline over the year to that month | `metric` (required): new_customers, spend, bot_cost, cac, revenue_month, margin_pct, ltv, ltv_cac, payback_months, kept_d30, kept_d90, kept_d180; `channel`: a channel slug, empty for all |
| Funnel | The five stages' KPIs, each reading (as of the picked month's end), conversion from the stage before, and the North Star | none |
| Channels | Each channel's new customers and CAC for the month, LTV:CAC, six months of new customers, and up to three open tests | `count` (1 to 20, 10 by default) |
| Retention | The newest cohort months' share kept at 30, 90 and 180 days | `months` (3 to 12, 6 by default) |
| Growth board | Open tests in lanes: Live, Working, Measuring, In development, On deck; each lane's count and its newest tasks | `count` per lane (1 to 5, 3 by default) |

Growth cards go on the Growth page only: a Number pinned to the Dashboard is refused. Any other card (a KPI, tasks, pages)
may go on the Growth page as on any place.

Figures follow the rules in [The numbers](#the-numbers). A Number card's change is from the month before the one it shows;
for retention and LTV it is from the newest earlier month that has the figure, as the page draws it. Cohort money is the
company's: anyone who may see the Growth page sees it. A KPI shows only to someone who may read its owner. Marketing tests
show only to someone who may read the task, so a private test is listed for its reader alone.

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

**Who enters them.** The Head of Marketing (the `growth-manager` template) enters each month's rows and the KPI readings
it owns, or that a goal it owns uses, in its weekly numbers routine. The routine ships off like every template routine: the bot's onboarding turns it on. It
runs on Mondays at 07:00, before the growth meeting. Turn it off or on with `tico automation update <id> --disable` or
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

A growth experiment is a task in **Growth Experiments** (Ideas, Building, Running), and a thing that worked and is kept
is a task in **Growth Monitoring** (Watching, Slipping): docs/pipelines-v3.md "Growth and Success". Each channel has an
Owner that files its ideas and owns its tasks in both pipelines from start to finish: SEO, Content, Email, CRO, Search
Ads and Social Ads (the growth channel packs in the same section).

- **Channel.** A `channel:<slug>` label on the task or its sub-task. A channel's marketing work on this page is its open
  tasks with that label, in any pipeline. The department is the task's team.
- **Prediction and result.** The Go-live page in Building gives the prediction as a number; the Results page in Running
  gives the number before and after. The result is a label: `result:failed`, `result:worked-once` or
  `result:watching`, and `result:watching` files a Monitoring task as the experiment's follow-up.
- **Ending.** A measured experiment is Done; one stopped before it is measured is Closed with the reason. A Monitoring
  task that can no longer be fixed is Closed with the reason, and an idea is filed for what to try instead.

The older form, an idea as a General task with a stage label (`growth:ideas` or `idea:on-deck`, `idea:in-development`,
`growth:running` or `idea:live`, `idea:working`, `growth:measuring`, `idea:paused`; backend/marketing_tasks.py), is still
read: a test's start and finish markers on the charts come from those labels. The Growth Manager and the segment
Marketing Owners still file ideas that way.

Bets are company claims and live on the Strategy page. A marketing task does not link to a bet, so this page has no bet
view.

## Who may do what

A channel's stage and budget are its owner's, and the owning bot may change its why, stage and budget. Anyone else
proposes the change. The owner and admins may change any channel, source or setting.

Everyone signed in reads the Growth page. The owner and admins arrange it, and so does a bot acting as itself, which
pins only the cards it made there (a bot pins no pages to it). Its audience is everyone; it has no audience to change.

## Commands

```
tico growth numbers                       the page: each month's rows, Unknown and the totals
tico growth channel list | show SLUG | add | update SLUG | retire SLUG
tico growth source list | add | update ID | retire ID
tico growth cohort list [--channel SLUG] [--month YYYY-MM]
tico growth cohort set --channel SLUG|unknown --month YYYY-MM [--spend N] [--bot-cost N] [--new N]
                 [--d30 N] [--d90 N] [--d180 N] [--revenue N] [--margin N]
                 [--quality actual|estimate] [--source FIELD=TEXT]
tico growth settings [--set KEY=VALUE]
tico kpi log <kpi id> <value> [note] --period-start YYYY-MM-DD --period-end YYYY-MM-DD
tico task create --owner <who> --label idea:on-deck --label channel:<slug> --title "..." --body "..."
tico task list --label channel:<slug>
tico card add growth_number --place growth --metric cac [--channel SLUG]
tico card add growth_channels --place growth --count 10
tico card list --place growth
```

## Related

[Strategy](strategy.md), [Goals and KPIs](goals-and-kpis.md), [Routines](routines.md), [Tasks](tasks.md). The Dashboard
and its cards are in [overview.md](overview.md#dashboard-team-pages-and-cards).
</content>
