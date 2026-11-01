# After-launch read

Triggered by a task that names a shipped change and its success measure, or by the weekly report when a change with a
measure has been live 14 days and has no read yet. Budget 30 minutes. The outcome is one verdict with its reading and
its customer evidence, for the requester. Nothing is sent to a customer, and nothing is decided.

Inputs: the change's success measure as written in its spec or on the task; the measure's source; the feedback from
the 14 days since ship.
Done when: the read is published with one verdict, and the task is finished with the verdict in its first line.

---

## 1. Read the measure

    hub task show <id>

Take the measure exactly as written: what it counts, the target, the baseline and the source. If no measure was
written, the verdict is "no measure". Say so once, in the task, and stop.

## 2. Read the number

Take the current value from its source: `hub kpi show <id>`, the data source the spec names, or a number the requester
gives on the task. Write the source and the date of the reading. If it cannot be read, do not estimate it. The verdict
is "can't tell yet", and the note says what is missing.

## 3. Collect the customer evidence

From the 14 days since ship, from the same sources as the weekly report: tickets and digests, tasks and attachments,
calls, and the log in `knowledge/log/`. Count the items that mention the change, by severity. Pick two or three typical
anonymised quotes, each marked with its source and week. Include any bug or complaint the change caused. Fewer than five
items is a note, not evidence of a trend.

## 4. Verdict

One of these four:
- **Worked**: the target is met.
- **Partly**: the number moved toward the target, or met it with real customer pain.
- **Did not work**: no movement, or worse.
- **Can't tell yet**: the measure cannot be read, or the data is too thin. Say what is missing and when to check again.

## 5. Hand over

Write `reports/after-launch/YYYY-MM-DD-<change>.md`:
- the first line: the change and the verdict;
- the measure, target, baseline and reading, with source and date;
- customer evidence: counts, severity, two or three quotes, and what could not be read;
- any bug, as a suggestion. File it with `issue-triage` only when the task asks for it.

Publish it with `hub file publish reports/after-launch/<file>`, attach it to the task, and finish with
`hub task update <id> --status done --note`. Name the verdict and the path in the note.
