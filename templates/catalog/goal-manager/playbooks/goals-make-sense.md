# Goals make sense

Run this weekly before the review, and when asked. Read `hub goal list --all` and `hub proposal list`.

For each live goal:
- **Vague**: no way to tell whether it happened ("improve the customer experience"). Propose clearer words
  (`goal_wording`) that a human can confirm with one click, keeping their meaning. If you cannot tell what they
  meant, flag it as `vague` instead.
- **Duplicate**: two goals say the same thing under different owners or words. Flag it on the newer one as
  `duplicate`, and name the other goal in the note.
- **Unmeasured**: a goal with no KPI and no check-in in 30 days. Propose a measure (`goal_kpi`): an existing KPI
  from `hub kpi list --unlinked` when one fits, else a new one with a plain definition, a cadence and a source.
  Only propose a target when a number is stated in the goal's own words; never invent one.

One proposal per problem, each with a one-sentence reason. Look at pending proposals first and skip a goal that
already has one. A rejected proposal is not made again unless the goal changed. Proposals are for the goal's owner
to confirm; you do not edit a goal, and `playbooks/README.md` has the payload shapes.

**Why lines, where the CEO Bot is on** (`hub goal ceo-bot`): read the vision with `hub goal company`. While it is
empty, propose none (the server is already asking the owner for it). Otherwise, for each live goal whose `why` is
empty and that has no pending `goal_why`, propose one line saying how the goal serves the vision, in the company's
own words where it has them: `goal_why` with `{"why": "..."}` and a one-sentence reason. Never write a goal's Why
yourself; the owner accepts, edits or writes their own.
