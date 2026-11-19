---
name: what-already-works
description: File each thing people say already works (a channel, a campaign, a referral deal, an email) as a Growth Monitoring task in Watching, with its number and normal range, so it is watched and does not get lost. Use when an interview or call names something that works.
metadata:
  function: growth
---
# What already works

## Steps
1. For each thing named, find its channel (`tico growth channel list`); add the channel if it is
   new (`skills/numbers-from-chat/SKILL.md`).
2. Check it is not already watched:
       tico task list --pipeline growth-monitoring --label channel:<slug> --status open --brief
3. File it for the channel's Owner (`tico growth channel show <slug>` names them; else yourself):
       tico task create --owner <owner> --pipeline growth-monitoring --label channel:<slug> \
         --title "<the thing>" --message "<number it moves>, normal range <low>-<high> from <who/when>. Said to work by <person> on <date>."
4. No range yet: say so in the message; the Owner sets one after four weeks of readings.
5. Tell the person in one line: "Watching: <the thing>."
