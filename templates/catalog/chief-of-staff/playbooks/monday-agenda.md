# Monday agenda

Triggered by a request ("prepare Monday's agenda") or as the last section of the weekly brief.
Budget 10 minutes. The outcome is a draft agenda a human can run the meeting from, with an owner
and a time on every item.

---

## 1. Read

    hub task show <id>

Then `knowledge/rhythm.md` for the meeting's attendees and length, this week's brief (or run
`playbooks/weekly-company-brief.md` first if there is none), `hub calendar list` for what is
booked, and last Monday's agenda in `reports/` to see what was left open.

## 2. Pick at most five items

An item earns its place if it is:
1. a decision that only this group can take, waiting more than a few days;
2. a goal that is red, or yellow for two weeks running;
3. a stalled item that needs a human in the room to unblock;
4. carried over from last Monday and still open.

Status updates that nobody needs to discuss are not items. Put them in one "For your information"
line linked to the brief.

## 3. Draft

    # Monday agenda, <date>, <length> minutes
    1. <Item as a question or a decision>, <owner>, <minutes>. Why it is here: <one line, cited>.
    Decision needed: <the exact decision, or "none, discussion">.
   ...
    For your information: <one line and a link>.
    Carried over: <what stayed open and why>.

Minutes must add up to the meeting length minus five. Every item has one owner, named.

## 4. Deliver

Write `reports/YYYY-MM-DD-monday-agenda.md`, attach it to the task, and say it is a draft. Send the agenda to the named attendees only when the task asks for it and a granted Tool allows it; otherwise it stays a draft for the owner. After the meeting, if notes were imported,
compare the decisions with the agenda and update `knowledge/open-loops.md`.
