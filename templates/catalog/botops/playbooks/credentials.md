# Credentials

Read this before you handle any credential. The rules in AGENT.md always hold: a bot never uses a credential that was not
granted to it, and no value is printed, logged, committed, copied between bots, read, or rotated when it already exists.

Inputs: the bot that needs the credential, the variable's name, and whether the value already exists in Credentials or in another bot's file.

Done when: the credential is in Credentials and granted to that bot, its read-only connection test is reported in one line, and no value was printed, logged or copied.

## A bot needs a credential
Open the card: `hub credential request <VARIABLE> --for-bot <bot> --label "your Jira credential" --format "you@example.com:API token"
--help-url <where they make one>`. A field appears in the chat; the value goes straight to Credentials and to that bot, never
through you. You are woken when it is saved: run the bot's read-only connection test, say in one line what it showed, and open
the card again if it failed. Send no one to Tools.

## A human pastes a credential in chat anyway
Store it and carry on: `printf '%s' "$VALUE" | hub credential set <VARIABLE> --for-bot <bot>` (the value on standard input,
never in the command). That also takes it out of the conversation. Tell them in one line that it is saved and removed from the
chat, and that the card keeps it off the model entirely next time.

## A human asks to give bot B a credential bot A has ("give it Jira access")
Follow the steps: move it into Credentials if it is only in A's own file (`hub credential import <VARIABLE> --from-bot <A>`),
grant it to B as them (`hub credential grant <name> --to <B>`), then test B's connection. A credential administrator (the owner
or an admin) gets it done at once, with no card. Anyone else is told who to ask. Never copy a value from one bot or file to
another: a grant is the only way a bot gets a credential that was not its own.

## Who may store one
Storing a credential is allowed for the owner and the admins; the server decides. Never print, log, commit or copy a value
between bots. Never read, print or rotate a credential that already exists.

## Where a new one goes
Credentials always work: there is no key to set up and no fallback to a bot's secrets file. Put a new credential in
Credentials (a card, or `hub credential set`), not in `secrets/<bot>.env`.
