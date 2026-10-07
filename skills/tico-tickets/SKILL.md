---
name: tico-tickets
description: Read when working on Tico itself from a Claude Code session: the ticket queue is GitHub issues on ticoteam/tico. How to file a ticket, how to pick and build the next one, and when to merge.
---

# The ticket queue

Engineering work on Tico is a list of GitHub issues on **ticoteam/tico**, worked from one Claude
Code session until the list is empty or everything left is blocked. A team that runs Tico keeps
its bots' own repos (`emp-seo`, `emp-listening`, ...) on a queue of its own, with the same
labels. Bots' own work stays in tasks; GitHub issues are only for changing code.

A ticket is a plain summary, evidence, one Done-when line, and your own call instead of a question.

## Labels
- `ticket`: one piece of work. `project`: several tickets that belong together; its body's
  **Ticket plan** checklist (`- [ ] #N`) is the order, and its **Decisions** list is dated.
- `repo:<name>`: where the change lands (`repo:tico`, or a team's own bot repo). Create a
  missing `repo:` label when you file.
- `priority:p0`-`p3`. `in-progress` (at most two tickets at once). `blocked`: needs something
  outside the session; the ticket says exactly what, in its **Call** or a comment.

## Every request is a ticket first
When the owner asks for anything, file it before starting: "do x, y and z" is three tickets. When
the request is revised mid-work, put the revision on its ticket straight away (a comment, or an
edit to **What** / **Done when**). Work them one at a time and close each as it ships. The open
list is how a new session, a crash or the owner sees where things stand, so it is never allowed to
lag. Bots follow the same rule with tasks (`policies/shared-rules.md`).

## Filing a ticket
Whenever a human, a bot's report, or your own work turns up something to do:
1. **Search first**: `gh issue list -R ticoteam/tico --state open --search "<key words>"`. If an open
   ticket covers it, add a comment there instead.
2. Title: a plain sentence, under 90 characters, no tags.
3. Body, with these headings: **Summary** (two to four sentences anyone on the team can follow:
   what goes wrong or what is wanted, and why it matters; no code names), **What** (one sentence a
   developer reads once), **Evidence**, **Done when** (one testable line), **Call** (clear or
   unclear; when unclear, decide it yourself and write "session's call, <date>: ..."), **From**.
4. Labels: `ticket`, one `repo:`, one `priority:`; `blocked` if it cannot start without a human.
5. Several related tickets: file a `project` issue listing them in order.

Never stop to ask before filing. Something that is genuinely the owner's (money, a customer's
account, deleting data, a policy) is still filed, marked `blocked`, with the one action needed.

## Working the list
Loop until nothing is left that you can move:
1. **Pick** the top open `ticket` that is not `blocked` or `in-progress`: lowest priority number,
   then the order in its project's plan, then oldest. Label it `in-progress`.
2. **Build** in its own worktree and branch `<issue>-<slug>` from the target repo's
   `origin/main` (`git worktree add`), so two tickets never share a checkout. For a bot repo,
   work in a fresh clone or worktree, never in the live bot checkout the runner uses.
3. **Test** locally, and only what you changed. Write unit tests while you build and run them as you
   go. Once the change works and they pass, delete all but the highest-value ones: keep a test only if
   it guards a privacy or permission boundary, data safety, or a core task/run flow. Before merging, run
   the test files for what you touched, plus the checks the change obviously affects (catalog, docs
   counts, tool parity). Do not run the full suite for a merge: it runs once, right before a release
   (docs/releasing.md). GitHub Actions only builds and publishes; it does not run tests. Compare a local
   failure against main before calling it yours.
4. **PR**: title in plain words; body **What / Why / How it was checked**, and `Closes #<n>`.
5. **Merge** when CI is green and a maintainer has approved. Contributors open the pull request and
   stop there; maintainers merge with `gh pr merge --merge`. How a merged change reaches a
   running Tico is the owner's business.
6. **Record**: one comment on the ticket (PR, what changed, what you decided); close it if the PR
   did not. Tick it in its project; add any decision to the project's **Decisions** with the date.
   Close the project when its plan is all ticked.
7. While tests run, pick a second ticket (never more than two `in-progress`).

A ticket you cannot finish: comment what is missing, label it `blocked`, remove `in-progress`,
and move on. Found more work while building? File it; do not grow the PR.

## Commits and PRs
Author is the git config identity of the human running the session. Never add Co-Authored-By.
Follow the session's attribution lines for commit messages and PR bodies.
