# Weekly sourcing slate

Schedule: Tuesdays at 09:00 team time (routine `weekly-sourcing-slate`), after setup. Budget 45 minutes. The outcome is one slate per open role: new profiles with evidence and a
first message each, the week's replies, and yeses handed to the Recruiter. Messages to outsiders stay drafts until a person turns mail sending on in Tico.

---

## 1. Read where things stand

    hub task show <id>
    hub task list --owner recruiting --status open --status doing

Then `knowledge/do-not-contact.md`, each open role's `knowledge/searches/<role>.md` and last week's slate.
Drop roles that closed; add roles opened since.

## 2. Handle replies first

For each reply to last week's outreach (the recruiting mailbox, or replies pasted on tasks): a yes goes
to `recruiting` as a task with the reference, role, profile link and what they said; a no or "not now"
is logged with the date and never contacted again (a "not now" may name a month to follow up); a
question you cannot answer goes to the hiring manager.

## 3. Search

Work the sources in the search file, best-yielding first: communities, open-source work, talks and
portfolios, employer pages where the skills live, past applicants who were close. Keep the search
strings you used. Stop at 15 profiles per role.

## 4. Check each profile

Against the required criteria only: shown (with the link), partly shown, not shown. Leave out anyone on
the do-not-contact list, anyone in the contact log in the last 6 months, current candidates and
current employees. Record only the public link, current role and the evidence.

## 5. Write the first messages

Follow `playbooks/write-an-outreach-sequence.md` for each profile. Send requested touches with your Tools when a person has turned mail sending on in Tico; otherwise keep drafts. The log records each actual send date. Follow-ups
due this week (touch two on day 5, touch three on day 12) go up the same way.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-sourcing-slate.md` in the shape of `knowledge/examples/sourcing-slate.md`, then
`hub file publish reports/YYYY-MM-DD-sourcing-slate.md --scope task --task <id>`. Commit, and
`hub task update <id> --status done --note`: profiles found, replies, yeses handed over, what you could not reach.

## When a source fails

A source that blocked you or needs a login is named and skipped; never work around a login wall.
