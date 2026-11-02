# Weekly numbers

When: Mondays at 07:00 (routine `weekly-numbers`), before the growth meeting reads them. Budget 30 minutes. The outcome is
this month's channel numbers and the KPI readings, entered from the sources and each one read today. Unknowns stay empty,
and the closing line names them.

Every number comes from a source you read today. You never type a computed number, and you never guess a channel.

---

## 1. Read the sources

    hub growth source list          which system holds each number: spend, customers, retention
    hub growth channel list         the channels Tico has; nothing else exists

## 2. Set this month's channel numbers

For each channel, and for the Unknown row, set the month (`YYYY-MM`). Give only the fields a source gives you:

    hub growth cohort set --channel <slug> --month <YYYY-MM> --spend <dollars> --bot-cost <dollars> \
      --new <customers> --d30 <percent> --d90 <percent> --d180 <percent> --revenue <monthly $ per customer> \
      --margin <percent> --quality actual --source "new=<system and view>" --source "spend=<finance report>"

- **Spend** comes from the Finance report for the month, tagged by channel. If Finance is not connected, use the owner's
  number and `--quality estimate`.
- **New customers and retention** come from the CRM, using the system `hub growth source list` names. A customer with no
  source, or a source that is not a channel Tico has, goes to `--channel unknown`. Never assign a channel Tico does not
  have, and never guess.
- **Retention** at day 90 or 180 is blank (leave the flag out) until the cohort is that old. Do not write 0.
- **Quality** is `actual` only when every number came from a source you read; otherwise `estimate`, and say which.
- **Bot cost** is what the channel's bot work cost this month, as its source gives it. When no source gives it, leave it out and mark the cohort `estimate`.

## 3. Log the KPI readings

Each measurement a KPI tracks is a reading, with the period it describes:

    hub kpi log <kpi id> <value> --period-start <YYYY-MM-DD> --period-end <YYYY-MM-DD>

Log only what you read today. To correct a reading, supersede it (`--supersedes <reading id>`); do not add a second one.

## 4. Read the numbers back

    hub growth numbers

Tico computes the numbers from what you entered. Read them; do not change them. If the Unknown row is large, say so first
in the closing line.

## 5. Finish

    hub task update <id> --status done --note "<week of YYYY-MM-DD>: <n> cohorts set, <k> readings logged; unknown share <x>%; not available: <source, or none>"

If a source was not available, name it in the closing line and mark its cohort `estimate`. Numbers that read as the whole
picture with one source missing are worse than numbers that name the gap.
