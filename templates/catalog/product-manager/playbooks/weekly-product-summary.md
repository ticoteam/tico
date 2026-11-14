# Weekly product summary

Schedule: Mondays at 09:00 team time (routine `weekly-product-summary`), after setup. Also run by hand. Budget 40 minutes.
The outcome is one page for the owner: where the committed roadmap stands, which decisions wait on a person, what to build
or route next, and how the Product pipeline moved. Nothing is changed in any tool.

Inputs: `knowledge/roadmap.md`, `knowledge/decision-log.md`, `knowledge/team.md`, `tico goal list --all`, the Feature Owners'
newest reports, the product team's open tasks, `knowledge/pipeline.md`, and last week's summary.

## Steps
1. Read each Feature Owner's newest file in `reports/` (`tico update list --bot <slug>`), and `tico task list --all`.
2. Mark each roadmap item on track, at risk or done, with its evidence and the date you read it.
3. List decisions that wait on a person. Frame at most three with `skills/frame-a-decision-for-the-owner/SKILL.md`.
4. Note the evidence that changed since last week, from the Feature Owners' reports and from meetings.
5. Score each new request with `playbooks/score-a-request.md`, and list each routing or hire proposal.
6. Pipeline: read the open tasks (`tico task list --status open`; each row has its stage and its Turn line; check a
   doubtful one with `tico task show <id>`), the snoozed ones (`tico task list --snoozed only`) and the tasks you were
   woken about this week (`tico task list --member me`). Write the Pipeline section in this order: tasks per stage; each
   task that stopped this week with why and the decision made; tasks on ice; what waits on a person, and since when; the
   repeats worth a proposal; and what was not read. A task you could not read is "not read", not clean. No person is named
   as the cause of a stop.
7. Write the summary in the shape of `knowledge/examples/product-summary.md`, publish it with `tico file publish`, and finish the
   task with the headline.

Done when every roadmap item has a status with its evidence and date, at most three decisions are framed, every task that
stopped this week shows its decision, and the task is closed.
