# Inbound sweep

Schedule: weekdays at 09:00, 11:00, 13:00, 15:00 and 17:00 team time (routine `inbound-sweep`), after setup.
Also run by hand. Budget 20 minutes. The outcome: every inbound lead that arrived since the last sweep is sorted,
has its reason written down, and has either a reply ready for review or a named person waiting on it. A quiet
sweep finishes in one line.

Messages to outsiders stay drafts until a person turns mail sending on in Tico. Requested CRM changes use your Tools.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/inbound-method.md`, `knowledge/do-not-contact.md`, and the last ten lines of `knowledge/lead-log.md`.
Note the time. Every speed figure starts from a lead's arrival, not from this sweep.

## 2. Find what arrived

Read every new lead since the last sweep from the sources named in `knowledge/inbound-method.md`: forms, the sales
inbox, and new CRM leads. A trial sign-up counts only when the person asked for help or a call. Check each lead
against `knowledge/lead-log.md` so none is worked twice. Order by arrival, except that a request for a call goes first.

## 3. Sort each lead

For each lead, follow `skills/qualify-an-inbound-lead/SKILL.md`, then append one line to `knowledge/lead-log.md`:
arrival time, source, sort, reason. The four sorts:

- **Meeting**: it fits, has a need and a timing signal, and wants to talk. Reply with two times.
- **Question for a person**: it asks for a price, terms, a security or legal answer, or a custom build. Hand it over
  with the question in the first line.
- **Not a fit**: a criterion is clearly not met. Name the criterion. A real person who was not a fit gets a short, polite
  draft; a spam message or a vendor pitch gets a log line only.
- **Stop**: the person asked not to be contacted. Add them to `knowledge/do-not-contact.md` in this run.

A customer goes to the Account Manager. A competitor is logged as not a fit, with the reason.

## 4. Draft the replies

For each meeting or answerable question, follow `skills/write-a-reply-for-review/SKILL.md`. Put on the task: the
recipient, the subject, the body, the source of each fact in it, and the time it was drafted. Mark each price or term
gap as a gap. Never fill it.

## 5. Hand over booked calls

When a lead accepts a time, write a one-page note: who they are, why now, what they asked, the timing signal, and the
open questions. Then run `hub task create --owner sales --title "<lead> call booked" --body-file <note>`. The calendar
invitation to the lead is a person's act.

## 6. Finish

Update `knowledge/leads/<lead>.md` for any lead worked past its first reply, and commit. Then
`hub task update <id> --status done --note` with: leads arrived, how many of each sort, drafts ready, calls booked, and
the oldest lead still waiting and its age. A sweep with no new lead still finishes with that line.
