# Share a credential with another bot

Triggered when a human asks you to give bot B access to something bot A already has ("give it Jira access", "let the monitor use the
same GitHub token"). Budget 5 minutes. The human never leaves the chat, and no value passes through you.

The rule: a bot uses only its own credentials and the ones granted to it. A grant is the only way B gets what was A's. Never copy a
value between bots, files, tasks or messages, and never read a secrets file to do this.

Inputs: the bot that has the credential, the bot that needs it, and the credential's name.

Done when: the bot that needs it holds the credential through a grant and has reported its read-only check in one line, and the first bot's access is unchanged.

## 1. Find it

    hub credential list

It lists names, variables and which bots have each; never a value. If the credential is listed, go to step 3.

## 2. It is only in A's own file: move it into Credentials

    hub credential import <VARIABLE> --from-bot <A>

A's computer reads that one variable from A's own file and sends it to the server itself; the value never reaches you or the chat.
A's file is untouched and A keeps working. Only a credential admin (the owner or an admin) may ask, and the human you act for must
be one. If the server says they are not, say who can (the message names them) and stop. If it says the computer is offline or the
variable is not in A's file, say so in one line. Do not look for the value anywhere else.

## 3. Grant it to B, as them

    hub credential grant "<name>" --to <B>

It runs at once; no card. If B already has another credential under the same variable name, the server says so: take the old one
away (`hub credential revoke "<old name>" --from <B>`) when the human wants the new one instead, then grant again. Refused for their rights:
say so kindly, and who can. Do not look for another way in.

## 4. Check B's connection

Make sure B's `bot.yaml` declares the tool with that variable in `env:` (`hub tool add <B> <service> --can read --env <VARIABLE>`;
declaring it is the owner's ask, so do it only if they asked B to use the tool). B gets the credential from its next run. Run B's own
read-only check (read one Jira project, list one repository) and report in one line what it showed: "Engineering Monitor is connected to
Jira: project HTM loaded." If it fails, say why in one line; do not try another credential.

## 5. Taking it away

    hub credential revoke "<name>" --from <B>

B's next run no longer has it. A's access is unchanged.
