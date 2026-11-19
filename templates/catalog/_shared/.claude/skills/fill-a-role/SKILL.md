---
name: fill-a-role
description: Fill a role with a bot first. Proves from the record that the work exists, writes a role scorecard (mission, 3 to 5 measurable outcomes, owns, never does), tries the cheaper answers first (stop the work, a routine, a skill for a bot the team already has), picks a Tico role template, gets the bot created through BotOps, sets it up over a first week and judges it at day 7 and day 30 by results. Goes to a contractor or a human hire only when a bot truly cannot do the work, with the reason written down and an OK. On that path it writes the job post, a structured interview kit with one four-level scale, evidence summaries of applicants, scorecards and a debrief pack, and a person decides. Use when a task says hire, hiring, recruit, headcount, open a role, job description, job post, interview, candidate, contractor, freelancer, "we need someone to", "who should own this", add a bot, new bot, staff this, or when the same work keeps landing on nobody.
metadata:
  function: people
  roles: [head-of-people, recruiter, recruiting-coordinator, sourcer, hr-generalist, botops, chief-of-staff, operations-manager, general-manager]
  actions: [read, draft, send, spend, change-records]
  review: professional
---
# Fill a role

When to use: when work has no owner, when a person or a bot says "we need someone to do X", when a task asks for a
hire, a job post, an interview plan, a contractor or a new bot, and when a bot you manage is not delivering and the
role has to be filled again.

Words used here: a **team** is the company in Tico. A **teammate** is anyone on it: a **person** or a **bot** (an AI
employee that picks up tasks, uses skills and works through Tools). A **role** is a named set of outcomes one teammate
owns. A **role template** is one of Tico's ready-made bot roles in `templates/catalog/<template>/`, each with a
`card.yaml` saying what the bot owns and never does. **BotOps** is the built-in bot that creates, checks and archives
bots. **The person above** is the nearest person up your reporting line. A **routine** is an automation that runs on a
schedule. A **role scorecard** is the one-page definition of the role this skill writes.

On `review: professional`: the bot path needs no outside reviewer. An employment lawyer checks three things only: an
offer letter or employment contract before it is sent, a contractor agreement before it is signed, and whether a
contractor is correctly classified when the work is ongoing or full time.

## What great looks like

The owner of a Tico company does not want to hire people. Filling a role means a bot first.

- **Outcomes before titles.** The role is defined by three to five results, each with a number, a date and the record
  the number is read from. "Handle support" is a title. "First reply inside 30 minutes on 90% of tickets, read from the
  support Tool weekly" is an outcome.
- **The cheapest answer that works.** Before any new teammate: stop the work, then a routine, then a skill for a bot
  already on the team, then a new bot. A person comes last.
- **A bot is judged like a hire.** It gets a narrow first task, a first week of close reading, and a verdict at day 30
  from its results. A bot that misses is fixed, narrowed or replaced. It is never left running and unread.
- **A human hire needs a written reason.** The reason names the exact part of the work a bot cannot do and why. The
  role is split so the person does only that part and a bot does the rest.
- **People are assessed on evidence, by people.** Every candidate gets the same questions, scored on the same scale,
  with scores written before anyone talks. The bot runs the process. A named person makes each decision.

## First time at a company

Read `knowledge/filling-roles.md`. If it has an "Our settings" section, skip this. If not, ask the owner these in one
numbered message, save the answers there with the date, and work on the defaults until they reply.

1. **Who says yes to a new bot, and to a human hire?** Default: a manager bot adds a bot to its own group without
   asking, inside Tico's weekly limit. The owner OKs every human hire and every contractor.
2. **What may a new bot cost?** Default: the team's default model and run budget, and no paid Tool the team does not
   already have. Anything above that goes in the ask.
3. **Which reasons count for "a bot can't do this"?** Default: the five in `references/bot-or-person.md` (physical
   presence, a licence or signature, a counterpart who requires a person, a tool whose terms forbid automation, a real
   trial that failed). Nothing else.
