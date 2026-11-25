# Review the tools

Schedule: Mondays at 09:00 team time (routine `review-the-tools`). Budget 20 minutes. Read
`skills/manage-tools-and-keys/SKILL.md` first.

Inputs: `tico tool index` (every tool with its status, who can use it and whether it needs a person), `tico access
stats --days 7` (access requests: how many reached people, the share granted at once, the median time to grant), `tico
tool find` (the Tools registry: every tool's guide, who uses it, when it was last used), and your open tasks (`tico task
list --owner me`).

Outcome: one task of your own for each problem, with the one thing a person must give on it, and nothing for a tool
that works.

Done when: each problem below has one open task, no problem has two, and the run is finished.

1. Read the index. Note each API tool whose status is **Key rejected**, **Check failed**, **Key expired**, **No key** or
   **Expires in N days**, each tool nobody can use, and each pair of tools or keys that look like one service stored twice
   (same service and variable, names that differ only by a bot).
2. For a failing check, run `tico tool check <id>` once to confirm it is still failing. A passing one needs nothing.
3. For each problem still there, check your open tasks: one already covers it, leave it.
4. Otherwise file one task for yourself (`tico task create --owner me --title "<tool>: <problem>"`): what is wrong, since
   when, and the one fix. Work it in its own run: a new key is a card for the bot whose key it is
   (`tico credential request`, which pauses the task until a person saves it); a level, a merge or a check path is one
   question on the task (`tico task ask`) with the tool's link.
5. Change no access yourself: every grant, level, merge and delete of a tool or key is a person's. The registry's words
   are yours to keep (step 7).
6. Read `tico access stats --days 7`. An open request older than a day is a problem for step 4 (its card waits on a
   person); a free-text access task you own is asked again with `tico access request` and closed.
7. Curate the registry (`tico tool find`, then `tico tool guide <slug>` for each one you touch):
   - a vendor agents use (its `used_by` above 0) that shows "No guide yet": write its guide from its activity and
     learnings (`tico tool guide-write <slug> --summary ... --instructions-file ...`);
   - two guides for one tool (a built tool that wraps a vendor's CLI, two slugs for one script): keep the better one,
     fold the other's words into it, archive the other (`tico tool guide-archive <slug>`);
   - a built tool nobody used in 60 days: ask its owner on a task of yours (`tico task ask`) and archive it only when
     they agree or do not answer in a week;
   - a wrong or empty category: fix it (`tico tool guide-write <slug> --category data`).
8. Finish: `tico task done <id> -m "<n tools, n working, n problems filed; access: n to people, n% at once, median
   n min to grant; registry: n guides written, n archived>" --version N`.
