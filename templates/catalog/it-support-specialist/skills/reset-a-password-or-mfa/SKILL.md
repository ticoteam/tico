---
name: reset-a-password-or-mfa
description: Help a person reset a password or replace a lost MFA method through the tool's own reset page, confirm who is asking by a route they cannot control, and state the team's password rule without ever seeing the password.
---
# Reset a password or MFA method

When to use: someone is locked out of an account, has lost the phone or key for their second factor, or asks for a
password change. Read `knowledge/it-practice.md` for the password rule and its source.

Steps:
1. Confirm the request comes from the account's owner. If it came from someone else, or from a channel the person does not
   normally use, verify first by a route the requester cannot control: their manager in person, or a call to a number on
   record (not one given in the request). Do not reset on a message alone.
2. Send the person to the tool's own reset page. `knowledge/tools.md` names it. The person sets the new password or enrols
   the new MFA method there. The new value never comes to you.
3. For a lost MFA method, remove the old method in the tool's admin console only after step 1 is complete. The person
   enrols the new one at their next sign-in. Record both dates.
4. Tell the person the rule in words and let the tool enforce it: a password used on its own is at least 15 characters, it
   is not a common or breached password, and nothing forces a change on a timer. Do not test or read the password.
5. For a shared account, change the password through the admin console for everyone who knew it, and list who was told
   on the task.
6. Ask the person to confirm that sign-in works. Close the request only on that confirmation, or after a stated wait.

Checks before you finish:
- The requester's identity was checked by a route they could not change, and the check is written on the task.
- No password, recovery code or MFA code was asked for, pasted into the task, or written into `knowledge/`.
- The MFA removal and the re-enrolment each have a date.
- A shared-account change lists who was told.

Example (invented, for illustration): "Lena cannot sign in to the billing export. Her manager confirmed by phone at 10:05
(a number on record). Sent her the tool's reset page; she set a new password and an authenticator app at 10:20 and
confirmed sign-in at 10:24. IT did not see the password. Closed."
