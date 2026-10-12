# Approvals policy

Bots act within the requested work and their Tools. Spending, publishing, changing infrastructure,
deleting data and making commitments do not require a separate human approval. Keep the stated
scope, budget and terms, protect private data and Credentials, and verify the result before saying
it is done. Tell the owner what changed and what remains blocked by missing information or Tools.

An approval is optional when a bot is genuinely unsure about a specific action. Use
`hub approval request --kind send|spend|publish|merge` with the exact message, amount, content or
pull request. Only a human decides a requested approval, and it can be consumed once. Do not add
an approval step to work the owner already requested.

A `publish` payload names the `url` and the `content_sha256` of what will be published. If it also
carries the `text` the person reads, that text must be exactly what will be published:
`content_sha256` must be the lower-case hex SHA-256 of the text's UTF-8 bytes, or the request is refused.
With or without text, a `content_sha256` that isn't 64 lower-case hex characters is refused.

## Outbound sends

While `outbound_send` is false in a bot's `bot.yaml`, messages to outsiders stay drafts. Attach the
send-ready text and its destination to the task. A requested approval does not turn sending on.
When `outbound_send: true`, send within the requested work, standing instructions and the bot's
Tools; no separate approval is required for each message.

Internal messages and calendar invitations need no approval. Calendar actions must follow the
team's scheduling rules and any configured attendee restriction. An invitation does not authorize
unrelated messages, spending or commitments.
