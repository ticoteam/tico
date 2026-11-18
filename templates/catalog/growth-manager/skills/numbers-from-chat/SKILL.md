---
name: numbers-from-chat
description: Turn what someone said (a chat reply, a pasted sheet, a screenshot, a call transcript) into Growth's channels, monthly numbers, settings and KPI readings, then read back what was saved. Use after every answer in a growth interview and after a growth call.
---
# Numbers from chat

## Steps
1. Pull out every fact: channel, month, spend, new customers, revenue per customer, margin, kept
   shares, a KPI reading. Write each with who said it and when.
2. Check the month. "Last month" is the last full calendar month on the day it was said.
3. Write it:
   - A new channel: `tico growth channel add <slug> --name "<Name>"`. Slug = the name, lower-case,
     dashes. Unknown is not a channel.
   - A month: `tico growth cohort set --channel <slug> --month <YYYY-MM> --spend <dollars> --new <count>`. Leave
     `--quality actual` off unless they read it from a system ("from the Stripe report": actual;
     "about 30": estimate). `--quality` marks the whole month, so leave it off while any other
     number for that channel and month is still an estimate.
   - Revenue per customer and margin: `tico growth settings --set revenue_month=N --set margin_pct=N`.
   - A KPI reading: `tico kpi log <kpi id> <value> "<who said it>" --period-end YYYY-MM-DD`, with
     `--estimate` for a guess. If it is refused (not your KPI), send the number to the KPI's owner
     in chat instead.
   - Where it came from: the first number a person gives for a channel names them as its source,
     `tico growth source add --truth-for "<what>" --system "<their name>" --reach-kind person --reach-ref <their person id>`, then
     `tico growth channel update <slug> --source <id>`.
4. Never change a number Tico computed, and never overwrite a typed actual with an estimate. A
   number that disagrees with its source: ask the person who typed it which is right, then
   `tico growth cohort resolve --channel <slug> --month <YYYY-MM> --field <field> --keep <typed|source>`.
5. Read back in one line what you saved, with "estimate" where it is one. Ask for nothing else in
   that line.

## Checks
- Every saved number has a month, a channel (or is a setting) and a person or system it came from.
- Ranges become their midpoint, saved as an estimate ("20 to 30" → 25).
- A currency other than the team's unit is said back, not converted silently.
