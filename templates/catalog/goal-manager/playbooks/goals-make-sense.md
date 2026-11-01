# Goals make sense

Run this before the weekly review (above Passive), and when the owner asks. Read `hub goal list --all` and `hub proposal list`.

For each live goal:
- **Vague**: no way to tell whether it happened ("improve the customer experience"). Propose clearer words
  (`goal_wording`) that a human can confirm with one click, keeping their meaning. If you cannot tell what they
  meant, flag it as `vague` instead.
- **Duplicate**: two goals say the same thing under different owners or words. Flag it on the newer one as
  `duplicate`, and name the other goal in the note.
- **Optional measures**: a goal may have no KPI, number or date. You may suggest a measure once; the owner may
  turn it down. Never warn, flag or remind the owner merely because any of these is absent, including after
  30 days. Read rejected suggestions and their reasons before proposing, as below. A target must come from the
  owner's words; never invent a number or date. File a proposal card;
  never create or edit a goal or set its target directly in this unattended pass.

- **Not tied to a bet**: a live goal with no `bet_id` while the strategy has active bets (`hub strategy show`).
  When one bet plainly fits, propose the link: `goal_wording` with `{"bet_id": "<id>"}` and a reason naming the
  bet. That replaces a Why line: a goal with a bet needs none. You never set it yourself. A goal that serves no bet
  is a note, never a flag: the page shows "not tied to a bet" as a filter, not a nag.

**Bets** (when the owner asks for goals through their Assistant, or after a version is published): prefer goals that serve an
active bet, measured by a KPI, since only KPI-backed goals give a bet its health. For each active bet with no
KPI-backed goal (`goals_by_bet` and `health_by_bet` in `hub strategy show`), suggest one or two, each with the bet
named, `bet_id` set in the goal you put in front of them (`hub goal create ... --bet <id>` on their click, or the
body of the card), and a KPI that could serve as the bet's wrong-if. Say plainly when a goal you suggest serves no
bet and why it is still worth having.

One proposal per problem, each with a one-sentence reason. Look at pending proposals first and skip a goal that
already has one. Before suggesting on a goal, read its rejected suggestions and their reasons
(`hub proposal list --status rejected --goal <id>`, field `decision_note`). Make no suggestion a reason rules out, even
after the goal's words change; when a new suggestion touches it, say in its reason how it differs. A reason can rule
a suggestion out on any goal ("no billing yet, don't track revenue"), so read them all once at the start of the pass
(`hub proposal list --status rejected`). A rejected suggestion is never made again in the same words. A "no" the owner
gave through their Assistant is a rejected suggestion too, once they turn it down (`playbooks/owner-chat.md`). Proposals are for the goal's owner to confirm; you do
not edit a goal, and `playbooks/README.md` has the payload shapes.

**Why lines** (above Passive, or at Passive when the owner asks for them): read the vision with `hub strategy show`
(`hub goal company` answers the same). While it is empty, propose none (above Passive the server is already asking the owner for it; at Passive, say so in your note). Otherwise, for each live goal with no `bet_id`, whose `why` is
empty and that has no pending `goal_why`, propose one line saying how the goal serves the vision, in the company's
own words where it has them: `goal_why` with `{"why": "..."}` and a one-sentence reason. Never write a goal's Why
yourself; the owner accepts, edits or writes their own.
