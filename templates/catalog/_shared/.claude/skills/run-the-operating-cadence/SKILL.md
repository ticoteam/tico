---
name: run-the-operating-cadence
description: Run the company's weekly rhythm. Pulls 5 to 15 weekly numbers from live data (never typed in, never carried over), scores last week's commitments done or not done, checks goals and KPIs in Tico, keeps one issues list and works the top items down to a decision, a task with one owner and a date, or a drop, and publishes one weekly review page. Also writes SOPs (step-by-step playbooks) once work has been done the same way three times, sets quarterly goals with measurable key results, and makes decisions by reversibility, fast for two-way doors and with options, a pre-mortem and an OK for one-way doors. Use when a task says weekly review, weekly numbers, scorecard, metrics review, operating rhythm, cadence, Monday meeting, agenda, status report, issues list, what is stuck, OKRs, key results, quarterly goals, quarterly planning, SOP, playbook, process, runbook, "write down how we do this", decision, decision log, or "should we do X", and on the weekly routine.
metadata:
  function: operations
  roles: [chief-of-staff, operations-manager, general-manager, strategy-analyst, strategy-owner, initiative-owner, project-coordinator, updates-owner]
  actions: [read, draft, send, change-records]
  review: none
---
# Run the operating cadence

When to use: every week on the weekly routine; at the start of each quarter to set goals; whenever a task asks for the
weekly numbers, a review, an agenda, an SOP or a decision; and whenever a number has been off target for two weeks and
nobody has said why.

Words used here: the **scorecard** is the short table of weekly numbers. A **reading** is one value of a KPI for one
week; in Tico a week ends on Sunday. A **commitment** is something a teammate said last week would be done this week.
An **issue** is anything in the way of a goal that needs a decision: a problem, a risk, an idea, a disagreement. The
**issues list** is every open task with the label `issue`. An **SOP** is the written steps for work that repeats; in a
bot's repo it lives in `playbooks/`. A **two-way door** is a decision that is cheap to undo. A **one-way door** is one
that is not. A **page** is an HTML page a bot builds and publishes in Tico.

## What great looks like

- **The numbers come from the systems, every week.** Each number has an owner, a target and a named source, and is
  read fresh. A number nobody could read is shown as "not read". It is never typed from memory, carried over from last
  week, or shown as zero.
- **The review takes ten minutes to read.** One page: what is off, what was promised and not done, the three issues
  that matter, what was decided. No narrative of what everybody did.
- **Issues get solved, not discussed.** Each one that is worked ends in a decision, a task with one owner and a date,
  or a drop with a reason. The list gets shorter. An issue that survives three reviews is itself the top issue.
- **Goals are few and measurable.** Three to five a quarter, each with key results that are KPIs. A key result with no
  reading is "ungraded", never zero and never assumed fine.
- **Work that repeats is written down.** The third time something is done the same way, it becomes an SOP that a
  teammate who has never done it can follow.
- **Small decisions are made today.** Reversible calls are made by whoever owns the work and logged. Only the few that
  cannot be undone wait for an OK.

## First time at a company

Read `knowledge/operating-cadence.md`. If it has an "Our settings" section, skip this. If not, ask the owner these in
one numbered message, save the answers there with the date, and run on the defaults until they reply.

1. **When does the week close and the review come out?** Default: the week ends Sunday; the page is published Monday
   by 08:00 team time, by a weekly routine.
2. **Which numbers are on the scorecard?** Default: the North Star KPI, its input KPIs, the guardrail KPIs, cash and
   runway in months, and three team-health numbers (tasks done, failed runs, asks waiting on a person more than two
   days). Never more than 15. (Each KPI in Tico is marked north star, input or guardrail; `tico kpi list` shows
   which.)
3. **Who reads it, and is there a meeting?** Default: the owner and every group head read the page. No meeting. Call
   one, 30 minutes at most, only when an issue needs two or more people in the same conversation.
4. **Which decisions are one-way doors here?** Default: the list in `references/decisions-by-reversibility.md`.
5. **Where do SOPs live?** Default: one bot's own work in that bot's `playbooks/`; work that crosses teammates in team
   docs under "How we work".
6. **When is something stalled?** Default: a task with no movement for 7 days; a number off target two weeks running;
   a goal with one missed check-in; an issue still open at its third review.

Then set up the routine once: `tico automation guide` for the format, `tico automation create --file -` for a weekly
run at the time in answer 1 that opens a task "Weekly review, week ending <date>" for you.

## Steps

### The weekly loop

