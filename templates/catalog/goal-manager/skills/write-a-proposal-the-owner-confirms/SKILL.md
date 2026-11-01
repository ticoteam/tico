---
name: write-a-proposal-the-owner-confirms
description: "Files one proposal on a goal, KPI or strategy that the owner confirms, after checking what is already pending and rejected. Use it for any change to a goal, a measure, a target or a strategy draft."
---

# Write a proposal the owner confirms

## When to use

- A goal is vague, duplicated, unmeasured or not tied to a bet.
- A KPI definition or target is wrong or missing.
- A bet or a strategy draft needs a change, or a red goal needs a recovery.

## Steps

1. Check first: `hub proposal list --status pending --goal <id>` (or `--kpi <id>`), then `hub proposal list --status rejected --goal <id>`. Read the `decision_note` of each rejected one. Do not repeat a suggestion that a reason rules out, and do not file a second proposal for a problem that already has one pending.
2. Keep to one proposal per problem. A goal with two problems gets two proposals, each with its own reason.
3. Pick the kind from the table in `playbooks/README.md`: `flag`, `goal_wording`, `goal_kpi`, `kpi_definition`, `goal_why`, `recovery`, `kpi_target` or `strategy_draft`.
4. Write the payload to a file in that kind's shape. Use only the numbers the owner gave or the record states. A target the goal does not state is not yours to invent.
5. Write the reason as one sentence: what is wrong, and why now.
6. File it once: `hub proposal create --kind <kind> --goal <id> --payload-file <file> --reason "<one sentence>"`. Add `--kpi` where the kind needs it. The command returns the proposal's id once it is saved; that means it is filed.
7. Do not edit the goal, the KPI or the strategy yourself. The owner confirms. Say so in one line: the proposal waits for their confirmation and is not live until then.

## Checks before you finish

- The pending and rejected lists were read in this run.
- The payload matches the kind's shape in `playbooks/README.md`.
- The reason is one sentence, and it says what is wrong.
- No target, date or threshold came from memory.
- The proposal does not duplicate one that is pending.

## Example

A goal reads "Get better at onboarding." It has no KPI and no bet, and the rejected list has nothing on it.

File a flag first, with `--kind flag --goal <id>`, the payload:

    {"issue": "vague", "note": "No measure says what better onboarding means."}

and the reason "The goal names no result, so no reading can show whether it happened." If the owner wants the words changed, a separate `goal_wording` proposal follows, in the owner's own words where they have them.

The goal is invented for this example.
