# Draft a postmortem

Used for each incident that meets `knowledge/triggers.md` (see `playbooks/weekly-incident-review.md`), and when a
task names an incident. Budget 40 minutes. The outcome is one blameless draft for the incident lead's review,
due within three working days of the incident (the default in `knowledge/triggers.md`). Nothing is posted or shared.

PagerDuty's postmortem template schedules its review within five business days of an event (see
`knowledge/incident-practice.md`). Three working days is the stricter default. The owner may change it.

---

## Inputs

- `reports/incidents/YYYY-MM-DD-<name>.md`, the timeline from `playbooks/build-a-timeline.md`. If there is none, build it first.
- `knowledge/triggers.md` (the severity scale and who reviews) and `knowledge/actions.md` (existing items, so none is duplicated).
- The change record near the incident: merged changes, deploys, and any debrief notes.

## Steps

1. **Start from the timeline.** Condense the full timeline to the events that mattered.
2. **Write the draft, in this order.**
   1. **Summary**, three sentences: what broke, for how long and for whom, and how it was fixed.
   2. **Impact**: users, requests, minutes, money if known, each with its source (`skills/count-impact-from-sources/SKILL.md`).
      A missing number is a marked gap.
   3. **Detection**: how it was noticed and how long after it started. If a customer noticed first, say so.
   4. **Timeline** (UTC): the condensed events.
   5. **Contributing factors**: the conditions that made it possible or worse, written as system facts. Ask
      "what made this easy to do?", not "who did this?". A short five-whys chain is allowed.
   6. **What went well**: at least one line from the record, such as a fast rollback or a useful alert.
   7. **Where we got lucky**: what could have made it worse.
   8. **Action items**: each written as in `skills/write-an-action-item/SKILL.md`, marked "proposed".
3. **Check it is blameless.** Reread for a person's name next to a cause, or for "failed to", "forgot" or
   "should have". Rewrite each as a condition or a decision, with what was known at the time.
4. **Check the facts.** Every time, number and cause has a source. Anything inferred is marked "approx." with why.
   Two sources that disagree are both listed with their dates.
5. **Hand over.** Put the draft on the task, labelled "Draft for incident lead review". Add the action items to
   `knowledge/actions.md` as "proposed". Ask the incident lead with `hub task ask <id>` only about missing owners,
   dates or conflicting facts. Create action-item tasks with `hub task create` only when the task asks for them.
6. **Finish.** Commit, then `hub task update <id> --status done --note`: the incident, the path, the gaps, and who must review.

## Output

`reports/incidents/YYYY-MM-DD-<name>.md`, with the draft under its own heading. Sections in the order above, then
what could not be read, then the sources. Shape: `knowledge/examples/incident-postmortem.md`.

## Done when

- Every section is written, or is marked "not enough data" with what would fill it.
- No person's name sits next to a cause, and no cause is a single plausible story.
- Each number, time and cause has a source or a gap mark.
- The draft is on the task, the action items are proposed, and the task is closed with a note.

## When you lack the data

Write the section as "not enough data" and say what would fill it. Never fill a cause with a plausible story.
