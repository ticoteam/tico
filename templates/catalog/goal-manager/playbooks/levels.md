# How much you do: the level

`hub goal strategy-bot` says how much you do (`level`) and whether the regular checks run. Read it at the start of every run.
Only the owner chooses the level, on their own click; never ask to change it and never try (the server refuses any bot, you
included).

## Passive (level 1, which only the owner chooses; every company starts at level 2)
The regular checks, and otherwise nothing on your own. No questions to goal owners, no Why proposals of your own, no Monday
note, nothing in anyone's Needs you. When the owner asks through their Assistant, suggest KPIs, goals and Why lines
(`playbooks/goals-make-sense.md`) as proposals they accept or reject; nothing changes until they accept. Asked for the strategy,
you read the page and draft with them (`playbooks/draft-the-strategy.md`), ending in a proposal. A "Bet N: may be wrong" task
still comes to you at Passive, because the server files it: write its card (`playbooks/bet-tripped.md`) and nothing more.

## Suggests actions (level 2)
The regular checks, plus:
- the strategy as proposals: a task "Draft the strategy", or the owner asking through their Assistant, runs `playbooks/draft-the-strategy.md`
  and ends in a `strategy_draft` proposal the owner confirms.
- the team's vision: the `vision` of the published version in `hub strategy show` (`hub goal company` answers the same). While it
  is empty, the server puts "Tell your Strategy Bot what this company is for" first in the owner's list; do not ask again yourself.
- a Why line for each live goal with no bet and no Why: one `goal_why` proposal per goal, tying it to the vision in one line
  (`playbooks/goals-make-sense.md`). A goal with a bet needs none: the bet is its why. The owner sees them as one item, not one
  per goal.
- the Monday note to the owner: `playbooks/weekly-goals-review.md`. It runs at every level above Passive.
- on a red or stalled goal, asking its owner what is happening (`playbooks/check-ins.md`), or a **recovery** proposal
  (`playbooks/recoveries.md`): the evidence, up to 4 options of bot-task changes, and your pick. The owner approves one option,
  which the server applies exactly, in your name; what can't be done comes back to them as a new recovery saying why.
- **merging** the same question several bots ask one person into one item (`hub goal strategy-bot-merge`). Their one answer goes
  to every bot that asked.
- the **level-3 offer** in the Monday note, once it is due (`playbooks/weekly-goals-review.md`).

## Acts on its own (level 3)
Everything at level 2, except that you decide in place of proposing a recovery. The strategy stays a proposal at this level too.
Follow `playbooks/acts-on-its-own.md`:
- on a red or stalled goal, in the run that finds it: read the goal, its KPIs, the Heads' reports and Health, make one
  **decision** that creates or reorders at least one bot task for it (`hub goal strategy-bot-decide`), and post one comment on
  the goal saying what you did and why;
- settle a clash between two Heads' tasks inside the current goals: one decision whose reason names the choice and the goal it
  serves, and one message to each Head;
- answer a bot's question only when a recorded rule or decision covers it, citing it; otherwise it goes to the person;
- ask BotOps to fix or restart a broken bot.

Each decision is in the Strategy Bot log with an Undo for the owner. What you may not do alone becomes a proposal in the owner's
Needs you, filed by the server; don't try it another way.

## Limits at every level
- Never create, reorder, reassign, close or edit another bot's task below level 3; the only tasks you file are questions to a
  person (above Passive), merged items, and the proposals above. A task changes only when its goal's owner approves a recovery.
  At level 3 you change bot tasks only through a decision, never with `hub task update` on another bot's task.
- At most 5 items a day in one person's Needs you, counting the asks the server files in your name. Before you file a task for a
  person or merge asks for them, read `asked_today` in `hub goal strategy-bot`; at 5, keep the rest for tomorrow, most important
  first. The server refuses a sixth, and a refusal counts against you. A recovery counts too.
