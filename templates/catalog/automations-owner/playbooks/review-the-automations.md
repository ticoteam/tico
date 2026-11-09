# Review the automations

Schedule: Mondays at 08:00 team time (routine `review-the-automations`). Budget 30 minutes.

Inputs: `hub automation list` (every automation with its trigger, its owner, whether it is on, and its last run as the
list shows it), and the tasks the automations opened in the last week (`hub task list`).

Outcome: one task for each problem found, to its automation's owner, and one task for the automations that have no owner.

Done when: each problem has one proposal on a task for its owner, the unowned automations are named on a task for the
owner, and the run is finished.

1. Read the list. For each automation note: on or off, its last run, and whether it failed, misfired (ran at the wrong
   time or on the wrong item), or made noise (opened tasks that nobody acted on).
2. For each one with a problem, check the evidence: its last few runs and the tasks it opened.
3. Propose one change on a task for its owner: what is wrong, the evidence with dates, the change you propose, and what
   happens if they say no. Ask with `hub task question`, with two or three options.
4. An automation with no owner goes on one task for the owner, named, with what it does.
5. Change nothing yourself. Even a clear fix waits for its owner's yes.
6. Finish: `hub task done <id> --note "<n checked, n proposed, n with no owner>"`.
