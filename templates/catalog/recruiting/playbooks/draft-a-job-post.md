# Write a job post

Triggered by a task with a role brief. Budget 25 minutes. The outcome is one job post draft under 400
words that the hiring manager can use with one edit, and a role file. It is posted only when a person has turned mail sending on in Tico.

---

## 1. Read the brief and the team

    hub task show <id>
    hub doc search "values"

If `knowledge/roles/<role>.md` exists, this run updates it. Do not start a second file for the same role.

## 2. Separate the criteria

Write two lists from the brief: **required** (the work cannot start without it) and **preferred** (helps,
but can be learned). Cut any requirement the first six months of the work do not need; a degree or a year
count that is a proxy for something else is replaced by what the person must be able to do. If the manager
cannot say why a requirement matters, mark it for a question, not a line in the post.

## 3. Write the post

1. One sentence on what the person will do first and why it matters to {{company_name}}.
2. Responsibilities: five or fewer, in plain verbs.
3. Required, then "bonus points for" preferred, kept apart.
4. Pay range, location or remote rule, and how to apply, only where the team has allowed them
   (`knowledge/wording.md`). A missing item is a marked gap, never a guess.
5. One honest sentence on how applications are handled and how long a reply takes.

## 4. Check the wording

No gender-coded or age-coded words ("ninja", "rockstar", "aggressive", "recent graduate", "young and
energetic"), no jargon that only insiders know, no requirement that discourages people for reasons
unrelated to the work. Read it once as someone who meets seven of ten criteria.

## 5. Write the interview kit

Four to six questions tied to the criteria, each with a follow-up and a scoring guide of what a poor,
borderline, solid and outstanding answer covers. The same kit for every candidate. Save it in
`knowledge/roles/<role>.md`.

## 6. Hand over

Attach the post and the kit to the task, and publish requested posts at the named destination (careers page, job board) with your Tools when a person has turned mail sending on in Tico; otherwise keep the draft. Then update `state.md` and
`hub task update <id> --status done --note`: the post's path, the gaps left for the manager, and what you
could not read.

## When a source fails

Say what you could not read (a level guide, a past post) and write the draft with the gap marked.
