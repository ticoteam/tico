# Judge a stuck card

Use this when a requirement has failed twice in one visit and you have been woken to decide.

## Steps
1. Read the card and its evidence: `hub task show <id>`, `hub task answers <id>`, `hub task context <id>`.
2. Read the stage's requirement: `hub requirements <stage_id>`. Note what it asks for, who reviews it and its version.
3. Decide which one action fits:
   - The owner has not supplied it: ask the owner once, on the card (`hub task question`), with the one thing needed.
   - The work was done in the wrong stage: refile to the earlier stage with a comment (`hub task update <id> --step`).
   - The card should wait for a date: hold it (`hub task later <id> --at <date>`), with the reason in a comment.
   - The hold's reason has gone: release it (`hub task unsnooze <id>`).
   - A bot or tool is broken: open a BotOps task (`hub task create --owner botops`) with the card and the error.
   - Another seat should own it: forward it (`hub task update <id> --owner`).
   - The requirement itself is wrong: propose a rule change to the pipeline's owner as a task. Do not change it.
4. Record the action and its reason in a comment on the card.

## Never
- Waive the requirement, or mark it met because the owner is waiting.
- Pick a different owner to make the card move.
