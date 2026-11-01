# Schedule an interview loop

Triggered by a task from `recruiting` or by a hiring manager moving a candidate to interview. Budget 15 minutes.

Inputs: the task, `knowledge/loops/<role>.md` (rounds, panel, length, format), `knowledge/interviewer-rules.md`, and each
panel member's calendar for the window.

Outcome: one message to the candidate offering three real sets of times, ready to review, and held slots for the panel
once the candidate chooses. Candidate messages stay drafts until a person turns mail sending on in Tico.

Done when the message is on the task, the calendars you checked are named in the task note, and `knowledge/schedule.md`
has the candidate's line.

---

## 1. Read the loop

    hub task show <id>

Read the role's `knowledge/loops/<role>.md`. If the task changes the loop for this candidate, use the task and note why.
If a panel name is missing, ask one question with `hub task ask <id>`.

## 2. Find three options

Run `skills/build-three-real-slots/SKILL.md`. It covers the calendar reads, the interviewer rules, the buffers and the time
zones.

## 3. Write the message

Run `skills/write-a-one-round-candidate-message/SKILL.md`. Put the draft on the task. It goes to the candidate only when a
person has turned mail sending on in Tico and the task asks for it.

## 4. On the candidate's choice

When the candidate chooses, book each roster interviewer with `hub calendar schedule --title "<role> interview: <reference>"
--start <iso> --end <iso>`, one call per round. Check each booking with `hub calendar status <action-id>`. The candidate's
confirmation, with the join link, goes up for review as a message. Update `knowledge/schedule.md`.

If the candidate asks for a time that is not one of the three, offer new options from step 2. Do not book a time the
candidate has not chosen.

## 5. Kits

The day before, send each panel member the role's questions, the scoring guide and the resume. Attach them to the interview
task (`hub task attach`) and send one `hub message send` line that points to them.

## When a source fails

A calendar you cannot read means that person has no known free time. Say so in the task, and offer no option that needs
them.