4. **Where do we employ people, and do we show pay?** Default: United States, remote. Every job post shows a pay range.
5. **May any tool score or rank applicants?** Default: no. The bot summarises evidence and a person decides.
6. **How long is the trial for a new teammate?** Default: a check at day 7 and a verdict at day 30, for bots and
   contractors. For an employee, check-ins at 30, 60 and 90 days.

## Steps

### A. Decide whether there is a role

1. **Prove the work exists.** Read the last 30 days of the record, not the request: tasks (`tico task list`, or
   `tico sql` for counts), meetings (`tico meeting search --since <date>`), team docs (`tico doc search`) and KPIs
   (`tico kpi list`, `tico goal list`). Look for tasks with no owner or that sat still, asks that waited on a person,
   the same request arriving more than once, and KPIs with no owner. Write down each instance with its task id or
   link, how often it recurs, and what broke when it was late. If you cannot find five real instances or one outcome
   with a number behind it, there is no role yet. Say so in the task and stop.
2. **Write the role scorecard.** Use `references/role-scorecard.md`. Mission in one sentence, three to five outcomes
   (number, date, source), what the role owns, what it never does, what it needs (inputs, Tools, access), who it
   reports to, and a cost ceiling. Save it as `knowledge/roles/<role-slug>.md`. Every later step reads this file.
3. **Try the cheaper answers, in this order.** Stop at the first that covers the outcomes.
   1. *Stop the work.* If nobody reads the output, or the outcome serves no goal in `tico goal list`, propose
      dropping it.
   2. *A routine.* If the work is the same check on a schedule, write a routine (`tico automation guide`, then
      `tico automation create --file -`) and give it an owner.
   3. *A bot the team already has.* Run `tico team show` to list every bot with its template and who it reports to,
      and `tico bot show <slug>` for one bot's detail. If one bot's "Owns" list already borders this work, add the
      work to it: a skill, a wider "Owns" line, a routine. One bot with a clear edge beats two with a blurred one.
   4. *A new bot.* Go on to step 4.

### B. Fill it with a bot

4. **Pick the template.** Run `tico template list`. Read the `card.yaml` of each likely template: `summary`, `owns`,
   `never`, `onboarding`, `first_routine`. Match on what it owns and never does, not on its title. If two fit, take
   the narrower. If none fits, take the nearest and write the differences as three lists for BotOps: add to Owns,
   remove from Owns, add to "you do not".
5. **Run the bot-or-person test.** Go line by line through the scorecard's outcomes with `references/bot-or-person.md`.
   Mark each line "bot", "bot with a Tool we must connect" or "person, because <one of the accepted reasons>". Then
   split: the bot takes every line it can, and only the remainder goes to step 10. Most roles split.
6. **Get the bot created.** Bots do not create bots directly. Use the route that fits you:
   - *You are a manager bot and the new bot reports to you.* Ask BotOps in a message
     (`tico message send botops "<request>" --needs-action`) with the template, the display name, who it reports to
     and a link to the scorecard. Tico allows this by default up to 3 creates in any 7 days.
   - *Anyone else.* File `tico task create --owner botops --title "Build a bot: <name>"` with the same four things. If
     a person asked for the role, cite their request.
   - *Refused.* If BotOps answers `report_cap` or any other refusal, Tico has already put a task in front of the
     person above. Wait for it. Never try another route around a refusal.
7. **Run the first week.** Follow `references/first-30-days.md`. In short: one narrow task whose result a person could
   check in two minutes; read the first three runs end to end; when a run is wrong, fix the bot's instructions, not
   the task; after three clean runs add one routine from the template's playbooks. Sending outside the team stays off
   or on "Ask first" until the day-7 check passes.
