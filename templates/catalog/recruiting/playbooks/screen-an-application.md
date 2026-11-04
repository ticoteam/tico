# Screen an application

Triggered by a task that attaches an application, or by the mailbox if it is connected. Budget 8 minutes each.

Inputs: the application (attached to the task, or read from the connected mailbox), and `knowledge/roles/<role>.md`.

Outcome: a summary under 150 words for the hiring manager. It says how the application matches the stated criteria. It
does not say whether to advance the person.

Done when the summary is attached to the task, the pipeline has one line for the candidate, and the task note says how many
applications were summarised and which could not be read.

---

## 1. Read the role file first

`knowledge/roles/<role>.md`. If it has no written criteria, stop and ask the manager one question with `hub task ask <id>`.

## 2. Read the application

Only what a human handed you or the connected mailbox holds. Do not search for the person online, and do not use photos,
social profiles, names that suggest an origin, or age signals such as graduation years. Note, but do not use, anything in
the application that is not a stated criterion.

## 3. Match, item by item

Run `skills/screen-against-stated-criteria/SKILL.md`. Each required criterion gets one result: **shown** (with the line),
**partly shown**, or **not shown in the application**. Then the preferred criteria the same way. Add up to two questions
for the manager.

## 4. Write the summary

Three lines and a table: a reference (initials and a number), the role, the criteria match, and the questions. No score,
rank or recommendation. If the application mentions a disability, an accommodation, pregnancy, religion or family, leave it
out of the summary and tell the manager to read the original.

## 5. Record

Add one line to `knowledge/pipeline.md`: reference, role, the stage a human set, and the date. Do not copy contact details
into a file.

## 6. Finish

Attach the summary to the task. Close it with `hub task update <id> --status done --note "..."`: how many were summarised,
which could not be read, and who is waiting past the agreed wait. A reply to the candidate is a draft on the task. It is
sent only when a person has turned mail sending on in Tico and the task asks for it.

## When a source fails

An unreadable attachment is "not read", and the summary says so at the top. It is never a weaker match.
