# Weekly spec and launch review

Schedule: Tuesdays at 10:00 team time (routine `weekly-spec-review`), after setup. Budget 30 minutes. The outcome is one page: which specs are blocked, on whom, and which
launch lines are due. Nothing is filed or changed.

---

## 1. Read where things stand

    hub task show <id>

Pull the repository. Then `state.md`, every spec in `specs/` not marked shipped, `knowledge/launch-checklist.md` and last
week's review. Check whether last week's blocking questions were answered, and whether last week's launch lines
were signed.

## 2. Check each spec in flight

For each: status (draft, agreed, building, in QA, shipped), its appetite and whether the work so far is inside it,
open questions and how many days each has been open, scope changes since last week with who agreed them, and what
engineering is doing against it (read the linked issues and pull requests, read only). A question open more than
three working days is blocking. A spec whose work has passed its appetite is a finding: say how far over, and
ask the owner to cut scope or decide the new appetite.

## 3. Check each launch due in four weeks

Follow `skills/run-a-launch-readiness-check/SKILL.md` for each launch. Mark each checklist line done (with its
evidence), due (with its date) or missing (with its owner). A missing line with no owner is a launch blocker.

## 4. Account for every item

Anything the review lists from a source (open tickets, requests, checklist lines) appears once in the review or
under "Left out" with its reason. Count the items you read and check the count against the source before you publish.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-spec-review.md` in the shape of `knowledge/examples/spec-review.md`, then
`hub file publish reports/YYYY-MM-DD-spec-review.md`. Commit, and `hub task update <id> --status done
--note`: the headline, the blocking questions and their owners, and what you could not read.