8. **Wire the outcomes to numbers.** Each scorecard outcome becomes a KPI with an owner and a source
   (`tico kpi create`, then `tico kpi link` to the goal it serves). Tico also keeps five numbers for every bot
   automatically, named `auto:<bot>:<metric>`: `tasks_done_7d`, `first_response_min`, `approval_rate_30d`,
   `failed_runs_7d` and `cost_7d`. Read them with `tico kpi show`.
9. **Judge it at day 7 and day 30.** Build the review page in `references/first-30-days.md`: each outcome against its
   target from the KPI readings, the five automatic numbers, cost against the ceiling, and three runs you read in
   full. Give one verdict:
   - **Keep.** Every outcome is at target, or inside the tolerance on the scorecard and moving the right way.
   - **Fix.** An outcome missed and the cause is in the instructions, a Tool, the reasoning effort or the model. Change
     one thing, in that order, and review again in 14 days. One fix round only.
   - **Narrow.** It does part of the role well. Cut "Owns" to that part and take the rest back to step 3.
   - **Replace.** It missed after a fix round. Ask BotOps to archive it with a named successor for its open tasks, and
     either try a different template or go to step 10 with the trial as the written evidence.
   A reading that is missing is "not read". It is never a pass and never a zero.

### C. Only when a bot can't: a contractor or a person

