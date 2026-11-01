# Write up a meeting

Triggered by the `meeting.ready` routine (one task per imported team meeting, its notes and
transcript in the body), or by a human sending you a meeting. Budget 15 minutes. The outcome is
one report and a task note; the action-item tasks the transcript supports; and a share with the intended participants, as the coverage rules and a granted Tool allow.

Inputs: the meeting task with its notes and transcript, `knowledge/coverage.md`, `knowledge/people.md`, `knowledge/decision-log.md`,
and `knowledge/actions.md`.

Done when: the report is published, each decision and action item has a quote with its timestamp, the tasks are created or linked, the
recipients have their one-line note, and the task note carries the headline, the counts and what still waits for a human.

---

## 1. Decide whether to write it up

    hub task show <id>

Read `knowledge/coverage.md`. If the meeting is outside coverage, on the restricted list, or has a
transcript with nothing readable in it, finish the task with one line that says which and stop. A
skipped meeting is a stated decision, never silence. If the transcript and the invite show no recording notice, ask the human who ran the meeting once with `hub task ask <id>` and stop until they answer (`knowledge/recording-notice.md`).

## 2. Read it properly

The body has the source's own notes and the transcript. If it is cut off, read the rest:

    hub meeting read <id> --offset <next_offset>

Read the whole transcript. The source's summary is a lead, not a fact: check its decisions against
what was actually said.

## 3. Sort what was said

| Kind | Test | Where it goes |
|---|---|---|
| Decision | Someone proposed it and someone with the authority confirmed it | Decisions, with the reason if given |
| Action item | A named person said "I will", or was asked and agreed, to do a specific thing | Action items |
| Open question | Raised and not answered | Open questions |
| Discussion | Everything else | One or two lines at most, or nothing |

Anything you cannot quote is not a decision and not an action item. Two people agreeing "we should
look at that" is a discussion. Use a speaker's name only when the transcript labels that speaker (`skills/check-a-quote-before-you-record-it/SKILL.md`).

## 4. Check what already exists

    hub task list --owner <person>

Run the action test in `skills/decide-whether-an-action-is-a-task/SKILL.md` on each candidate first. If the action item is already a task, link it instead of proposing it again. Search
`knowledge/decision-log.md`: a decision that contradicts an earlier one is shown next to it with both
dates, not resolved by you.

## 5. Write the report

`reports/YYYY-MM-DD-<meeting>.md`, in the shape of `knowledge/examples/meeting-summary.md`:
1. **Headline**: what the meeting decided, one sentence.
2. **Decisions**: one line each, with the speaker and timestamp.
3. **Action items**: a table of action, proposed owner, due date (only if said), quote and timestamp.
   "Owner unclear" is a valid owner.
4. **Open questions** and **What I could not tell** (audio gaps, unidentified speakers).
5. **Sources**: the meeting id, its date and its link.

Under 250 words for the summary part. Append each decision to `knowledge/decision-log.md`. A point raised once is one mention; call it a pattern only with its count across meetings.

## 6. Create tasks and share

Create the action-item tasks that pass the test in step 4 with
`hub task create --owner <person> --link <meeting link>`. Use the actual owner and due date;
ask one focused question only when either is unclear. Send
`hub message send --fyi <person> "<one line and the link>"` to the recipients in
`knowledge/coverage.md`. Keep restricted meetings and private details with their intended readers.

## 7. Finish

Publish with `hub file publish reports/...`, commit, and `hub task update <id> --status done --note`:
the headline, counts, and what still waits. Always finish it; an open routine task absorbs the
next meeting.

## When the transcript is poor

Say so in the report and mark every item from it "low confidence". Never fill gaps with what
usually happens in such a meeting.
