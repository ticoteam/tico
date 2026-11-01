# Monthly growth report

When: the 3rd of each month at 09:00 (routine `monthly-growth-report`), for the month before. Budget 90 minutes. The outcome
is a Page with the month's numbers from the sources, what was learned, which bets are working, and one proposal for next month.

Every number on the page comes from Tico (`hub growth economics`) or from a source you read today. You never type a
computed number, and you never change a formula.

---

## 1. Put the month's cohorts in

Cohorts are the inputs to the economics. Read the sources first:

    hub gtm sources --json          which system and field holds each truth: spend, customers, retention
    hub growth channel list         the channels Tico has; nothing else exists

For each channel, and for the Unknown row, set the month (`YYYY-MM`) once:

    hub growth cohort set --channel <slug> --month <YYYY-MM> --spend <dollars> --bot-cost <dollars> \
      --new <customers> --d30 <percent> --d90 <percent> --d180 <percent> --revenue <monthly $ per customer> \
      --margin <percent> --quality actual --source "new=<system and view>" --source "spend=<finance report>"

- **Spend** comes from the Finance report for the month, tagged by channel. If Finance is not connected, use the owner's number and `--quality estimate`.
- **New customers and retention** come from the CRM, using the source field `hub gtm sources` names. A customer with no
  source, or a source that is not a channel Tico has, goes to `--channel unknown`. Never assign a channel Tico does not have,
  and never guess.
- **Retention** at day 90 or 180 is blank (leave the flag out) until the cohort is that old. Do not write 0.
- **Quality** is `actual` only when every number came from a source you read; otherwise `estimate`, and say which.
- Bot cost is what the channel's bot work cost Tico this month, from Tico's usage report for the month.
- **Segments**: when the source can tell customers' segments apart (`hub growth segment list`), set one row per segment
  with `--segment <id>`. Spend goes on a segment's row only when the source ties it to that segment; the rest stays on
  the channel's row without `--segment`.

## 2. Read the economics

    hub growth economics --month <YYYY-MM> --json

Read the rows (with `--json`: quality, retention and sources for each row), the total, the `settings` (revenue, margin,
win share) and the `guardrails`. Check that the Unknown row is
there; a large Unknown share goes on the page as the first learning, not as a footnote.

## 3. Read what was learned and what the bets did

    hub growth idea list --status won --json
    hub growth idea list --status lost --json
    hub growth idea list --status inconclusive --json
    hub growth evidence
    hub growth kpis

Take only ideas whose verdict date (in the JSON) is in the month. For each bet: supports, weakens and none, and the bet's goal status
from `hub strategy show`.

## 4. Write the Page

Write `reports/<YYYY-MM>-growth.html`, a single HTML page, and publish it:

    hub page publish reports/<YYYY-MM>-growth.html --cadence monthly --as-of <YYYY-MM-01>

Use these sections, in this order, with no intro and no thanks:

1. **Top line**: the North Star this month against last, and the blended CAC and payback, in one sentence.
2. **By channel**: one table. Columns: channel, spend, new customers, CAC, kept at 30 days, payback, LTV:CAC, quality.
   The Unknown row is in the table. Under the table, the method in fixed words: "CAC = (spend + bot cost) ÷ new customers.
   Payback = CAC ÷ (revenue × margin). Sources are listed per channel in the economics output."
3. **What we learned**: one line per verdict this month: the idea, the verdict, the learning.
4. **Which bets are working**: one line per bet with its supports, weakens and none counts, and what you would do if it retired.
5. **Next month**: the tests to start (from the weekly meeting), and one proposal.

The proposal is one question to the owner on this task, Yes or No or a pick, for a budget change or a stage change. A channel's
budget and stage are the owner's to change. Ask with `hub task question` and name the amount in the option.

## 5. Finish

    hub task update <id> --status done --note "<month>: page published; <n> cohorts set; unknown share <x>%; proposal asked"

If a source was not available, say which, in the closing line, and mark its cohort `estimate`. A page that reads like the
whole picture with one source missing is worse than a page that names the gap.
