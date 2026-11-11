---
name: write-an-engineering-bug-report
description: Write one bug report an engineer can act on without asking back, with a title that says what breaks and where, steps from a known state, expected and actual results, environment, frequency, customers affected, evidence with credentials removed, and any tested workaround.
---
# Write an engineering bug report

When to use: after a problem is reproduced and before anyone files it. One report per bug.

Steps:
1. Search first. Look at the existing issues for the same symptom and version (`gh search issues`, where connected,
   and `knowledge/workarounds.md`). If one covers it, do not write a new report. Add this ticket's evidence to the
   existing issue through the person who files.
2. Write the title as what breaks and where: "Booking time shifts one hour after calendar sync for locations outside the
   account zone". Not "Calendar bug".
3. Write the steps from a known state, numbered, each with its value (`skills/reproduce-from-a-known-state/SKILL.md`).
4. Write the expected result and the actual result, each in one sentence.
5. Write the environment: product version, plan, browser or client, region, integration and its version.
6. Write the frequency as every time, sometimes or once, and say how many times you ran the steps.
7. Write the customers affected as a count and the ticket references. Do not name the customers.
8. Attach the evidence: the log lines, request ids or screenshots that show the problem. Replace every credential,
   token and personal detail with "redacted" before it leaves your files.
9. Write the workaround, if there is one, and mark it tested (with the version it was tested on) or untested.
10. Write what you ruled out, in one line. Engineers save time when the setting that does not matter is named.
11. Put the report on a task for the person who files bugs. Do not file it yourself unless the owner has granted write
    access and the task asks for it.

Checks before you finish:
- One bug only. A second problem gets its own report.
- The steps run from a stated state with values, and the title says what and where.
- Expected and actual are each one sentence and differ.
- No credential or personal detail is in the text or the evidence.
- The workaround is marked tested or untested.

Example (invented, for illustration only): title "Calendar sync moves class times one hour for locations set to a
different zone than the account". Steps: a test account in zone A, a location in zone B, a class at 18:00 in zone B.
Expected: 18:00 after sync. Actual: 17:00. Frequency: every time, 2 of 2 runs. Affects 5 tickets. Environment: version
x.y.z, web client. Workaround: set the location's own zone, tested on the same version. Ruled out: the browser.
