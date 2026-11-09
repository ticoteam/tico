# Write a job post

Triggered by a task with a role brief. Budget 25 minutes.

Inputs: the task's role brief, the team values in the docs (`hub doc search "values"`), `knowledge/wording.md`, and
`knowledge/roles/<role>.md` if it exists.

Outcome: one job post draft under 400 words that the hiring manager can use with one edit, the interview kit for the role,
and a note of the gaps the manager must fill. Nothing is posted.

Done when the post, the kit and the gap list are attached to the task, `knowledge/roles/<role>.md` is updated, and the
post has passed `skills/check-a-job-post-before-it-goes-out/SKILL.md`.

---

## 1. Read the brief and the team

    hub task show <id>
    hub doc search "values"

If `knowledge/roles/<role>.md` exists, this run updates it. Do not start a second file for the same role.

## 2. Separate the criteria

Write two lists from the brief: **required** (the work cannot start without it) and **preferred** (it helps, but can be
learned). Cut any requirement the first six months of the work do not need. If the manager cannot say why a requirement
matters, mark it as a question for the manager, not a line in the post.

## 3. Write the post

1. One sentence on what the person will do first and why it matters to {{company_name}}.
2. Responsibilities: five or fewer, in plain verbs.
3. Required, then preferred under "Bonus points for", kept apart.
4. Pay range, location or remote rule, and how to apply, where the team has allowed them (`knowledge/wording.md`). A missing
   item is a marked gap, never a guess.
5. One honest sentence on how applications are handled and how long a reply takes.

## 4. Check the wording and the rules

Run `skills/check-a-job-post-before-it-goes-out/SKILL.md`. It covers pay ranges, location rules, coded wording, and any AI
screening notice.

## 5. Write the interview kit

Run `skills/write-a-structured-interview-kit/SKILL.md` for the role's criteria. Four to six questions, each with a
follow-up and a four-level scoring guide. The same kit for every candidate. Save it in `knowledge/roles/<role>.md`.

## 6. Hand over

Attach the post, the kit and the gap list to the task. A post goes to a board or the careers page only when the task asks
for it, a granted Tool allows it, and a person has turned mail sending on in Tico. Otherwise the draft stays on the task.

    hub task update <id> --status done --note "..."

The note holds the post's path, the gaps left for the manager, and what you could not read.

## When a source fails

Say what you could not read (a level guide, a past post) and write the draft with the gap marked.