10. **Write the reason and ask.** One paragraph: the scorecard lines a bot cannot do, which accepted reason applies to
    each, the evidence (task ids from a failed trial, the licence rule, the counterpart's own words with a link), and
    what the bot half of the role already covers. Choose contractor or employee with the test in
    `references/contractor.md`: bounded work with a deliverable goes to a contractor; ongoing work under the team's
    direction is employment. Publish the role page (see Output), then put one OK to the person named in the
    settings:
    `tico task ask <task id> --to <person> --kind ok "Open a search for <role>: <reason in one line>" --show page:<n> --version N`.
    Nothing public happens before the OK.
11. **Contractor.** Follow `references/contractor.md`: a statement of work priced by deliverable, acceptance tests
    taken from the scorecard, an end date, the least access that does the job, and a dated classification check.
12. **Human hire.** Follow `references/human-hire.md`, start to finish: pay range, job post, where to look, evidence
    summaries of applicants, the structured interview kit, scorecards written before any discussion, the debrief pack,
    the person's decision, the offer, and check-ins at 30, 60 and 90 days. In Tico, the Recruiting seat, the applicant
    list and the Hire action belong to a person. You prepare everything and they click.
13. **Close the loop.** Update `knowledge/roles/<role-slug>.md` with who fills the role, from what date, and the
    verdicts so far. Add one line to `memory/decisions.md`. If a trial taught you something about a template, add it
    to `memory/learnings.md`.

## Gates

- **No scorecard, no teammate.** Nothing is created, posted or signed until the scorecard has outcomes with numbers
  and sources.
- **No human path without a written reason and an OK.** The reason must be one of the accepted reasons in
  `knowledge/filling-roles.md`. "It would be faster", "it feels important" and "we always had a person do this" are
  not reasons.
- **You never score, rank, recommend or reject a human candidate.** You report evidence against each stated criterion
  as shown, partly shown or not shown, with the line you relied on quoted. A named person decides who moves forward,
  who gets an offer and who does not.
- **Protected characteristics are never a signal and never recorded.** Age, race, colour, sex, pregnancy, gender
  identity, sexual orientation, religion, national origin, disability, genetic information, veteran status, and any
  others the local law adds. If an application shows one, it does not go into your summary. Do not infer any of them
  from a name, a photo, a gap or a graduation year.
- **Every job post shows a pay range**, and passes the checks in `references/human-hire.md` before it leaves the team.
- **No tool scores applicants** unless the settings say so and the local rules in `references/human-hire.md` have been
  read at the source that week.
- **A person signs.** Offers, employment contracts, contractor agreements and anything else that binds the team.
- **A refusal is final for you.** `report_cap` or any other refusal goes to the person above, once.
- **A bot is never deleted to hide a miss.** It is archived, with a successor named for its open tasks, and the review
  page stays published.

## Autonomy

Do on your own: read the record, write the scorecard, propose dropping work, write routines, add a skill or a wider
"Owns" to a bot you manage, ask BotOps for a new direct report inside the weekly limit and the cost ceiling, run the
first week, create and link KPIs, change a report's model or reasoning effort, publish the day-7 and day-30 pages, give
a Keep, Fix or Narrow verdict, and ask BotOps to archive a bot you manage when a successor is named. On the human
path, do all the preparation: drafts, evidence summaries, kits, scheduling, chasing scorecards, the debrief pack.

Ask for an OK, once, with the page attached:

- a new bot you would not manage, one over the weekly limit, or one above the cost ceiling;
- opening any contractor or human search, and publishing a job post (it leaves the team and is public);
- each decision about a person: advance, reject, offer. You then send the message the person approved;
- any spend above your budget, and any paid Tool the team does not have;
- anything signed.

## Output

- `knowledge/roles/<role-slug>.md`: the role scorecard, kept current.
- **A role page**, when an OK is needed: the scorecard, a table of the outcomes marked bot or person with the reason,
  the template picked and the expected cost. Publish with `tico page publish reports/role-<slug>.html --title "Role:
  <name>" --task <task id>`.
- **The review page** at day 7 and day 30: one row per outcome with target, reading, source and date, coloured green,
  yellow, red or gray for "not read"; the five automatic numbers; cost against ceiling; the verdict in the first line.
- On the human path: the job post, the interview kit, one evidence summary per applicant, the debrief pack, the
  30-60-90 plan. A table beats prose for all of them.

## Checks before you finish

- The scorecard has three to five outcomes and each has a number, a date and a source that exists today.
- Step 3 was really tried: the page says which cheaper answers were considered and why each was not enough.
- Every "person" line names one accepted reason and its evidence.
- The bot's first task was narrow, and you read three runs in full before adding a routine.
- Each outcome is a KPI with an owner, and none is marked green from a missing reading.
- The verdict is one of Keep, Fix, Narrow, Replace, and the next review date is on the task.
- On the human path: pay range shown; same questions and scale for every candidate; no score, rank or recommendation
  written by you; every dated legal fact was re-read at its source this month.
- Nothing left the team, and nobody was added or removed, without the OK it needed.

## Example

Invented for illustration. The company, the numbers and the task ids are made up.

The Operations Manager bot at a small furniture seller sees that freight damage claims keep missing the carrier's
deadline: in 30 days, 11 damaged deliveries (tasks 412 to 447), 5 claims filed late and refused.

- **Scorecard**, saved to `knowledge/roles/freight-claims.md`. Mission: recover what carriers owe us. Outcomes: every
  claim filed inside the carrier's own deadline; claim value recovered at or above 70% by quarter end; carrier invoices
  matched to quotes weekly. Never does: accept a settlement, or pay a carrier.
- **Cheaper answers.** Stopping is wrong (money is owed). A routine can spot new damage reports but cannot assemble a
  claim. No current bot owns carriers.
- **Template.** `logistics-coordinator`: its card owns claims and carrier invoices and never pays.
- **Bot-or-person test.** Filing, chasing and matching invoices: bot. Photographing the damaged pallet at the dock:
  person, because it needs physical presence. Split: the warehouse lead, already on the team, uploads photos to the
  task. No hire.
- **Created.** The bot asks BotOps in chat: template `logistics-coordinator`, name "Freight Claims", reports to
  Operations Manager, scorecard linked. First task: file the claim for task 447 only.
- **Day 30.** 14 damaged deliveries, 14 claims filed in time, 9 paid. Cost inside the ceiling. One run missed a
  photo and the instructions were fixed on day 4. Verdict: **Keep**. Next review in 90 days.
