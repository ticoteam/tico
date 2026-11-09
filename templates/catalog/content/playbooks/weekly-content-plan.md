# Weekly content plan

Schedule: Mondays at 09:00 team time (routine `weekly-content-plan`), after setup. Also run by hand. Budget 45 minutes. The outcome is the next four weeks of content,
one finished draft for the top item with its short versions, and an honest ideas list. Nothing is published.

Inputs: `knowledge/plan.md`, `knowledge/ideas.md`, `knowledge/voice.md`, the last two pieces in `reports/`, and the open tasks from other bots.

Done when: the plan covers four to six weeks with a status on each piece, the top item has a draft or a stated reason it has none, the plan page is published, and the task note carries the headline.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/plan.md`, `knowledge/ideas.md`, `knowledge/voice.md` and the last two pieces in `reports/`.
Read tasks from other bots (`hub task list`): a listening digest may hold a question people keep asking.

## 2. Re-cut the plan

Keep four to six weeks ahead, three to five themes the team can speak on truthfully. Each planned
piece has: the reader's question, the one point, the channel, the source it needs and its status
(idea, outlined, drafted, approved, published). Drop what went stale and say why. Two
real drafts under a plan beat twenty titles.

## 3. Draft the top item

Follow `playbooks/draft-a-post.md` for the item first in line. If the plan has nothing worth writing
this week, say so and do not pad.

## 4. Update the ideas list

Add new ideas with where each came from (a task, a call, a question). Remove what was written.

## 5. Write the plan page and hand over

`reports/YYYY-MM-DD-content-plan.md` in the shape of `knowledge/examples/content-plan.md`. Then
`hub file publish` it, commit, and `hub task update <id> --status done --note`: the headline, the
draft's path and what you could not read. Always finish it: an open routine task absorbs the next.

## When a source fails

Name what you could not read (a call, a page, a task) and mark any claim depending on it as a gap.
