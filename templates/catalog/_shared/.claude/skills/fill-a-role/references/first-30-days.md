# A new bot's first 30 days

How to set up a bot so that the day-30 verdict is about the bot and not about a bad start. The same shape works for a
contractor: narrow first job, close reading, a verdict from results.

## Before day 1: the request to BotOps

Give BotOps these in one message or task:

- the template (folder name from `tico template list`);
- the display name. Use a plain role name: "Freight Claims", not a mascot;
- who it reports to and which group it sits in;
- a link to the role scorecard in `knowledge/roles/<role-slug>.md`;
- the differences from the template, if any, as three lists: add to Owns, remove from Owns, add to "you do not";
- the Tools it needs, and for each one whether it reads or also writes;
- the sending setting for anything that leaves the team. Start on "Never" or "Ask first".

BotOps creates the bot from the template, runs its preflight check and makes it live. The new bot starts in setup and
answers its template's onboarding questions. You do not need to do BotOps's part; if a step fails, BotOps reports it on
the task.

What good instructions look like, so you can read what BotOps wrote: one to two pages; a Role section that includes
"you do not"; an Owns list; the sending rule; how to start and end a run; working style. If the instructions are
longer than two pages, the role is too wide.

## Day 1: one narrow task

Give the bot one task with these properties:

- it is real work from the scorecard, not a test exercise;
- a person could check the result in two minutes;
- it touches each source the role needs at least once, so a missing connection shows today;
- nothing leaves the team.

Write the task so "done" is unambiguous: what to produce, where to put it, and what to do if a source cannot be read.

## Days 1 to 3: read three runs end to end

Open each of the first three runs and read all of it, not just the answer. Look for:

| Look for | Good | Fix |
|----------|------|-----|
| Did it answer first? | The result is in the first lines | Add "answer first" to working style |
| Is each fact cited? | Source and date beside each number | Add the citation rule; check the Tool returns dates |
| Gaps | "Could not read X" stated plainly | Never accept "not found" where it could not look |
| Scope | Stayed inside Owns | Tighten "you do not" |
| Length | Short and scannable | Give a format in the instructions |
| Asks | One clear ask, only when needed | Name what it may decide alone |

When a run is wrong, change the instructions so the next run is right. Do not just correct the output on the task: a
corrected task teaches nothing. If you manage the bot, send the instruction change to BotOps or as a task for the
bot's next run. Model, name and description you may change yourself (`tico bot model`, `tico bot update`).

## Day 4 to 7: the first routine

After three clean runs in a row:

- add one routine from the template's `first_routine` or playbooks (`tico automation guide` shows the format);
- create a KPI for each scorecard outcome (`tico kpi create`) and link it to its goal (`tico kpi link`);
- log the baseline reading with its source and date.

## Day 7 check

Pass when all are true:

- three clean runs in a row;
- every source in "Needs" was read at least once without an error;
- `auto:<bot>:failed_runs_7d` is zero, or each failure has a known cause that is fixed;
- nothing left the team that should not have.

On a pass, sending may move from "Never" to "Ask first", or from "Ask first" to "On their own" for the message types
you have seen go out correctly. On a fail, fix the cause and repeat the check in seven days. Two failed day-7 checks is
a Replace.

## Weeks 2 to 4

- Read the bot's `memory/` once a week. Wrong lessons learned early are the usual cause of a slow drift.
- Read one run a week in full.
- Do not widen the role before day 30.

## Day 30 review page

Publish one page (`tico page publish reports/role-<slug>-day30.html --title "<Role>: day 30" --task <task id>`). Layout:

1. **Verdict line.** "Keep", "Fix", "Narrow" or "Replace", and one sentence why.
2. **Outcomes table.** One row per scorecard outcome.

   | Outcome | Target | Reading | Period | Source | Colour |
   |---------|--------|---------|--------|--------|--------|
   | | | | | | green / yellow / red / gray |

   Green: at target. Yellow: inside tolerance. Red: outside. Gray: not read. Gray is never counted as a pass.
3. **Bot numbers.** `tasks_done_7d`, `first_response_min`, `approval_rate_30d`, `failed_runs_7d`, `cost_7d`, each
   beside last week's value. A falling approval rate means people keep saying no to what it asks: read those asks.
4. **Cost.** Spend against the ceiling on the scorecard (`tico usage show`).
5. **Three runs read in full.** Links, and one line on each.
6. **What changes.** The one fix, the lines cut from Owns, or the successor.
7. **Next review date.**

## Verdict rules

| Verdict | When | What you do |
|---------|------|-------------|
| Keep | Every outcome green or yellow and not getting worse; guardrail holds; cost inside ceiling | Next review in 90 days; consider widening one line |
| Fix | An outcome is red and the cause is found in the instructions, a Tool, effort or model | Change one thing; review in 14 days; one round only |
| Narrow | Some outcomes green, others red with no fixable cause | Cut Owns to the green part; take the rest back to "try the cheaper answers" |
| Replace | Red after a fix round, a guardrail broken, or two failed day-7 checks | Archive with a successor for open tasks; try another template, or write the "bot can't" reason |

**The order of fixes.** Try them in this order, one at a time, because each is cheaper than the next:

1. Instructions: scope, definition of done, format.
2. Tools and access: can it read what it needs?
3. Reasoning effort: raise it if the work is wrong; lower it if only the cost is wrong.
4. Model.
5. Narrow the role.
6. Archive and replace.

**Archiving.** Ask BotOps. Name the successor that takes the bot's open tasks. Archiving can be undone; say so on the
page. The default limit is 3 archives in any 7 days for a manager bot.
