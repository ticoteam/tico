---
name: write-a-botops-fix-task
description: "Turn a setup finding into one task for BotOps: confirm the finding still holds, name the fix and the check that shows it worked, and file it as a subtask of the finding's task."
---
# Write a BotOps fix task

When to use: a finding of kind setup, such as a computer on an older release, a bot with no fallback model, a bot stuck
in setup, a stuck task worktree, or a repository a computer cannot fetch.

Steps:
1. Confirm it holds: `hub health-monitor findings`. If the key is gone, write "Cleared on its own" on the task and stop.
2. Write the title as the fix in a few words, for example "Update the computer to the current release".
3. Write the body in four short lines:
   - Seen: what the finding says, with its numbers and how many times it was seen.
   - Fix: the fix from the finding, in plain words.
   - Check: what a person can read on Health that shows it worked (a release shown as current, a status that clears).
   - Key: the finding's key, so the fix can be traced.
4. File it under this task: `hub task create --owner botops --parent <this task> --title "<title>" --body "<body>"`.
   One task per finding, not one per bot or computer.
5. Put the BotOps task's id in this task's note, and mark this task done.

Checks before you finish:
- The finding was confirmed in this run.
- The body names a check a person can read on Health.
- The body holds the finding and the fix. No task text, message text or a person's words are copied in.
- There is one BotOps task for this finding.

Example (invented, for illustration): title "Update the computer on the older release". Seen: a computer runs an
older release and holds two queued bots' work, seen three times since Tuesday. Fix: update it from the computers page.
Check: Health shows the computer on the current release and the queued work starts. Key: runner_outdated for that
computer.
