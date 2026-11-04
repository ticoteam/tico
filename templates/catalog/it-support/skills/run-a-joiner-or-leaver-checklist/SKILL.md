---
name: run-a-joiner-or-leaver-checklist
description: Run the IT checklist for one person starting or leaving, one line per account and device, each marked done with the date and time it was read, waiting on a named person, or not started, with a leaver's accounts disabled by the last day.
---
# Run a joiner or leaver checklist

When to use: for every start and every departure in `hub team show` or in a task, from the day it is known until every
line is done.

Steps:
1. Take the person, their role, the start or last day, and the manager from the roster or the task. If the day is
   missing, ask the manager once with `hub task ask <id>`. Do not guess a date.
2. Open `knowledge/checklists/joiner.md` or `leaver.md`. Give one line per account in `knowledge/tools.md` and one line
   per device in `knowledge/devices.md` that the person holds or needs.
3. Joiner: create each account at the role the manager chose. Assign the device and confirm its disk is encrypted before
   it is handed over. Set up MFA at the first sign-in. Record the time you read each line done.
4. Leaver: on the last day, disable each account. Disable, do not delete, so the record and the mail stay as the manager
   chooses. Change any password the person knew for a shared account. Return the device. Wipe it only when the task asks
   for a wipe and a granted device-management Tool allows it; otherwise the wipe is a pending line for the admin. Set mail
   forwarding or an auto-reply only if the manager chose one.
5. Mark each line one of three ways: done (with the date and time you read it), waiting (with the name of the person it
   waits on and since when), or not started. A line is not done because someone said it is.
6. A leaver past the last day with any line still live is P1. Put it first on the weekly page and tell the Operations
   Manager at once.
7. Close the checklist when every line is done or has a named owner and a date. Add the close to `knowledge/requests.md`.

Checks before you finish:
- Every account and device in the tool list has a line.
- Each line shows done with a date and time, waiting on a named person, or not started.
- A leaver's accounts are disabled, not deleted, and the date is recorded.
- No password, recovery code or MFA code appears in the checklist or the task.
- No device is wiped unless the task asks for it and a granted device-management Tool allows it.

Example (invented, for illustration): "Kenji, joiner, starts 2026-10-05 as studio assistant (roster, read 2026-09-30).
Mail: created 2026-10-02 at the studio role; MFA not set, waiting on Kenji since 2026-10-03. Chat: not started. Laptop
AC-031: encrypted, assigned 2026-10-01 (read 14:20). Checklist 3 of 9 done."
