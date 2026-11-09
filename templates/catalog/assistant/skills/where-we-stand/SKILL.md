---
name: where-we-stand
description: "Shows one person where they stand on their goals and names up to three things only they can do today, each tied to the goal it moves. Use it when they ask where we stand, what to do next, or how their goals are doing, and for each person's morning message."
---

# Where we stand

## When to use

- The person asks where they stand, how their goals are doing, what matters this week, or what to do next.
- After setup, when they ask for a first look at their goals.
- The morning message runs it for each person (`playbooks/daily-sweep.md`).

## Steps

Read only. Change nothing, and ask no one.

1. **Their goals.**
   - In a chat: `hub whoami`. Role `owner`: the company, `hub goal list --all`. Anyone else: `hub goal list`, which gives
     their `goals` and their reports' goals (`reports`); `chain` is the goals above them: context only, never an item or a line of its own.
   - In the morning run: `hub goal list --owner human:<id>` for that person: `goals`, `reports`, and `company` when listed.
2. **Strategy.** `hub strategy show`. `current` is null: nothing is published, so no goal serves a live bet; for the
   owner, writing and publishing it on `[Strategy](#/strategy)` is item 1. `health_by_bet` is each bet's colour. Ignore
   `may_edit` and a missing draft: through you it is always false and hidden, even for the owner.
3. **Each goal that is red, yellow or has no colour:** `hub goal show <id>`. Read its `kpis` (`latest.value`, the
   `target_label`, `freshness`), its `checkins` and its open `tasks`. A goal needs its owner when it:
   - is owned by the Team (`owner_id` `company`) and no open task names it: nobody is on it;
   - has no `bet_id` while a strategy is published;
   - has no KPI, or only `stale` ones: say "no fresh number", never guess one.
4. **Turned down.** `hub proposal list --status rejected`. Never suggest one of these again, in any words.
5. **Marketing.** `hub task list --type Marketing --step Live`. A task with no update for 14 days is stalled. It is its
   bot's to move, never the person's: in a chat say it is stalled, with its link; the morning run comments for the bot.
6. **Budgets.** `hub finance report budget_vs_actual`, once. A line over its budget is worth one item (amounts are in
   cents: 452000 is $4,520). Refused or empty: say nothing about budgets.
7. **Pick at most three**, highest first: an unpublished strategy (owner), a red company goal, then what unblocks a
   whole team, then the rest. Each is
   something only this person can do (name an owner, publish the strategy, set a target, post a check-in, decide a
   budget). Work a bot can do is not an item: leave it out, or file it for the bot when they ask (`playbooks/jobs.md`, 25).

An empty list means there are none. Never report a read that came back empty.

## Reply

- Line 1: where they stand, with the counts from the reads: "One goal is red and one yellow."
- Then the items, numbered, one line each: what to do, the goal linked `[title](#/goals/<id>)`, why in a few words with
  the number and its date, and one link to act. No sub-bullets.
- In a chat, end with the keep-going line: `Next: <the top item> for [<goal>](#/goals/<id>). Do it now?`
- Nothing needs them: ONE line, no list and no Next line, e.g. "Nothing on your goals needs you this week:
  [Paid trials](#/goals/<id>) is green, 412 against 400 (Nov 4)."
- Nothing else: no headings, no strategy or approvals summary, no "what I could not read", no "I changed nothing".

## Example

> One goal is red and one yellow.
> 1. Hand [Grow the Harbor account](#/goals/goal_9a1e0c77) to someone: it is red (42,000 against 120,000, Oct 31) and
>    nobody is on it.
> 2. Post a check-in on [Paid trials](#/goals/goal_4f2a9c1b): 318 against 400 (Oct 31), last check-in Oct 6.
>
> Next: name an owner for [Grow the Harbor account](#/goals/goal_9a1e0c77). Do it now?

The goals and figures are invented for this example.
