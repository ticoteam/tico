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
- the share still customers at 30, 90 and 180 days, blank until the cohort is that old (or computed from customers:
  see "Computed from customers" below);
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

### Computed from customers

A team that knows its customers can load them instead of typing retention. Growth then computes new customers,
retention, LTV, CAC and payback itself, cut by channel, segment or plan, monthly or weekly, and each figure opens to the
customers behind it.

**Customer facts.** One row per customer, as a CSV: **Upload customers** in Growth's Settings or on the Retention card,
or `tico growth customers load customers.csv`. Growth keeps only these columns:

| Column | Required | What it holds |
| --- | --- | --- |
| customer key | yes | your own id for the customer, never a name, an email or a phone number |
| signed up | no | a date |
| became customer | yes | the date the customer first paid |
| channel | yes | a Growth channel slug; anything else counts as Unknown |
| segment | no | a short label, such as `agency` |
| plan | no | a short label, such as `pro` |
| churned | no | the date the customer stopped paying; blank while they pay |
| revenue per month | no | for this customer; blank uses the channel's month or the settings value |

Each load replaces the last one: a customer left out is deleted, which is how you remove someone who asks to be
forgotten. A customer who left and came back is a second row with the same key and a new became customer date, after
the first row's churned date. A customer counts where they were when they became a customer; a later load that moves
them to another channel, segment or plan does not move them between cohorts. Only the owner and admins load or delete
customer facts (`tico growth customers delete` deletes them all; the figures go back to typed values).

**What is refused.** Nothing loads when any of these hold, and the message names the rule and the column, never a value:
a column outside the list; a customer key that looks like an email address or a phone number; a segment or plan longer
than 40 characters; a segment or plan column with more than 50 different values or more than one per two rows (a name
went into it); a date that is not a date; two rows of one customer that overlap. Keys are stored as you give them. You
may replace them with tokens before loading, as long as the same customer always gets the same token.

**The figures.** A cohort is the customers who became customers in the same month, or the same Monday-to-Sunday week.
For each cohort Growth computes:

- **New customers**: how many it holds.
- **Day 30, 90 and 180**: the share still paying on that day. A day shows only once every customer in the cohort is at
  least that old; until then it says "not yet" and the date it fills. September's day 90 fills on 29 December. It is
  never shown as 0%.
- **LTV and expected months**: the maths above, on the computed retention.
- **CAC and payback**: by channel only, from the month's spend. Spend is not split by segment or plan, and typed spend
  is monthly, so weekly cohorts and segment or plan cuts say so instead of a number.

A cohort under 30 customers shows counts only ("12 of 19") and too few for a rate, for retention, LTV and revenue. Change
the minimum with `tico growth settings --set min_cohort=N` (never below 5). Dates are read in the Growth time zone
(`--set time_zone=Europe/London`, default UTC).

Figures are worked out when they are read, so a load shows at once and day 90 and day 180 fill in as cohorts age, with
nobody typing. A two-week test shows its new customers and CAC by week; its day-30 retention appears 30 days after its
last week ends.

**On the Growth page.** With customer facts, the Retention card shows the picked month's cohorts. **Cut by** chooses
channel, segment or plan, and **Weekly / Monthly** the grain; a month shows every whole week that touches it. Each row has
its new customers, day 30, 90 and 180, LTV and CAC; the foot says how many customers the figures come from and when and
where they were loaded. The month picker lists the months with customer facts too. Each Number card says whether its
figure is computed, typed, or both (every channel together, some of each), and the Channels table dots its computed
figures.

**Computed beside typed.** A channel's month with customer facts shows the computed figures, each marked computed and
with the number of customers it comes from. A typed estimate for the same month is replaced. A typed actual that differs
is kept beside it. Channels and months with no customer facts read exactly as before. Every figure says which it is.

