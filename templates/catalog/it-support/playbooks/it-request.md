# IT request

Triggered by a task from anyone: "I can't log in to the CRM", "my laptop is slow", "Omar needs access to
the finance folder". Budget 15 minutes. The outcome is a fix the human can follow, or a complete access
change in front of its owner, and the request logged.

Inputs: the task and its attachments, `knowledge/tools.md`, `knowledge/fixes/`.

Done when: the request has a category, a priority and a reason on the task; the fix or the prepared access change is
on the task; the requester has confirmed it works, or the task states the next step; and the row is in
`knowledge/requests.md`.

---

## 1. Classify

    hub task show <id>

Run `skills/triage-an-it-request-by-impact/SKILL.md`: a category (account and access, device, network, software,
security), a priority by how many people are stopped, and the reason on the task. A suspected compromise (a login they
did not make, a clicked link that asked for a password, a lost laptop) is P1: tell the Operations Manager now and say
what to do first (change that password, report the device) before anything else.

## 2. Look for a known fix

`knowledge/fixes/`, then `hub doc ask "<the problem>"`. If the team has a guide, point to it and
give the steps; if the guide is wrong, say so and report it to the Librarian.

## 3. Give the fix

Numbered steps, one action each, and what the human should see after each. Ask for the exact error
text or a screenshot if the first try fails. Two failed attempts on a device problem means a human
looks at it: say who. A password or MFA problem follows `skills/reset-a-password-or-mfa/SKILL.md`.

## 4. Access requests

Fill every field: human, tool, role (the least that does the job), reason, end date if temporary,
approver from `knowledge/tools.md`. Use granted admin access only within the stated scope. Put it
on the task and ask the owner with `hub task create --owner <owner>` after the requester
confirms the details. The admin applies the change, or you do where the owner has given this bot
write access. Then confirm with the requester that it works.

## 5. Log

Add the row to `knowledge/requests.md`; a new fix that worked becomes `knowledge/fixes/<problem>.md`.
Close the task when the human says it works, or after the stated wait.
