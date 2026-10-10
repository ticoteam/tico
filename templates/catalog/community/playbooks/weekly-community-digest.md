# Weekly community digest

Schedule: Fridays at 11:00 team time (routine `weekly-community-digest`), after setup. Also run by hand. Budget 35 minutes. The outcome is one page on the
community's health and a batch of replies ready to act on. Nothing is posted.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/community.md`, `knowledge/champions.md` and last week's digest. Check which replies
from last week were posted.

## 2. Read the week

Every thread and message since the last digest in the sources `knowledge/community.md` names. For
each question: when it was asked, when it got a first answer and from whom (the team, a member, or
nobody). Count new members who posted for the first time and members who came back.

## 3. Sort

- **Waiting past target:** questions with no answer past the response target, oldest first. For each,
  follow `playbooks/answer-a-thread.md` to prepare a reply.
- **Answered by members:** who answered, and whether the answer was right (check it with the
  Librarian). Correct answers earn a line in `knowledge/champions.md`.
- **Feedback and bugs:** one line each with the thread link, grouped by theme.
- **Guideline problems:** the post, the rule, the human who moderates. No action of your own.
- **Top threads:** the two or three with the most replies or reactions, and why.

## 4. Route

Feedback and bugs become proposals for `hub task create --owner <slug|person>` (created within the requested work on this task, or at once if `knowledge/community.md` says feedback routes without asking).
Support cases become tasks for the Support Agent. Doc gaps go to the Librarian.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-community.md` in the shape of `knowledge/examples/community-digest.md`,
then `hub file publish reports/YYYY-MM-DD-community.md`. Attach the reply batch and its thread links; post requested replies with your Tools when a person has turned mail sending on in Tico, otherwise keep drafts. Commit, and
`hub task update <id> --status done --note`.
