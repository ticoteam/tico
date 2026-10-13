# Weekday prospecting run

Schedule: weekdays at 07:30 team time (routine `weekday-prospecting`), after setup. Also run by hand. Budget 45 minutes. The outcome is one pack: inbound replies qualified, new
leads scored and briefed, first touches and due follow-ups ready to use, and meetings to book.
Messages to outsiders stay drafts until a person turns mail sending on in Tico; requested CRM changes use your Tools.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/icp.md`, `knowledge/scoring.md`, `knowledge/voice.md`, `knowledge/do-not-contact.md`,
`knowledge/sequences.md` (who is at which touch) and yesterday's pack. A lead on the do-not-contact list
is skipped and named as skipped.

## 2. Inbound first

New form fills and replies come before anything else: speed decides inbound. For each, sort it: wants a
meeting, has a question, not a fit, or asked to stop. A stop goes on `knowledge/do-not-contact.md` at
once. A meeting request gets a reply offering two times from `hub calendar list` for the seller,
prepared for review. A price or terms question goes to a human under "Needs a human now".

## 3. New leads: filter, research, score

Take at most ten, newest inbound first, then the fittest from a list. Drop customers, competitors and
leads already talking to an Account Executive. Follow `playbooks/research-a-lead.md` (10 minutes, two
sources read well). Apply `knowledge/scoring.md`: A is fit plus a signal from the last 30 days; B is fit
without a recent signal; C is parked with the reason.

## 4. Touches

- **First touch for each A lead**: under 100 words, plain text, at most one link, one specific, true,
  dated fact about them and why it matters, one small ask, no price or date.
- **Follow-ups due today** from `knowledge/sequences.md`: each adds a new reason (a fact, an answer, a
  smaller ask), never "just checking in". After the last agreed touch, stop and mark the lead "closed quiet".

Each touch carries recipient, subject, body and its source. Put them on the task and send the requested batch with your Tools when a person has turned mail sending on in Tico; otherwise leave drafts in the connected seller's mailbox.

## 5. Hand over booked meetings

A lead that accepted a time: write the handoff note (who, why now, what they said, the brief) and
propose `hub task create --owner sales` for the Account Executive. The calendar invitation to the lead
is a human's act.

## 6. Write the pack and finish

Write `reports/YYYY-MM-DD-prospecting.md` in the shape of `knowledge/examples/prospecting-run.md`, update
`knowledge/sequences.md`, `hub file publish` the pack, commit, and `hub task update <id> --status done
--note`: inbound handled, leads worked, A/B/C, touches drafted or sent, meetings booked, sources not
read. Always finish it: an open routine task absorbs tomorrow's.
