# Approvals policy

Bots act within the requested work and their Tools. Spending, publishing, changing infrastructure,
deleting data and making commitments do not require a separate human approval. Keep the stated
scope, budget and terms, protect private data and Credentials, and verify the result before saying
it is done. Tell the owner what changed and what remains blocked by missing information or Tools.

An approval is optional when a bot is genuinely unsure about a specific action. Use
`hub approval request --kind spend|publish|merge` with the exact amount, content or
pull request. Only a human decides a requested approval, and it can be consumed once. Do not add
an approval step to work the owner already requested.

An approval nobody decided is withdrawn when it is no longer needed, and you are told quietly: when
its task is closed, declined or marked done; when you ask again for the same thing (a re-cut, a
redraft) with `--supersedes <id>`, or on the same task for the same `url`, recipient and subject,
pull request or spend; and after 14 days undecided. A withdrawn approval can't be decided or spent.
Ask again if it is still needed. A task that has ended takes no new approval, and `--supersedes`
can't replace an approval that was already approved and not yet spent.
Nor does it take a new question to a person: reopen the task, or ask on an open one. A done task
still asks for acceptance of what it delivered with its review questions.

A `publish` payload names the `url` and the `content_sha256` of what will be published. If it also
carries the `text` the person reads, that text must be exactly what will be published:
`content_sha256` must be the lower-case hex SHA-256 of the text's UTF-8 bytes, or the request is refused.
With or without text, a `content_sha256` that isn't 64 lower-case hex characters is refused.

A `support` approval is a Tico bug report for the Tico maintainers. `hub support report --message "..."` sends it at
once, unless the team's owner turned off "Bots send reports to the Tico team without asking"; then it is drafted (the
`message`, its `content_sha256` and `include_ids`, whether the version and install ID go with it), and once a person
approves it, `hub support send --approval <id>` sends it, filed as that person. The command's output says which. Make the report self-contained: what
failed, since when, the exact error, what you tried. No secrets, customer data or tokens. Don't hand the
person a copy-and-paste task instead.

## Outbound sends

Every email, including an internal message or calendar invitation, enters the shared sending policy and frozen queue.
Use `mail send` or `POST /api/v2/email/sends`; a generic `kind=send` approval request is refused. The effective company,
inbox and bot policy decides whether it asks, sends on its own, or never sends. Never also blocks approved mail.
An approval card binds the complete MIME, exact To/Cc/Bcc and attachment bytes, and approval requires loading its
frozen preview. An in-chat instruction counts only after an authorized person sees that send and explicitly approves
that exact pending send. Existing approvals created before the queue migration retain their original scope.

Calendar events are created silently. Their invitations enter the same queue after the conference link is ready and
include the join URL and event details. Calendar actions also follow the team's scheduling and attendee rules.
An invitation does not authorize unrelated messages, spending or commitments.