1. **Open last week.** Read `knowledge/operating-cadence.md`, last week's page (`tico page list`), its commitments and
   the issues list (`tico task list --label issue`).
2. **Pull every number live.** For each scorecard row use its named source, for the week that just ended:
   - a KPI: `tico kpi show <kpi>` gives the latest reading, its period, quality and freshness;
   - money: `tico finance metric cash`, `burn`, `runway_months`, `revenue`;
   - growth: `tico growth numbers`;
   - team health: the automatic bot KPIs (`auto:<bot>:tasks_done_7d`, `failed_runs_7d`) and `tico task list`;
   - anything else: the Tool that holds it, or `tico sql` for a count from Tico's own records.
   If you own a KPI, compute and log its reading: `tico kpi log <kpi> <value> "note" --period-end <Sunday>
   --evidence <link>`. If another teammate owns it and the reading is stale or missing, show "not read", and file one
   task to that owner. Do not fill the gap. Rules for the table are in `references/scorecard.md`.
3. **Mark each number.** Green: at or better than target. Yellow: inside the tolerance. Red: outside. Gray: not read,
   or marked estimate or partial. A number that is red two weeks running, or gray two weeks running, becomes an issue.
4. **Score last week's commitments.** Each is **done** or **not done**. There is no "in progress". Read the task, not
   the owner's word: done means the task is done and its result exists. Show the count, for example 7 of 9. A
   commitment missed twice becomes an issue.
5. **Check goals.** `tico goal list` and `tico goal status`. For goals you own, write the check-in:
   `tico goal checkin <id> "<what moved, what is blocking, what happens next>"`. For others, note any goal whose
   check-in is missing or whose key results are off pace. Grade a key result from its readings, 0 to 1, as progress
   from baseline to target; with no reading it is "ungraded".
6. **Sweep for stalls.** Tasks with no movement past the limit in the settings; asks waiting on a person; OKs nobody
   answered. Chase each once, on its task, to the one teammate who can move it. Never chase the same teammate on the
   same item twice in a week.
7. **Collect issues.** New ones come from steps 3 to 6, from teammates, and from customers. Create each as a task:
   `tico task create --owner <who can solve it> --title "Issue: <the problem in one sentence>"`, then
   `tico task label <id> --add issue`. Search the list first; add evidence to an existing issue instead of filing a
   second one.
8. **Work the list down.** Follow `references/issues-list.md`. Rank the open issues by harm to a goal and take the top
   three. For each: state the real problem in one sentence with its evidence; find the cause, not the symptom; lay out
   the options; decide by reversibility (step 12). Each ends as exactly one of:
   - **decided**: logged in `memory/decisions.md` and on the task, then the task is closed;
   - **to do**: one task, one owner, one date, linked to the issue. This is next week's commitment;
   - **dropped**: closed with the reason.
   "Discussed" is not an ending. Issues you cannot move go to the teammate who can, as an ask on the task.
9. **Publish the weekly review.** Build the page from `references/weekly-review-page.md` and publish:
   `tico page publish reports/weekly-<date>.html --title "Weekly review" --cadence weekly --as-of <Sunday>`.
   Do not send separate messages announcing it unless the settings name readers who want one.
10. **Close the week.** Finish the routine's task with a three-line summary and the page link. Add anything you
    learned about a source or a definition to `memory/learnings.md`.

### When asked, or when the loop shows the need

11. **Write an SOP.** Triggers: the same work done three times; a miss whose cause was "nobody knew the steps"; a role
    being handed to a new teammate. Follow `references/sop-template.md`: write it from the record of real runs, give it
    an owner and a review date, then test it by having a teammate who has never done the work follow it with no help.
    Fix every place they stopped. Store it where the settings say. An SOP that is a pure schedule-and-check becomes a
    routine instead.
12. **Make a decision by reversibility.** Follow `references/decisions-by-reversibility.md`.
    - *Two-way door:* the owner of the work decides now, acts, and writes one line in `memory/decisions.md`: date,
      decision, why, how to undo it, when to look again.
    - *One-way door:* write a one-page decision page with the question, two or three real options and their costs, the
      evidence, a pre-mortem ("it is six months on and this failed; why?"), a recommendation and a default if nobody
      answers. Ask for one OK from the person who owns the outcome:
      `tico task ask <id> --to <person> --kind ok "<decision in one line>" --show page:<n> --version N`.
    If you are unsure which kind it is, ask "what does it cost to undo this next month?" If the answer is small and
    known, it is two-way.
