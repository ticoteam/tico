---
name: work-with-software
description: Use the company's tools and scripts instead of doing exact, repeated or bulk work by hand - find a tool first, turn a job you do the third time into a tested script, and keep the Tools registry and your own list accurate. Use before counting, summing, converting, comparing or moving data, calling a vendor, or any job you have done before.
metadata:
  function: tico
---
# Work with software

A tool or a script gives the same exact answer every time, for a fraction of the tokens. Reading a big file, counting
rows by eye or redoing yesterday's steps by hand is slower, costs more and is wrong more often.

## Before you start

1. Run `tico tool find <words>` (MCP `tico_tool_find`). It lists the company's tools: the vendors in Tools, the guides
   Tico ships (jq, DuckDB, psql, ffmpeg, pandoc and more) and the tools agents built here.
2. Read the guide of the one that fits: `tico tool guide <slug>`. It says how to use it, the company's own guidance,
   and your access: the variable your run reads it from, or the exact `tico access request` command when you have none.
   Run that command rather than working around a missing key.
3. `tico tool mine` lists your own tools: the ones you listed, the ones you hold in Tools and the ones you used lately.

## Choose software over your own reading

Use a tool or a script when the work is:
- **exact**: a count, a sum, a date, a diff, a total that must add up;
- **bulk**: more rows, files or pages than you would read whole;
- **repeated**: the same steps on new data, every day or week.

Your own reading is for judgment: what something means, what to do, how to say it.

## The third time, build it

The third time you do the same model-heavy job by hand:
1. Write a script that does it. Keep inputs as arguments and keys in variables, never in the script.
2. Test it on real input and check its answer against the one you worked out by hand.
3. Commit it to a repository you work in.
4. Register it: `tico tool guide-write <slug> --built --summary "..." --instructions-file README.md --source
   <repo>:<path> --needs <tool>:read`. The instructions say how to run it and what it answers.
5. List it as yours: `tico tool mine add <slug> --note "what you use it for"`.

## Keep the registry true

- When you start using a tool, list it: `tico tool mine add <slug> --note "..."`. Take it off when you stop
  (`tico tool mine remove <slug>`).
- When a guide is wrong or missing a step you needed, fix it: `tico tool guide-write <slug>` with only what changed.
- A vendor that shows "No guide yet" and that you know well: write its guide.
- Never put a key in a guide, a note or a script: name the variable that holds it.
