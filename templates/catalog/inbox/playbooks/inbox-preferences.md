# Inbox preferences

Standing preferences for the mailbox this bot is assigned. A run that learns a preference that should
hold next month writes it here, one bullet, present tense, no dates. The dated note goes in
`memory/decisions.md`.

## Filing

Follow the requested mailbox-filing rules with your Tools. Record labels and archive preferences
here, and log changes in `memory/decisions.md`. If no filing rule covers a message, state the
necessary assumption or ask for the missing preference.

## Sending

Off by default. While mail sending is off in Tico (bot.yaml only asks), replies and forwards to outsiders stay drafts.
When the owner asks BotOps to turn sending on, BotOps sets `outbound_send: true` and records
`forward_to:` and the mailbox rules in `bot.yaml` and here. With sending on, send within the
requested work and those rules using your Tools; no approval is required for each message.
A recipient outside the standing rules needs a scoped request, not a separate approval.

## Always reaches the human
None yet. Setup fills this: people, topics and senders that are flagged first and never filed.

## Fine to file
None yet. Setup fills this: senders and kinds of mail that can be filed without asking.

## Commitments

Make only the commitments the requested work or standing instructions cover, with the necessary
Tools and sourced facts. Never invent a budget, meeting time or contract term.

## Routed to someone else
None yet. Setup fills this: kinds of mail that belong to another bot or human, and who.