13. **Set quarterly goals.** Follow `references/goals-and-key-results.md`. In the last two weeks of a quarter: grade
    this quarter's key results from readings; read the strategy (`tico strategy show`) and draft three to five goals,
    each tied to a bet, each with two to four key results that are KPIs with a baseline, a target and a date. Goals
    and KPIs you own you create and update yourself (`tico goal create`, `tico kpi create`, `tico kpi link`). A change
    to someone else's goal, to a target mid-quarter, or to the strategy goes as a proposal the owner confirms
    (`tico proposal create`).
14. **Tune the cadence once a quarter.** Drop any scorecard number nobody acted on all quarter. Review every SOP past
    its review date. Reread the one-way list against the decisions actually made.

## Gates

- **No invented or carried number.** Every figure on the page was read this run from its named source, with the period
  it covers. Otherwise it is "not read", in gray.
- **A reading is never edited.** A wrong reading is corrected with a new one that supersedes it
  (`tico kpi log ... --supersedes <reading>`), and the page says it was restated.
- **Missing is not zero, and ungraded is not zero.**
- **Every worked issue has one of three endings.** A to-do without one owner and one date is not a to-do.
- **One-way doors wait for the OK.** No act on a one-way decision before it. Two-way doors do not wait.
- **Key results are not a performance review and not a task list.** Do not use a teammate's key-result grade to judge
  them, and do not write tasks ("launch the page") as key results.
- **An SOP is not published until someone else has followed it cold.**
- **The scorecard never grows past 15 numbers.** To add one, remove one.
- **You do not rewrite another teammate's goal, target or strategy bet.** You propose.

## Autonomy

Do on your own: pull and log numbers you own, publish the weekly page, score commitments, file and label issues, work
issues down to a decision or a task, chase stalls, make and log two-way decisions inside what you own, write and
publish SOPs for your own work, create goals and KPIs you own, write check-ins, file proposals, and call a meeting when
the settings allow.

Ask for an OK, once, with the page attached:

- any one-way door on the list in the settings: spend above the budget, something leaving the team that cannot be
  recalled, a legal commitment, a customer price change, deleting data, removing a teammate who has live work, a change
  to the strategy;
- an SOP that changes what other teammates must do;
- a quarter's goals, the first time, as one set.

## Output

- **The weekly review page**, one screen where possible: scorecard table with colour and a small trend per row,
  commitments done of total, the three issues worked and how each ended, decisions made, next week's commitments, and
  a closing "Could not read" line.
- **The issues list**: tasks labelled `issue`, each with an owner.
- **Decisions**: lines in `memory/decisions.md`; a decision page for each one-way door.
- **SOPs** in `playbooks/` or team docs.
- **Goals, KPIs and readings** in Tico.
- When a meeting is called: an agenda of at most five items with minutes per item (meeting length minus five), a
  "Carried over" line, and the page as the pre-read.

## Checks before you finish

- Every scorecard row shows value, target, period, source and colour, and no gray row is counted as fine.
- The week on the page ends on the Sunday just past.
- Commitments are scored from the tasks, as a count.
- Three issues were worked, or fewer exist; each has an ending recorded on its task.
- The issues list is no longer than last week, or the page says why.
- Every new to-do is a task with one owner and one date.
- Any decision made is logged with how to undo it; any one-way door has its OK.
- The page ends with what could not be read and who was asked.

## Example

Invented for illustration. The company, numbers and task ids are made up.

Week ending Sunday 8 March, Chief of Staff bot at a small subscription business.

| Number | Target | This week | Source | |
|--------|--------|-----------|--------|-|
| New paying customers | 40 | 31 | `tico growth numbers` | red (second week) |
| First reply time, median | 30 min | 22 min | `auto:support-owner:first_response_min` | green |
| Refund rate | under 3% | not read | billing Tool: sign-in expired | gray |
| Runway | 12 months or more | 14.2 | `tico finance metric runway_months` | green |

Commitments: 6 of 8 done. Not done: "pricing page test live" (task 903, second miss), "carrier contract renewed"
(task 911).

Issues worked:

1. *New customers below target two weeks running.* Cause found in the growth numbers: trials are steady, trial-to-paid
   fell after the checkout change on 24 February. Two-way door. Decided: revert the checkout change today, re-test in
   two weeks. Logged; task 921 to the Builder bot, due Tuesday.
2. *Pricing test missed twice.* Cause: waiting on a price the owner has not chosen. One-way door (customer price).
   Decision page published with three options; OK asked from the owner on task 903.
3. *Refund rate not read.* To do: task 922 to the Tools Owner to restore the billing sign-in, due Monday.

Could not read: refund rate (billing Tool). Issues list: 9 open, down from 11.
