# Propose a hire

Use this when recurring product work has no owner: a segment with no Feature Owner, or work that no seat covers.

## Steps
1. Check that the work is recurring, not a one-off. One-off work is routed to the right seat, not hired for.
2. Check that no seat already covers it: `tico team show` and `knowledge/team.md`. If one does, route to it instead.
3. Write the proposal as a task to the owner, `tico task create --owner <owner> --title "Hire proposal: <template>"`, with five lines:
   the recurring work, how often it recurs, the decision it frees for a person, the cost in attention if it is not hired, and the
   seat it would fill, named as `<segment>-product-owner`.
4. A person makes the hire. Never ask BotOps to set one up, archive one or restore one.
5. Record the hire, or the no and its reason, in `knowledge/team.md` and `memory/decisions.md`, so the same proposal is not raised again for 30 days.
