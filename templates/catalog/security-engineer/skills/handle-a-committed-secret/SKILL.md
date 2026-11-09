---
name: "handle-a-committed-secret"
description: "Respond to a credential found in a repository, a pull request or a commit history, in the order that limits damage: confirm it is live, have it rotated, check its use in the audit window, then clean the repository and prevent a repeat, without ever copying its value."
---
# Handle a committed secret

When to use: when a scan, a report or a person finds a key, token, password or private key in a repository, a pull
request, a log or a commit. Start the same day, and tell the owner in `knowledge/exposure.md` at once.

Steps:
1. Record the location only: repository, file, commit, line number and the kind of credential. Never copy the value into a
   report, a task, a note or a message, and never any of its characters. When you must tell two copies apart, have a script
   print a hash of the value (a short SHA-256 prefix) instead.
2. Confirm the owner: match the value's hash against the provider's key list, or ask the owner of the service which key it is.
   Do not rate the secret until you know whose it is and what it can do.
3. Ask the owner to rotate or revoke it first, in the provider's own console, and say so on the task. You rotate or revoke it
   yourself only when the task asks for it and a granted Tool allows it. Deleting the line does not undo the leak. A secret
   that was public for any time is treated as known to an attacker.
4. Check its use in the audit window: from the first commit that held it to the rotation. Look for calls from unknown
   addresses, new resources, unusual regions, and access to data the key should not reach. Record what was checked and what
   the provider's logs did not cover.
5. Scan for other copies: the same value in other files, branches, forks, logs and open pull requests. Each copy is a location in
   the report.
6. Clean the repository only with the owner's decision. Removing the line from the current branch is safe. Rewriting history
   affects every clone and fork, so it is a planned change, and it is not needed once the secret is invalid. Delete any scan
   clone you made.
7. Prevent a repeat: name the secret-scanning or push-protection control the repository lacks, and propose it as a task for
   the owner.

Checks before you finish:
- The report names the location and the kind, never the value.
- The rotation status is known: rotated, asked, or refused, with the date.
- The audit window and what the logs did not cover are both written down.
- Every copy found is listed.
- The owner has the task the same day.

Example (invented, for illustration): "Repository web-app, file tests/fixtures/payments.env, commit 3f9c2a1 (2026-09-22), a payment
provider test key. Owner: the web team (confirmed by the lead). Rotation: asked on 2026-09-28, pending. Audit window: no calls
logged from unknown addresses, provider logs cover 30 days. Other copies: none in open pull requests. Prevention: push protection
is off; proposed as a task."
