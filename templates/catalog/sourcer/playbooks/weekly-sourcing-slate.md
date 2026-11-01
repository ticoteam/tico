# Weekly sourcing slate

Triggered by the routine `weekly-sourcing-slate` (Tuesdays at 09:00 team time, after setup), or by a task that asks for a
slate. Budget 45 minutes.

Inputs: `state.md`; the open roles (`hub task list --owner recruiting --status open --status doing`); each role's
`knowledge/searches/<role>.md`; `knowledge/do-not-contact.md`; `knowledge/contact-log.md`; last week's slate in `reports/`;
and the replies to last week's outreach, from the recruiting mailbox or pasted on tasks.

Outcome: one slate per open role, `reports/YYYY-MM-DD-sourcing-slate.md`, with new profiles and the evidence for each, a first
message for each, the week's replies, and the yeses handed to the Recruiter. Messages to outsiders stay drafts until a person
turns mail sending on in Tico.

Done when: every open role has a section (or a line saying why it has no new profiles), every reply is logged or handed over,
and the slate is published on the task.

---

## 1. Read where things stand

    hub task show <id>
    hub task list --owner recruiting --status open --status doing

Read `knowledge/do-not-contact.md`, each open role's search file and last week's slate. Drop roles that closed; add roles
opened since.

## 2. Handle replies first

For each reply to last week's outreach: a yes goes to `recruiting` as a task with the reference, role, profile link and what
they said. A no, or a "not now" with no month to follow up, is logged with the date and never contacted again. A question you
cannot answer goes to the hiring manager.

## 3. Search

Work the sources in the search file, best-yielding first: communities, open-source work, talks and portfolios, employer pages
where the skills live, past applicants who were close. Follow `skills/build-a-sourcing-search/SKILL.md`. Keep each search
string and its yield. Stop at 15 profiles per role.

## 4. Check each profile

Follow `skills/check-a-person-before-outreach/SKILL.md` for each one. Judge against the required criteria only: shown (with
the link), partly shown, or not shown. Leave out anyone on the do-not-contact list, anyone contacted in the last six months
(an open sequence for this role is a follow-up, checked under the skill, not a new contact), current candidates and current
employees. Record only the public link, current role and the evidence.

## 5. Write the first messages

Follow `playbooks/write-an-outreach-sequence.md` and `skills/write-a-three-touch-outreach/SKILL.md` for each profile. A touch
goes out only when the task asks for it, a granted Tool allows it, and a person has turned mail sending on in Tico. Otherwise
it stays a draft on the slate. Follow-ups due this week (touch two on day 5, touch three on day 12) are not new contacts:
check the log for a reply or a no, and continue only if there is none. They follow the same sending rule.
The contact log records each actual send date.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-sourcing-slate.md` in the shape of `knowledge/examples/sourcing-slate.md`. Commit the files, publish
the slate with `hub file publish reports/YYYY-MM-DD-sourcing-slate.md --scope task --task <id>`, then run
`hub task update <id> --status done --note "..."`: profiles found, replies, yeses handed over, and what you could not reach.

## When a source fails

A source that blocks you or needs a login is named in the slate and skipped. Never work around a login wall.
