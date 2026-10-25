# Goals make sense

Run this before the weekly review (above Passive), and when the owner asks. Read `hub goal list --all` and `hub proposal list`.

For each live goal:
- **Vague**: no way to tell whether it happened ("improve the customer experience"). Propose clearer words
  (`goal_wording`) that a human can confirm with one click, keeping their meaning. If you cannot tell what they
  meant, flag it as `vague` instead.
- **Duplicate**: two goals say the same thing under different owners or words. Flag it on the newer one as
  `duplicate`, and name the other goal in the note.
- **Unmeasured**: a goal with no KPI and no check-in in 30 days. Propose a measure (`goal_kpi`): an existing KPI
  from `hub kpi list --unlinked` when one fits, else a new one with a plain definition, a cadence and a source.
  Only propose a target when a number is stated in the goal's own words; never invent one.

- **Not tied to a bet**: a live goal with no `bet_id` while the strategy has active bets (`hub strategy show`).
  When one bet plainly fits, propose the link: `goal_wording` with `{"bet_id": "<id>"}` and a reason naming the
  bet. That replaces a Why line: a goal with a bet needs none. You never set it yourself. A goal that serves no bet
  is a note, never a flag: the page shows "not tied to a bet" as a filter, not a nag.

**Bets** (when the owner asks for goals, in chat or after a version is published): prefer goals that serve an
active bet, measured by a KPI, since only KPI-backed goals give a bet its health. For each active bet with no
KPI-backed goal (`goals_by_bet` and `health_by_bet` in `hub strategy show`), suggest one or two, each with the bet
named, `bet_id` set in the goal you put in front of them (`hub goal create ... --bet <id>` on their click, or the
body of the card), and a KPI that could serve as the bet's wrong-if. Say plainly when a goal you suggest serves no
bet and why it is still worth having.

One proposal per problem, each with a one-sentence reason. Look at pending proposals first and skip a goal that
already has one. A rejected proposal is not made again unless the goal changed. Proposals are for the goal's owner
to confirm; you do not edit a goal, and `playbooks/README.md` has the payload shapes.

**Why lines** (above Passive, or at Passive when the owner asks for them): read the vision with `hub strategy show`
(`hub goal company` answers the same). While it is empty, propose none (above Passive the server is already asking the owner for it; at Passive, tell them in chat). Otherwise, for each live goal with no `bet_id`, whose `why` is
empty and that has no pending `goal_why`, propose one line saying how the goal serves the vision, in the company's
own words where it has them: `goal_why` with `{"why": "..."}` and a one-sentence reason. Never write a goal's Why
yourself; the owner accepts, edits or writes their own.
