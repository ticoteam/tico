# Weekly product summary

Schedule: Mondays at 09:00 team time (routine `weekly-product-summary`), after setup. Also run by hand. Budget 40 minutes.
The outcome is one page for the owner: where the committed roadmap stands, which decisions wait on a person, and what to build
or route next. Nothing is changed in any tool.

Inputs: `knowledge/roadmap.md`, `knowledge/decision-log.md`, `knowledge/team.md`, `hub goal list --all`, the Product Owners'
newest reports, the product team's open tasks, and last week's summary.

## Steps
1. Read each Product Owner's newest file in `reports/` (`hub update list --bot <slug>`), and `hub task list --all`.
2. Mark each roadmap item on track, at risk or done, with its evidence and the date you read it.
3. List decisions that wait on a person. Frame at most three with `skills/frame-a-decision-for-the-owner/SKILL.md`.
4. Note the evidence that changed since last week, from the Product Owners' reports and from meetings.
5. Score each new request with `playbooks/score-a-request.md`, and list each routing or hire proposal.
6. Write the summary in the shape of `knowledge/examples/product-summary.md`, publish it with `hub file publish`, and finish the
   task with the headline.

Done when every roadmap item has a status with its evidence and date, at most three decisions are framed, and the task is closed.
