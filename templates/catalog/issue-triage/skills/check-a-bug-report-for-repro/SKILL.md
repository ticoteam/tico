---
name: check-a-bug-report-for-repro
description: Decide whether a bug report holds enough to reproduce the problem, name exactly what is missing, and try the steps on the current release before you call it confirmed, not reproduced or not a bug.
---
# Check a bug report for repro

When to use: for every new issue that reports something that worked, or should work, and does not. Use it before you
draft a request for more information, and before you label a bug as confirmed.

Steps:
1. Classify first. Something that worked and no longer does is a bug. A request for something new is a feature. A
   "how do I" is a question. Only a bug goes through this skill.
2. Compare the report with `knowledge/repro-checklist.md`. Mark each item present, missing or unclear: product version
   or build, environment (operating system, browser or device, account type), steps from a clean state, expected result,
   actual result, how often it happens, and logs, a screen recording or a screenshot with its timestamp.
3. Ask only for the missing items: if one item is missing, ask for that one. Never ask for credentials, tokens or a full
   personal data export.
4. If the steps are present, try them on the latest release in the repository and on the version the reporter named.
   Write down what you ran and what you saw. A repro that needs a special account or data is recorded as "needs data",
   not as failed.
5. Sort the outcome into one of four:
   - reproduced: the steps show the problem on the named version, or only on the latest release; record the versions and the
     date. A problem on the latest release means the defect is current, and say so;
   - reproduced on reported version only; not reproduced on latest: the latest release does not show it, so the fix may
     already exist; say so;
   - not reproduced: say what you tried and ask for the one detail that would change the result;
   - waiting: the reporter has not answered; record the date you asked.
6. Note any regression: a version that worked and the first version that did not, if the report or the changelog gives both.
7. Put the outcome on the task and in the digest with the issue number, not in a comment, unless a person has turned
   mail sending on for this bot.

Checks before you finish:
- Each checklist item is marked present, missing or unclear, with the reason.
- The drafted request names only the missing items.
- "Not reproduced" always says what was tried and on which version.
- No token, key or personal detail from the report appears in your note; if one does, say it was redacted and recommend
  the reporter rotate it.
- A report that describes a security problem is not reproduced here. It goes to the owner named in `knowledge/areas.md`.

Example (invented, for illustration): "#1418, reported on version 2.14.1. Missing: steps and the operating system. Asked for
both in one line. Not reproduced yet. Waiting 6 days, asked 2026-09-22." After the reply: "Reproduced on 2.14.1 and 2.15.0
with the steps in the report, on macOS. Confirmed bug, area sync."
