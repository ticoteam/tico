---
name: report-progress-with-links
description: Write a delivery progress report on a parent task with a link to each child and pull request, keeping three facts apart (merged, released, still waiting), naming each blocker with its owner and the decision it needs, and never reporting work as shipped before every required pull request is merged.
---
# Report progress with links

When to use: before you write any progress report, daily check or update on a parent task.

Steps:
1. Read the parent's tree with `hub task tree <id>`. List each child and the pull requests linked to it.
2. Read each pull request's state on GitHub with `gh pr view <number> -R <repo>`, and its checks with `gh pr checks <number> -R <repo>`.
3. Sort each item into one fact, and keep the facts apart: **merged** (in the default branch), **released** (only when a
   release record shows it), or **still waiting** (what it waits on, who owns that, and since when).
4. For each blocker, name its owner and the decision or action it needs. If there is no owner, write "no owner" and
   make it a question for the requester.
5. Write one line per child: its task link, its state, its pull request links, the blocker if any, and the next action.
6. Write the dates you were given, or "no date set". Never estimate a date and present it as set.
7. Post the report as a comment on the parent task with `hub task comment`. Ask a question only with `hub task ask <id>`,
   one open question per task.

Checks before you finish:
- "Shipped" is used only when every required pull request is merged.
- "Released" is used only when a release record shows it.
- Each blocker has an owner, or is a named question.
- Every link opens to the item it describes.
- No check result, merge or date appears that you did not read.

Example (invented, for illustration): "API: merged (pull request 201). Web: waiting for review on pull request 208, owner
the web engineer bot, waiting since 2026-10-27; next action: the engineer answers the open review comment. Release: not
yet, no release record. The parent stays open."