**The customers behind a figure.** On the Retention card, a day's figure opens to its customers. In the CLI,
`--customers` adds the rule ("became customer 2026-09-01 to 2026-09-30, channel
search-ads, still paying on day 30") and the customer keys, split into still customers and churned with the date. Anyone
who reads Growth sees the counts and the rule; the keys and churn dates show only to the owner, admins and the people or
bots they name (`--set key_readers=growth-product-owner`).

**KPI readings.** Each measurement a KPI tracks is a reading, logged with the period it covers. A correction supersedes
the earlier reading. The KPI views (Tree, Loops, Journey) are on Strategy's Goals tab; see
[Goals and KPIs](goals-and-kpis.md).

## Sources

**Sources** lists the systems that hold each number (spend, customers, retention) and which number each one is the truth
for. A source that only names a system is typed by hand: someone reads its numbers and enters them.

**Synced sources.** A source can fill its numbers itself, from a **file** or a **database query**. A file is a CSV with a `month` column
(`YYYY-MM`), a `channel` column (a channel's slug or name; blank or `unknown` is Unknown) and one column per number. Map
each number to its column once; the month and channel columns may have other names too:

```
tico growth source add --truth-for "new customers" --system "Signups sheet" --sync file \
  --map new_customers=signups --map revenue_month=revenue --map channel=source
tico growth source push <id> --file signups.csv
```

A **database query** source runs one named query (docs/databases.md, step 4) every day, on the computer of the bot
granted that database. The query returns the same columns as the file, and needs no parameter without a default.
Connecting it makes a daily automation (06:00 UTC) whose turn by that bot runs `tico growth source run <id>`: the query
runs there through `tico db`, audited as any other, and its rows are posted to the source. When it fails (a column is
missing, it times out, the database refuses the login) the reason is posted instead and the source shows Failing. The
server never holds the connection string.

```
tico growth source add --truth-for "new customers" --system "Signups (warehouse)" --sync db_query \
  --database warehouse --query signups-by-channel --bot data-bot --map new_customers=signups
tico growth source sync <id>      # starts the bot's run now
```

- **Months.** The first good run fills up to 12 months back. Later runs rewrite only the current month and the two
  before it, for late data and restatements. A future month is never written.
- **Rows it does not write.** A row naming a channel Growth does not have is not written; the run lists it, with
  the rows outside the months it may write.
- **Where each number came from.** Every number records whether it was synced (which source, when) or typed (by whom),
  and whether it is actual or an estimate. `tico growth cohort list --json` and `tico growth numbers --json` show it
  under `origins`.
- **Who wins.** A number a synced source filled belongs to it: typing it is refused, naming the source, until the source
  is retired. A sync never overwrites a number someone typed as actual. When the two differ, the typed number stays and
  the source's value is held beside it, and the source's owner or an admin keeps one
  (`tico growth cohort resolve ... --keep typed|source`). A value they chose not to use is not raised again. A typed
  estimate is replaced by the synced actual.
- **One source per number.** Two live synced sources never fill the same number.
- **Health.** `tico growth source list` shows each synced source as fresh, waiting (never run), stale (no good run in
  twice its refresh time, `--refresh-hours`; a file's is a week) or failing, with the reason. A failed run changes
  nothing already written. The source's owner gets one notice when it starts failing and one when it recovers.
- **Run again.** `tico growth source sync <id>` runs a file source's last file again, after a remap say, and starts a
  database query source's run on its bot's computer.

The owner and admins connect, remap and retire a synced source; retiring a database query source removes its daily
run. The daily run is an automation owned by the query's bot, so naming or changing that bot also needs the right to
make automations for it (its owner, or someone above it), and so does starting it early with `sync` (the bot itself
may always start its own run). Retiring the source, or switching it to another kind, removes its daily run whoever
does it: that run exists only for the source, and the owner and admins must be able to stop a source. `tico growth source update <id> --bot BOT` moves the run to
another bot and keeps the columns it maps. The owner, an admin, the source's own owner or its query's bot sends it rows.

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

## First run

An empty Growth needs four things, in this order, before it shows a team's first numbers:

1. **Settings**: revenue per customer per month and the margin (the unit has a default).
2. **A channel**: at least one active channel. Unknown is not a channel.
3. **Its source**: an active channel names a live source.
4. **Its first month**: an active channel that names a live source has a month with spend and new customers.

Every Growth read (`channel list` and `show`, `source list`, `cohort list`, `economics`, `numbers`, `kpis`, `settings`)
carries `setup` while any step is not done: each step and whether it is done, and the next one with its CLI command, its
MCP tool, whether the caller may do it and, if not, who may (the owner and admins by name). The CLI prints that next step
under its normal output. Once all four have been done, `setup` is only `{"done": true}` and stays so, even if a channel is
retired later. `tico growth setup` shows each step with the date it was first done.

Growth records once per team when it was first opened (any Growth read, by a person or a bot), when each step was first
done, and when its numbers were first seen (the Growth page, `economics`, `numbers` or a channel read after all four
steps are done). The Growth activation rate is read from these dates. A team whose first read already finds all four
steps done (set up before this was counted) is marked `set_up_before_first_open`, so the rate can leave it out. A Tico
support session reading Growth records nothing.

On the web, the Growth page shows a **Get started** panel over its cards while any step is not done: the four steps in
order, each done step with what was set, and the next one with a button that opens its form (Settings, a new channel,
the channel's source picker or Sources when there is no source yet, and the Months form on the channel and the last full
month). Someone who may not do the next step sees who may, by name, instead of the button. A team that finishes the
steps after first opening Growth is told once that it is set up, with a link to the channel's numbers
(`POST /api/v2/growth/setup/shown`); after that the panel does not come back. Each empty card names the step that fills
it, with a link; the Funnel, which reads the growth KPIs, links to Goals.

## Who may do what

A channel's stage and budget are its owner's, and the owning bot may change its why, stage and budget. Anyone else
proposes the change. The owner and admins may change any channel, source or setting.

Every Growth read carries `you_may`: per action, what this caller may do: change settings (`settings`), add or change
any channel (`channels`), add or change sources (`sources`), record months (`months`). `channels` false does not stop a
bot that owns a channel changing that channel's why, stage and budget. Each setup write (settings, a channel, a source,
a month) answers with `setup` too, so the next step follows without another read.
`may_edit` stays as it was: in `channel list`, `source list` and `settings` it is "may change channels, sources and
settings"; in `numbers` it is "may record months".

Everyone signed in reads the Growth page. The owner and admins arrange it, and so does a bot acting as itself, which
pins only the cards it made there (a bot pins no pages to it). Its audience is everyone; it has no audience to change.

## Commands

```
tico growth numbers                       the page: each month's rows, Unknown and the totals
tico growth setup                         the four first-run steps, each done or not, and the next one
tico growth channel list | show SLUG | add | update SLUG | retire SLUG
tico growth source list | add | update ID | retire ID
tico growth source add --truth-for TEXT --system NAME --sync file --map NUMBER=COLUMN [--map channel=COLUMN]
tico growth source add ... --sync db_query --database NAME --query ID --bot BOT --map NUMBER=COLUMN
tico growth source push ID --file FILE.csv | sync ID | run ID
tico growth cohort resolve --channel SLUG --month YYYY-MM --field NUMBER --keep typed|source
tico growth cohort list [--channel SLUG] [--month YYYY-MM]
tico growth cohort list --by channel|segment|plan|all [--grain week|month] [--week YYYY-MM-DD]
                 [--segment LABEL] [--plan LABEL] [--customers] [--json]
tico growth customers load customers.csv | delete
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
