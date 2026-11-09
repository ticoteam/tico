# Review a design doc

Triggered by a task naming a design doc, an RFC or a large structural pull request, and used for each new design in the
weekly review. Budget 40 minutes.

Inputs: the document and its linked threads; `knowledge/system-map.md` for the parts it touches; any ADR it contradicts or
supersedes; `knowledge/decision-rules.md`.

Outcome: review notes the author can act on and the decision-maker can decide from. Nothing is posted on the document. The
notes are one file, `reports/reviews/<doc>.md`, in this shape:
1. the recommendation, approved, approved with changes, or rework, in the first line. It is advice only: the human who
   owns the decision approves or blocks it, and this review never does. The one blocking question follows it;
2. the restatement, three sentences;
3. the one-way doors, each with its four answers (blast radius, data path, rollback, expand and contract);
4. the questions, blocking first, each labelled blocking or non-blocking;
5. the risks, each with its mitigation;
6. the ADR the decision will need, if any;
7. what you could not read.

Done when every decision in the proposal is marked one-way or two-way, every one-way door has its answers or a stated gap,
the recommendation matches the findings, and the task note carries the recommendation and the one blocking question.

---

## 1. Read it twice

    hub task show <id>

First for the problem, then for the solution. Read the parts of `knowledge/system-map.md` it touches and any ADR it
contradicts or supersedes.

## 2. Restate

In three sentences: the problem, the proposal, and what success looks like. If you cannot restate the problem, that is the
first finding.

## 3. Check

- **Options:** were at least two considered, including doing nothing? If not, name the obvious alternative.
- **Reversibility:** follow `skills/review-a-design-for-reversibility/SKILL.md`. One-way doors come first in the notes.
- **Boundaries:** which services, data stores and teams change; new coupling or a new single point of failure.
- **Data:** ownership, migration and backfill, what happens to existing records. For a relation that changes from one to
  many, walk two example rows through each reader.
- **Access:** who can obtain each new scope or credential, and which serializers show the new fields.
- **Failure:** what happens when a dependency is slow or down; limits, page sizes, retries and timeouts.
- **Operations:** how it is deployed, observed and turned off; who is paged.
- **Security and privacy:** new data collected, new external calls, new credentials. Flag them to the Security Engineer.
- **Cost:** the running cost and the cost of a wrong guess.

## 4. Write the notes

Write them in the shape above. Label each point blocking or non-blocking. Attach the file, commit it, and run
`hub task update <id> --status done --note` with the recommendation and the one blocking question in the first line.

## 5. When the design is a rework

A rework names the shape that is wrong, and the smallest change that would make it approvable. Do not list every
imperfection. A rework that only lists thirty small points has not said what is wrong.
