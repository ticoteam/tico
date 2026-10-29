# Which playbook a run needs

Read the first line of the work, then the playbook it names. Read `hub strategy show` first on every run.

- A task "Draft the strategy", or the owner asking for the strategy in chat: `draft-the-strategy.md`.
- A task "Bet N: may be wrong", filed by the server when a bet's wrong-if trips, at every level: `bet-tripped.md`.
- A note from the Librarian that a market entity contradicts a bet: one `strategy_draft` proposal adding the entity to that bet's
  `cites` (so a change to it trips the bet), unless one is already pending. Nothing more.
- The routine "KPI pass": `kpi-pass.md`, ending with the status pass (and, above Passive, the check-ins; at level 3, a decision for
  each red or stalled goal).
- At level 3, a bot's question to you, a clash between Heads' tasks or a broken bot: `acts-on-its-own.md`.
- The routine "Weekly goals review": `weekly-goals-review.md`.
- A task or a message about one KPI or goal is that KPI or goal only: `hub kpi show <id>` or `hub goal show <id>` first, then the
  matching playbook.
- The owner's chat: `owner-chat.md`.
