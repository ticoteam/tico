# OK policy

Bots act within the requested work and their Tools. Spending, publishing, changing infrastructure,
deleting data and making commitments do not need a separate OK from a person. Keep the stated
scope, budget and terms, protect private data and Credentials, and verify the result before saying
it is done. Tell the owner what changed and what is still missing.

Ask for an OK only when you are unsure about one exact action, or when Tico holds it. Ask on the
task you are working on, name one person, and show them the exact thing:
`tico task ask <id> --to <person> "<one sentence>" --kind ok --show <page:ID|file:ID@N|link> --version N`.
The task stays in its stage until they OK it or send it back. It is one line in their Needs you, and
their answer wakes you once. Asking again in the same stage replaces your open ask.

An OK covers exactly what the person saw. Never act on an OK for different content: a new draft, a
new file version, other recipients, a larger amount, a new head of a pull request. Ask again. A push
past is never an OK, and neither is a "yes" in a chat.

For a file, a budget raise or a reply to Tico support, Tico binds the OK to the content for you:

```text
tico task ask <id> --to <person> "<one sentence>" --for <kind>:<id> --content '<json>' --version N
tico ok use <requirement> --for <kind>:<id> --content '<json>' -m "<what was done>"
```

The first prints the requirement id. Run `tico ok use` just before you act, with the same subject and
content. It passes once, only after the person's own tap. If it is refused, do not act. The kinds are
`file` (`file:<ID@N>`, pinned to that version), `budget_raise` and `hq_reply`. An email, a letter, an
edition, a Finance action and a webhook ask for their OK where the work is: do not ask a second one.

A docs change needs no OK. Put a `Changelog:` line in its pull request body that says what changed
for readers, then merge it yourself once its checks are green. A pull request that mixes docs with
code, in a repository whose merges need a person, gets an OK on the task with the pull request shown.
Do not add an OK to work the owner already requested.

An ask never expires. It ends when it is answered, when you withdraw it
(`tico task ask <id> --withdraw -m "<why>"`), or when the task moves back or closes. A task that has
ended takes no new ask: reopen it (`tico task move <id> reopen -m "<why>" --version N`) or ask on an
open one.

A Tico bug report goes to the Tico maintainers with `tico support report --message "..." --task <id>`.
It is sent at once, unless the team's owner turned off "Bots send reports to the Tico team without
asking". Then Tico asks a person for an OK on the task, and once they OK it,
`tico support send --ok <id>` sends it, filed as that person. The command's output says which. Make
the report self-contained: what failed, since when, the exact error, what you tried. No secrets,
customer data or tokens. Don't hand the person a copy-and-paste job instead.

## Outbound sends

Every email, including an internal message or calendar invitation, enters the shared sending policy
and frozen queue. Use `mail send` or `POST /api/v2/email/sends`. The company, inbox and bot policy
decides whether it asks, sends on its own, or never sends. Never also stops mail that was OK'd.
Under Ask first, Tico asks the OK on the task itself. It binds the complete message, the exact
To/Cc/Bcc and the attachment bytes, and the person OKs it from its preview. An instruction in a chat
counts only after an authorized person sees that send and OKs that exact message.

Calendar events are created silently. Their invitations enter the same queue after the conference
link is ready and include the join URL and event details. Calendar actions also follow the team's
scheduling and attendee rules. An invitation does not authorize unrelated messages, spending or
commitments.
