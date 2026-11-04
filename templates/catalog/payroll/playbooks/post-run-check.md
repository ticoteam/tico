# Post-run check

Triggered by a task with the payroll register of a run just processed, or the day after a pay date.
Budget 20 minutes. The outcome is a pass, or a list of corrections for the next run. Nothing is changed.

Inputs: the payroll register of the run, the documented change summary for that run, and `knowledge/baseline.md`.

Done when: the pass or the list of differences is on the task, and any baseline update is committed with its register date.

---

## 1. Read both

    hub task show <id>

The register from the payroll system and the documented change summary for that run.

## 2. Compare

Per person: in the register and not in the expected roster, or the reverse; gross pay differing from
the baseline plus documented changes; hours or overtime that differ from the recorded timesheet; a bonus,
commission or deduction that differs from its source record.

## 3. Report

On the task: **Pass** or **N differences**, one line each (person, item, expected, register, source),
and the correction the payroll owner may want in the next run or off-cycle. The decision is theirs.

## 4. Update the baseline

once the register is final, and when a human asks, write the new headcount and gross by pay group to
`knowledge/baseline.md` with the register date, commit, and finish the task.
