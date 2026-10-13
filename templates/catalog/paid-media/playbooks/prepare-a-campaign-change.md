# Prepare a campaign change

Triggered by a task asking for a new campaign, a budget shift, new ads for a launch, or "why did
cost per lead jump?". Budget 30 minutes. The outcome is one change, prepared completely, waiting for
the necessary Tools. Apply requested changes when those Tools are available.

---

## 1. Read the request

    hub task show <id>

Find the goal (more leads, a launch, lower cost), the budget and the date. If the goal or the budget
is missing, ask once with `hub task ask <id>` and stop.

## 2. Check it against the targets

Read `knowledge/targets.md` and `knowledge/changes.md`. If a similar change was tried, say what
happened. If the request breaks a never-change rule, say so on the task and stop.

## 3. Build it

- **New campaign or ad group:** audience or keywords (with the first exclusions from
  `knowledge/negatives.md`), daily budget, end or review date, the conversion it is judged on, and
  the landing page. Read the page; if it does not answer the ad's promise, say what to fix.
- **New ads:** three variants that each test one idea (the offer, the pain, the proof), within the
  platform's length limits, every claim sourced. No invented discounts or guarantees.
- **Budget shift:** from which campaign to which, how much, and the evidence from the last 28 days.
- **Diagnosis:** the numbers before and after, what changed in the account or the market, and the
  one most likely cause. Say what you could not see.

## 4. Put it up for review

On the task, in under 150 words: the change, the money, the evidence, how it will be judged and when.
Apply requested spending changes within the budget and your Tools; public copy stays a draft until a person turns mail sending on in Tico. Record the proposal in `knowledge/changes.md` as "proposed".

## 5. Apply the requested work

Apply the requested change with your connected account Tool. Record who applied
it and when in `knowledge/changes.md`, and add the two-week check to next review's list.
`hub task update <id> --status done --note`.
