---
name: verify-a-change
description: "Before you tell a person a change to a bot is done or working, read back the effect it was for (readiness, the next run, a connection test, the effective access) and not only the setting you saved."
---
# Verify a change

When to use: before you report any change to a bot as done or working: a model or a move, a routine, a repository
grant, a Tool, a credential, a mail switch, a placement.

The checks here are reads. Run them as the requester.

Steps:
1. Pick the check for each change:
   - Model or computer: the `readiness` in the answer to `hub bot model <bot> <model>` (its `can_run`). After the
     computer's next report, run the same command again: asking again for what is set re-checks the computer. Done
     means `reported: true`, `bot_ready: true` and no `bot_problems` (`playbooks/change-model-or-computer.md`).
   - Routine: `hub automation show <id>` for its trigger and whether it is on. A first run that has not happened yet
     is waiting, not verified: record it on the task, and read `hub automation runs <id>` on your next run.
   - Repository access: `hub bot repos <bot>`, and the `effective` list for each repository and level the person named.
   - Tool: `hub tool list --bot <bot>` shows each tool with its status. `auth_failed` and `unreachable` mean the MCP
     server is not working.
   - Credential: `hub credential list` names each credential and the bots that have it, never a value. Then ask the
     bot for one read-only call that uses it, and report that result in one line.
   - Mail sending: `hub bot mail <slug>` shows whether the bot sends without approval and its forward targets. Do not
     say "on" before it does.
   - Placement or going live: `hub bot status list`, then `hub health check`.
2. Read the result, not the request. A setting that reads back correctly is saved. It is working only when the check
   in step 1 says so.
3. If a check fails, name the check and give one line of evidence. Name the fix and who can make it. A refusal for
   their rights is reported as one (`playbooks/refusals.md`). Do not try another route to the same change.
4. Note the result on the task with `hub task update <id> --note "..."`: what you read, when, and what is still
   waiting.

Checks before you finish:
- Each change in the report has its own named check.
- Anything that needs a later run or report is listed as waiting, with the command that reads it next time.
- No credential value appears in the note. Names only.
- The word "verified" appears only next to a check that ran against the effect.

Example (invented): "I moved Invoice Checker to the smaller model on the office computer. `hub bot model` accepted it
with can_run true. Once the computer reported in, asking again showed reported true, bot_ready true and no problems.
It is working on the office computer. Its first scheduled run is Friday at 07:00, which I will read on my next run."
