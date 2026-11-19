# Growth

Growth (`#/growth`, under Company in the sidebar) is one read-only page over the one read
([The core](#the-core-cohort-rows-costs-one-read), `GET /api/v2/growth/read`): what the month brought, what it cost,
how many stayed, what a customer was worth, each channel, the trends, retention and Growth's KPIs. Everyone signed in
reads it. Nothing on it is edited: numbers arrive as pushed rows and costs, or by chat with the Growth Manager, and every
setting has a CLI command and an MCP tool ([Commands](#commands)). Marketing work is not on this page: marketing ideas
are tasks, and the KPIs the numbers feed are read on Strategy's Goals tab.

Examples use Acme, a home-cleaning company.

## The page

The header has **All** and a tab per segment (none without segments) and a **month** picker: the read's months, newest
first, the last full month by default. The address keeps both (`#/growth?scope=segment:rental&m=2026-09`). Under it:

- **Tiles**: sign-ups, activated, paid, new customers, cost, CAC, kept, then LTV, LTV:CAC and payback for a revenue
  scope, or cost per retained for a retention one. Each shows its verdict against the three months before and, in small
  text, what it rests on (estimate, cohort month, window, how many, cost with effort, loaded CAC, retention spend).
- **Channels**: owner, new customers, cost, CAC, budget (and how far over), cost share and new share, Unknown last. A
  channel with no money spent says "no spend" and shows its CAC counting effort.
- **Trends**: twelve months of sign-ups and new customers as bars, CAC and kept as lines, the picked month marked.
- **Retention**: the six newest cohorts and the share kept at each window the data has.
- **KPIs**: Growth's KPIs, each opening on Goals.
- **Health**: Unknown's share, rows with no segment, mismatches, reconciliation and stale sources, in one muted line.

A figure the read has no value for says why in a muted word: too few, not supplied, no spend, not yet. It never reads
0. The page's chat (the Growth Manager, `#/growth`'s page owner) carries the scope and month on screen with each message.

## The numbers

Each month has one row per channel and one **Unknown** row. A row holds:

- spend and bot cost, in dollars;
- new customers;
- the share still customers at 30, 90 and 180 days, blank until the cohort is that old (or computed from customers:
  see "Computed from customers" below);
- revenue per customer per month and margin (`--revenue`, `--margin`, with the margin as a percent);
- a quality mark: `actual` when every input came from a source, `estimate` when one did not. A month typed without a
  mark (`tico growth cohort set` with no `--quality`, or the agent tool with no `quality`) is saved as an `estimate`;
  pass `--quality actual` when the numbers come from your books. The web form's Data field defaults to Estimate too.

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

**Customer facts.** One row per customer, as a CSV: `tico growth customers load customers.csv` (MCP `tico_growth_customers_load`). Growth keeps only these columns:

| Column | Required | What it holds |
| --- | --- | --- |
| customer key | yes | your own id for the customer, never a name, an email or a phone number |
| signed up | no | a date |
| became customer | yes | the date the customer first paid |
| channel | yes | a Growth channel slug; anything else counts as Unknown |
| segment | no | a short label, such as `agency`; with segments set, a segment's slug or name, any case |
| plan | no | a short label, such as `pro` |
| churned | no | the date the customer stopped paying; blank while they pay |
| revenue per month | no | for this customer; blank uses the channel's month, then the segment's, then the settings value |
| net d90, net d180, net d365 | no | the customer's cumulative net revenue by that age, refunds taken off (it may be negative) |
| attribution | no | which rung named the channel: `utm`, `code`, `asked`, `unknown` or another short label |

Each load replaces the last one: a customer left out is deleted, which is how you remove someone who asks to be
forgotten. A customer who left and came back is a second row with the same key and a new became customer date, after
the first row's churned date. A customer counts where they were when they became a customer; a later load that moves
them to another channel, segment or plan does not move them between cohorts. Only the owner and admins load or delete
customer facts (`tico growth customers delete` deletes them all; the figures go back to typed values).

**From a synced source.** A source connected with `--sync file --map customers` takes the same columns: each file you
push (`tico growth source push ID --file customers.csv`) is a load of that source's customers only, so an upload in
Growth and two sources never replace each other's. A file the rules refuse fails the run, with the rule, and leaves its
last customers as they were. Retiring the source, stopping its sync or mapping it to numbers deletes its customers and
its stored file at once.

**What is refused.** Nothing loads when any of these hold, and the message names the rule and the column, never a value:
a column outside the list; a customer key that looks like an email address or a phone number; a segment or plan longer
than 40 characters; a segment, plan or attribution column with more than 50 different values or more than one per two
rows (a name went into it); a date that is not a date; a net value that is not a number; two rows of one customer that overlap. Keys are stored as you give them. You
may replace them with tokens before loading, as long as the same customer always gets the same token.

**The figures.** A cohort is the customers who became customers in the same month, or the same Monday-to-Sunday week.
For each cohort Growth computes:

- **New customers**: how many it holds.
- **Day 30, 90 and 180**: the share still paying on that day. A day shows only once every customer in the cohort is at
  least that old; until then it says "not yet" and the date it fills. September's day 90 fills on 29 December. It is
  never shown as 0%.
- **LTV and expected months**: the maths above, on the computed retention.
- **CAC and payback**: by channel, from the month's spend, and by segment once the team has segments (below). Spend is
  not split by plan, nor by segment for a team without segments, and typed spend is monthly, so weekly cohorts and
  those cuts say so instead of a number.
- **Observed LTV at 90, 180 and 365 days**: once any customer has a net value, the mean of the net values of the
  cohort's customers that old. Until its first customer is that old it says "not yet" and the date; with fewer values
  than the minimum cohort it shows the count and too few.
- **Attribution**: how many of the cohort's customers each rung named, when the column is loaded.

A cohort under 30 customers shows counts only ("12 of 19") and too few for a rate, for retention, LTV and revenue. Change
the minimum with `tico growth settings --set min_cohort=N` (never below 5). Dates are read in the Growth time zone
(`--set time_zone=Europe/London`, default UTC).

Figures are worked out when they are read, so a load shows at once and day 90 and day 180 fill in as cohorts age, with
nobody typing. A two-week test shows its new customers and CAC by week; its day-30 retention appears 30 days after its
last week ends.

**On the Growth page.** With customer facts, the read's figures and its Retention grid are computed from them.

**Computed beside typed.** A channel's month with customer facts shows the computed figures, each marked computed and
with the number of customers it comes from and the customers source(s) they were loaded from (`origins` in
`tico growth cohort list --json`: `computed`, `computed_from`, `sources`). A typed estimate for the same month is
replaced. When a typed actual differs, the
Growth Manager asks whoever typed it which is right, and it, the channel's owner or an admin keeps one
(`tico growth cohort resolve ... --keep typed|computed --against computed`, MCP `tico_growth_cohort_resolve`). After a load,
and after a number is typed as actual, the channel's owner gets one notice naming the numbers that differ; each
channel, month and figure is asked about once. A channel with no owner is asked once an owner is set, at the next load.
LTV uses the computed shares even where a typed number was kept.
The choice holds across later loads; typing a new number for that month and figure asks again. Channels and months
with no customer facts read exactly as before. Every figure says which it is.

**The customers behind a figure.** In the CLI,
`--customers` adds the rule ("became customer 2026-09-01 to 2026-09-30, channel
search-ads, still paying on day 30") and the customer keys, split into still customers and churned with the date. Anyone
who reads Growth sees the counts and the rule; the keys and churn dates show only to the owner, admins and the people or
bots they name (`--set key_readers=growth-product-owner`).

### Segments

A segment is a group of customers worth different money: Consumer and Rental, say. Each has a slug and a name, its
customer rule in plain words (when someone becomes a customer and when they churn), `churn_days` (the inactivity window
churn waits on; 0 when churn is a cancel date), and its own revenue per customer month, margin and minimum cohort, each
falling back to the settings when blank. `tico growth segment set consumer --name Consumer --churn-days 90` adds or
changes one; `tico growth segment list` names them with their channels.

- **Who is in a segment.** The customer's `segment` label, matched to a segment's slug or name in any case. A label
  that names none, or a blank one, is **No segment** (`none` in the cut), shown last.
- **Which spend is whose.** A channel's spend is for one segment (`tico growth channel update brand --segment
  consumer`) or company-wide (no segment). The customer's own segment decides counts, retention and LTV; the channel's
  segment decides spend only.
- **Direct CAC**: the spend and bot cost of the segment's own channels ÷ its new customers. Payback uses it.
- **Loaded CAC**: direct plus company-wide spend times the segment's share of the period's new customers in a segment
  (No segment takes no share, so company-wide spend is spread over the customers Growth can place). Filtering to one
  segment keeps the share it has among all of them. The segments' loaded costs add up to their own channels' spend plus
  all company-wide spend; No segment carries none of it, and `no_segment` says how many customers that leaves out.
- **Churn windows.** Day 30, 90 and 180 of a segment's cohort wait `churn_days` more before they fill, since a customer
  who stopped is not churned until the window runs out. A cohort holding several segments (the channel cut) waits the
  longest of them.
- **Health.** Every answer with segments carries `no_segment` (customers whose label names none) and `mismatch`
  (customers in a segment other than the one their channel's spend is for; a company-wide channel matches every
  segment), over every customer in the period, whatever the filter.

Retiring a segment (`--retire`) stops channels being given it; customers whose label names it still count under it. A
team with no segments reads its labels as before. The owner and admins add, change and retire segments.

### The core: cohort rows, costs, one read

Every Growth number is a sum over two kinds of rows (backend/growth_core.py), read through one read.

- **Cohort rows** (`tico growth rows push --source LABEL --file rows.csv`, MCP `tico_growth_rows_push`). One row is the
  people in one cohort cell: `anchor` (`signup`: signed up that month; `customer`: reached the segment's customer step
  that month), `period` (YYYY-MM), `segment` and `channel` (slug or name; blank is No segment and Unknown),
  `campaign` (blank is none; at most 10 named per channel and month: name the rest `other`), `as_of` (when the counts were true),
  `entered`, and counts each with its `_eligible`: `activated`, `paid`, `kept_d30`, `kept_d90`, `kept_d180` (by the
  segment's own kept rule), and `net_d90_sum`, `net_d180_sum`, `net_d365_sum` with `net_dN_eligible`. Counts are whole
  numbers with `x <= x_eligible <= entered`; a blank cell is not supplied, never 0, and a blank count with
  `_eligible` 0 is not supplied too. Customer facts fold into
  customer-anchor rows when read (kept by the segment's `churn_days`, net revenue by age); they are never copied.
- **Costs** (`tico growth costs push --source LABEL --file costs.csv`, MCP `tico_growth_costs_push`): `period`,
  `channel` (blank is company-wide), `campaign`, `kind` (`money`, `promo_cash`, `promo_credits` at real cost when
  redeemed, `bot`, `hours` in money), `purpose` (`acquisition`, the default, or `retention`), `amount`, `quality`. Typed
  spend and bot cost (and Finance's ad spend, which fills them) read as `money` and `bot` acquisition costs, except
  where a pushed cost of that kind covers the channel and month: pushed wins. Typed new customers count where no
  customer-anchor row covers the channel and month.
- **A segment's `value_kind`**: `revenue` (the default) or `retention` (`tico growth segment set viewers --value-kind
  retention`).

A push replaces every row of its source for the months it covers, since cohorts restate as they age. The owner, admins
and the Growth Manager push anything; a channel's owner pushes rows and costs for its own channels only; a refusal names the rule, the column and the row numbers, never a value, and nothing is
pushed. A source holds customer facts or pushed rows, never both.

**The read** (`GET /api/v2/growth/read`, `tico growth read`, MCP `tico_growth_read`; everyone signed in): `--scope`
`all`, `mine` (the channels the caller owns; none is an empty list), `segment:<slug>` (`segment:none` is the rows with no segment) or `channel:<slug>`
(`channel:unknown`), `--period` (default the last full month; the current month reads `partial` with no verdicts) and `--detail` (`campaigns`, `retention`, `series`). It answers `headline`, `channels`,
`health` and `metrics`:

- **Counts** (signups, new customers) with `vs_3mo` and a `verdict`: `up`, `down` or `flat` (within 10%) against the
  trailing 3-month average, `too_few` under 5.
- **Rates** (activated, paid, kept, LTV as observed net revenue per customer) come from the latest cohort whose eligible
  count reaches the minimum cohort, and name it (`cohort`, `n`). Not pushed reads `not_supplied`.
- **Cost and CAC** are money and promotions for acquisition. With none, both are null with `no_spend`, never 0;
  `with_effort` adds bot cost and hours. Retention costs show as `cost.retention` and never enter CAC. A segment's CAC
  is its own channels' costs; `cac_loaded` adds company-wide costs by its share of new customers. A CAC under the
  minimum cohort has the verdict `too_few`. `payback_months` is CAC over net revenue per month, an `estimate`.
- **Channels** each carry their `new_customers`, `new_share`, `owner`, `budget` (`budget_month`) and `over_budget` (cost past the budget, else null).
- **A retention segment** reads `value` (`kept_d30` and `cost_per_retained`) in place of LTV, LTV:CAC and payback.
- **Health**: the Unknown share, `no_segment` and `mismatch` (with segments), counted over customers when the company
  pushes any, else over sign-ups (`basis`: `customers` or `signups`), `reconciled` (`not_reconciled` for now),
  and `stale_sources` (rows whose newest `as_of` is over 45 days old).
- **Metrics**: Growth's KPIs (a channel's input or guardrail KPI, or the Growth Manager's own), at most 20, each with
  its `channel`, latest `value` up to the month's end, `period_end`, `estimate` and `vs_prior` (the reading before).
  KPIs of bots the caller may not read are left out; a channel or `mine` scope shows only its channels' KPIs.

The read sums when asked: a TIDY-sized company (30,000 customers over two years) reads in under 0.1 s.

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
  the source's value is held beside it; the Growth Manager asks whoever typed it which is right, and it, the source's
  owner or an admin keeps one (`tico growth cohort resolve ... --keep typed|source`). A value they chose not to use is not raised again. A typed
  estimate is replaced by the synced actual.
- **One source per number.** Two live synced sources never fill the same number.
- **Health.** `tico growth source list` shows each synced source as fresh, waiting (never run), stale (no good run in
  twice its refresh time, `--refresh-hours`; a file's is a week) or failing, with the reason. A failed run changes
  nothing already written. The source's owner gets one notice when it starts failing and one when it recovers.
- **Run again.** `tico growth source sync <id>` runs a source now: a file source's last file again (after a remap,
  say), a Finance ad spend source from Finance's latest import, a database query source on its bot's computer.


**Finance ad spend.** Spend can come from Finance's Google Ads and Meta import. Growth reads its daily spend per
campaign in the book's currency and adds it up per channel and month. A day counts in the month the ad platform reports
it. Map each campaign to a channel once. A campaign left out, or mapped to `unknown`, counts on Unknown. Growth shows
only the channel-month totals, never a campaign. The source runs after each Finance import, so a day the platform
restates reaches Growth on the next import. An ad account in another currency than the books makes the source failing,
naming the account; nothing is converted. Connecting or remapping it also needs someone who sees every connected ad
account in Finance (a finance admin). The `--campaign` flags of an update replace the whole map, so a remap lists
every campaign to keep:

```
tico growth source campaigns
tico growth source add --truth-for spend --system "Finance ad spend" --sync finance_ad_spend \
  --campaign meta_ads:111:brand=search-ads --campaign meta_ads:111:retarget=social-ads
```

The owner and admins connect, remap and retire a synced source; retiring a database query source removes its daily
run. The daily run is an automation owned by the query's bot, so naming or changing that bot also needs the right to
make automations for it (its owner, or someone above it), and so does starting it early with `sync` (the bot itself
may always start its own run). Retiring the source, or switching it to another kind, removes its daily run whoever
does it: that run exists only for the source, and the owner and admins must be able to stop a source. `tico growth source update <id> --bot BOT` moves the run to
another bot and keeps the columns it maps. The owner, an admin, the source's own owner or its query's bot sends it rows.

**On the web.** A source's health shows on the Growth page as a stale source in its health line; sources are added,
mapped and run by chat or with the commands above.

**A signed address for a file source.** `POST /api/v2/growth/sources/<id>/address` (the owner, an admin or the source's
owner) gives a file source an address and a signing secret, shown once; a new one stops the old. A script posts the CSV
as the request body to `/api/v2/growth/in/<id>` with `X-Signature: sha256=<HMAC-SHA256 of the body under the secret>`, and
the result is the same as an upload. A missing or wrong signature is refused with 401 and writes nothing.

**Bot cost.** A `bot_cost` source (`tico growth source add --sync bot_cost`) fills only bot cost: each month, the run cost
of the bot that owns a channel goes on that channel. A bot that owns several channels cannot be split, so its cost goes on
Unknown and the run result carries a note. `sync` runs it.

## A channel's read and its alerts

A growth agent starts each run with `tico growth channel show <slug>` (API `GET /api/v2/growth/channels/{slug}`, MCP
`tico_growth_channel_show`). Besides the channel's settings and open `channel:<slug>` tasks it answers, in this order:

1. **KPI.** The KPI the channel moves (`input_kpi_id`, set with `tico growth channel update <slug> --input-kpi <id>` by the bot that owns
   the channel, the owner or an admin) with its latest value and date, the target and colour of the goal it is linked
   to, and its last 12 readings. Growth keeps no target of its own: a KPI with no target on any goal says "no target:
   set on the goal by the Strategy Owner". With no KPI linked, the read says how to link one and who may.
2. **Months.** The last 6 months of spend, new customers, CAC, kept at 30 days and LTV:CAC, each with its quality.
3. **Changed.** The newest month against the month before, per figure: value, difference and per cent. A figure not
   recorded in either month reads "not recorded" and has no change (never 0). Each figure that changed lists what lines
   up with it since the start of the month before: tests started or finished on the channel (with their result) and
   stage changes bear on every figure, budget changes on spend, CAC and LTV:CAC; a figure whose source was failing or
   stale says it "may be a data gap"; when nothing recorded lines up, it says "nothing recorded on this channel explains
   it". Tico never states a cause. The dated events (tests, stage and budget changes, crossings) follow, each with who
   and when.
4. **Tests.** Open tests on the channel with their stage and start date, and tests finished in the last 90 days, kept or
   dropped, with their `result:` tag. Only tasks the reader may see.
5. **Sources.** Each source behind the channel's figures with its health and last good update, and "stale" when it is
   failing or stale or the newest month is more than 45 days old.

`tico growth segment show <label>` (API `GET /api/v2/growth/segments/{label}`, MCP `tico_growth_segment_show`) is the
same for a segment, from customer facts: new customers, kept at 30 and 90 days and LTV for the last 6 months, and the
change from the month before. A month under the minimum cohort shows counts only. With segments set it takes the slug
or name (`none` for No segment) and adds direct and loaded CAC and observed LTV by month, the segment's customer rule,
`churn_days`, its channels and its health counts; without segments it says spend is not split by segment.
`GET /api/v2/growth/view-reads` counts the channel and segment reads by caller and run.

**Alerts.** Three lines the team already set are watched: the channel's KPI turning red against its goal, its guardrail
KPI going over its max, and its synced source failing. A KPI reading or a source run that crosses one files one task for
the channel's owner, filed by the owner itself (so it may close it), labelled `channel:<slug>` and `growth:alert`, in
General, with the value, the line and the read command. While the channel stays over the line, Tico comments on that
task at most once a day; once the task is closed or done, the next crossing files a new one. Coming back under the line
comments once and asks the owner to close the task: Tico never closes it. A channel with no owner tells nobody, and its
read lists the crossing with "no owner: nobody was told". Each change of state emits `growth.channel.crossed` (ids
only) for routines and rules. A failure in the check never undoes the reading or the run.

## Marketing tasks

A growth experiment is a task in **Growth Experiments** (Ideas, Building, Running), and a thing that worked and is kept
is a task in **Growth Monitoring** (Watching, Slipping): docs/dev/pipelines-v3.md "Growth and Success". Each channel has an
Owner that files its ideas and owns its tasks in both pipelines from start to finish: SEO, Content, Email, CRO, Search
Ads and Social Ads (the growth channel packs in the same section).

- **Channel.** A `channel:<slug>` label on the task or its sub-task. A channel's marketing work is its open
  tasks with that label, in any pipeline. The department is the task's team.
- **Prediction and result.** An idea carries no prediction. The Go-live page in Building gives it once, as a number: the
  metric from where it is now to where it should get to, by the end date; the Results page in Running
  gives the number before and after. The result is a label: `result:failed`, `result:worked-once` or
  `result:watching`, and `result:watching` files a Monitoring task as the experiment's follow-up.
- **Ending.** A measured experiment is Done; one stopped before it is measured is Closed with the reason. A Monitoring
  task that can no longer be fixed is Closed with the reason, and an idea is filed for what to try instead.

Growth reads both forms. An open task in Growth Experiments or Growth Monitoring is in the Growth board (`tico card`
template `growth_board`) lane its stage gives (Running is Live, Watching and Slipping are Working, with Slipping marked, Building is In
development, Ideas is On deck); a move into Running is a test's start marker, and a move from
Growth Experiments to Done (kept, with its `result:` tag) or Closed (dropped) its finish marker. A stage a team added
to Growth Experiments or Monitoring reads as that pipeline's first stage. Success Experiments and
Success Monitoring are not read here.

The older form, an idea as a General task with a stage label (`growth:ideas` or `idea:on-deck`, `idea:in-development`,
`growth:running` or `idea:live`, `idea:working`, `growth:measuring`, `idea:paused`; backend/marketing_tasks.py), is still
read the same way: taking a running label starts a test, and Done or Closed finishes it. A task in a Growth pipeline
that also carries a stage label shows once, where its pipeline stage says, and starts by its moves alone. The Growth Manager and the segment Marketing
Owners still file ideas that way.

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

On the web, Growth gets its numbers by chat, not forms: the Growth Manager's `growth-interview` skill asks whoever
knows (founder, sales, finance, marketing, support), one question at a time, takes answers in any shape (a guess is
saved as an estimate), routes "ask Ana" to Ana, and writes each answer as it arrives (`numbers-from-chat`); things that
already work become Growth Monitoring tasks (`what-already-works`), and person sources are asked again when a month
closes (`keep-numbers-fresh`).

## Who may do what

A channel's stage and budget are its owner's, and the owning bot may change its why, stage, budget and KPI. Anyone else
proposes the change. The owner, admins and the Growth Manager (`bot:growth-manager`, which writes what people tell it)
may change any channel, source or setting; only the owner and admins retire one.

Every Growth read carries `you_may`: per action, what this caller may do: change settings (`settings`), add or change
any channel (`channels`), add or change sources (`sources`), record months (`months`). `channels` false does not stop a
bot that owns a channel changing that channel's why, stage and budget. Each setup write (settings, a channel, a source,
a month) answers with `setup` too, so the next step follows without another read.
A bot may not add a channel or source, but it may **propose** one it will own: `tico growth channel propose <slug>
--name "..."` (with `--source-system`, `--source-truth-for` and the other `--source-*` flags for the source of its
numbers) or `tico growth source propose --channel <slug> ...` for a channel it already runs (`POST
/api/v2/growth/proposals`). It is an OK in the Needs you of the owner, or of an admin named with `--ask`, given from its
preview; the owner or any admin may give it (its task is theirs to read), and only once. Nothing is added before that; on the OK, Tico checks it all again, adds the channel and source as
that person, owned by the bot (the channel names the new source), and sends the bot a notice with what was added. A No
adds nothing and the bot hears why. A bot never proposes another owner, a channel already added or already waiting, or
a Finance ad spend source (a person connects it). While the next setup step is a channel or a source, a bot's `setup`
says so under `you_may_propose`, with the command, the tool and who OKs it.

`may_edit` stays as it was: in `channel list`, `source list` and `settings` it is "may change channels, sources and
settings"; in `numbers` it is "may record months".

Everyone signed in reads the Growth page. Its audience is everyone; it has no audience to change.

## Commands

```
tico growth numbers                       each month's typed rows, Unknown, the totals and `line`
tico growth setup                         the four first-run steps, each done or not, and the next one
tico growth channel list | show SLUG | add | propose SLUG | update SLUG | retire SLUG
tico growth channel update SLUG --input-kpi KPI_ID the KPI the channel moves (its target stays on its goal)
tico growth segment show LABEL            one segment's 6 months from customer facts
tico growth segment list [--retired]
tico growth segment set SLUG [--name TEXT] [--rule TEXT] [--churn-days N] [--revenue N] [--margin N]
                 [--min-cohort N] [--value-kind revenue|retention] [--owner WHO] [--retire]
tico growth channel add|update SLUG --segment SLUG   the segment its spend is for ("" for company-wide)
tico growth read [--scope all|mine|segment:SLUG|channel:SLUG] [--period YYYY-MM] [--detail campaigns,retention,series]
tico growth rows push --source LABEL --file rows.csv
tico growth costs push --source LABEL --file costs.csv
tico growth source list | add | propose | update ID | retire ID
tico growth source add --truth-for TEXT --system NAME --sync file --map NUMBER=COLUMN [--map channel=COLUMN]
tico growth source add ... --sync db_query --database NAME --query ID --bot BOT --map NUMBER=COLUMN
tico growth source push ID --file FILE.csv | sync ID | run ID
tico growth source push ID --file FILE.csv | sync ID
tico growth source campaigns [--months N]  Finance's ad campaigns and their keys, to map
tico growth source add --truth-for spend --system NAME --sync finance_ad_spend --campaign KEY=CHANNEL
tico growth source add --truth-for customers --system NAME --sync file --map customers
tico growth cohort resolve --channel SLUG --month YYYY-MM --field NUMBER --keep typed|source
tico growth cohort resolve --channel SLUG --month YYYY-MM --field NUMBER --keep typed|computed --against computed
tico growth cohort list [--channel SLUG] [--month YYYY-MM]
tico growth cohort list --by channel|segment|plan|all [--grain week|month] [--week YYYY-MM-DD]
                 [--segment LABEL] [--plan LABEL] [--customers] [--json]
tico growth customers load customers.csv | delete
tico growth cohort set --channel SLUG|unknown --month YYYY-MM [--spend N] [--bot-cost N] [--new N]
                 [--d30 N] [--d90 N] [--d180 N] [--revenue N] [--margin N]
                 [--quality actual|estimate] [--source FIELD=TEXT]
tico growth settings [--set KEY=VALUE]
tico kpi log <kpi id> <value> [note] --period-start YYYY-MM-DD --period-end YYYY-MM-DD
tico task create --owner <who> --label idea:on-deck --label channel:<slug> --title "..." --message "..."
tico task list --label channel:<slug>
```

## Related

[Strategy](strategy.md), [Goals and KPIs](goals-and-kpis.md), [Routines](routines.md), [Tasks](tasks.md).
</content>
