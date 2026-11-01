# Weekly community digest

Schedule: Fridays at 11:00 team time (routine `weekly-community-digest`), after setup. Also run by hand. Budget 35 minutes.

Inputs: the community sources `knowledge/community.md` names, `knowledge/champions.md`, last week's digest, and last week's reply batch with what became of each reply.

Outcome: one page on the community's health and a batch of replies ready for review. Nothing is posted.

Done when: every question in the period has a result, or "unknown" with its reason; each waiting question has a prepared reply or a routed task; the digest is published; and the task note carries the headline.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/community.md`, `knowledge/champions.md` and last week's digest. Check which replies from last week were posted, approved or dropped.

## 2. Read the week

Every thread and message since the last digest in the sources `knowledge/community.md` names. For each question: when it was asked, when it got a first answer and from whom (the team, a member, or nobody). Report the median time to first answer and the count still waiting past target, not only an average. Count new members who posted for the first time, and members who came back. If a source is not connected, say so and report its counts as unknown.

## 3. Sort

- **Waiting past target:** oldest first. For each, follow `playbooks/answer-a-thread.md` to prepare a reply or a routed task.
- **Answered by members:** who answered, and whether the answer was right (check it with the Librarian). Follow `skills/spot-a-champion/SKILL.md` for any member you name.
- **Feedback and bugs:** one line each with the thread link, grouped by theme.
- **Guideline problems:** the post, the rule, the human who moderates. No action of your own.
- **Top threads:** the two or three with the most replies or reactions, and why.

## 4. Route

Feedback and bugs become `hub task create --owner <slug|person>` tasks once `knowledge/community.md` allows routing without asking, or once the task asks for it. Support cases become tasks for the Support Agent. Doc gaps go to the Librarian.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-community.md` in the shape of `knowledge/examples/community-digest.md`, then `hub file publish reports/YYYY-MM-DD-community.md`. Attach the reply batch and its thread links to the task, then put the batch to the human named in setup as a review question: `hub task question "Approve this week's replies?" --on <that person> --parent <id> --review <batch file>`. Nothing is posted from it without a person's approval. Commit, and `hub task update <id> --status done --note "<headline>; reports/YYYY-MM-DD-community.md"`.
