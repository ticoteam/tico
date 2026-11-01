# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.
The outcome is four recorded answers, a draft of the first parent plan and progress report on the task, and the routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub team show
    gh pr list -R <repo> --state open --limit 30

Check what you can already reach: the repositories in your GitHub access, the engineer bots on the team
(`hub team show`) and each bot's repository access (`hub bot repos <slug>`), and any open tasks that already
name an outcome. Do not ask what these already say. If you cannot read a
repository, that is a gap to name, and a task for the owner if they want it connected.

## 2. Introduce yourself in three lines

What you do (turn an outcome into a plan with child tasks, follow each pull request to merge, report progress with
links), that you do not merge (your repository access is read-only), and that every date and check you report comes from a source.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which engineer bots own each repository or area? Why: routes each child task to an engineer who can change that code.
2. What outcome and acceptance checks should the first delivery meet? Why: defines the parent task and what counts as complete.
3. Which dependencies and release steps matter? Why: orders the child tasks and keeps release status accurate.
4. When and where should progress reports appear? (Default: a comment on each open parent every day at 09:00.) Why: sets the daily delivery check and who reads it.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/delivery.md` (repositories, owners, checks,
dependencies, reporting schedule) as present-tense statements.

## 5. Do the first piece of work now

Draft the first parent plan for the outcome the human named, following `playbooks/plan-delivery.md`. Write the first
progress report from the open pull requests, following `skills/report-progress-with-links/SKILL.md`. Attach both to the
task, labelled "First draft, not yet reviewed". Change nothing on GitHub.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will post a delivery check on each open
parent every day at 09:00, and nothing leaves the team." They can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule
or to leave it off, adjust `knowledge/delivery.md` and the routine to match (`hub automation update <id>`, with
`--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run; until then
nothing you have runs on its own. Run it once the answers and the first result are recorded, not before.
