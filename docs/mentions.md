# Mentions: what people ask a human in Slack

People keep @mentioning a human in Slack as they always have. Once that human connects their own
Slack, each thread that names them becomes a mention in Tico: their frontend lists it with the
thread so far, can draft an answer with a bot's context, and sends the reply they approve in the
thread as them. The code is `backend/mentions.py` (tables and API) and `receive_mention`,
`enrich_mentions` and `deliver_mention_replies` in `backend/slack_gateway.py`.

## How a message becomes a mention

| In Slack | What happens |
|---|---|
| A message that names the human (`<@them>`) in a channel or private channel they are in | A mention for that thread: `pending` until the gateway reads its context, then `open`. |
| The human is named again in the same thread | Joins the same mention; reopens it if it was `done`. |
| The human names themselves | A mention like any other (a note to self, or a test). |
| Anyone else's later message in that thread | Kept with the mention as context (`message`); does not reopen it. |
| The human's own message in that thread | Kept as their `reply`. A reply posted through Tico comes back once and joins itself. |
| Anything else | Dropped unread: nothing is stored. |
| A channel shared outside the company | The mention is `ignored` and never shown. |
| A DM or group DM, even one that names them | Never read: the app has no DM scopes, and the gateway drops any that arrive. The people writing there expect a DM to reach only that human. |
| A bot's message, an edit, a deletion | Ignored. |

The context read for a mention: the channel's name, permalinks, and the thread so far (or, for a
message nobody has replied to, the channel's last messages up to it), with every @name spelled
out. A Slack call that fails leaves that piece out; nothing waits.

## Who sees it

A mention is its human's: `GET /api/v2/mentions` returns only the caller's, the owner included,
and bots are refused. It is not a task: nothing runs, nobody is notified, the Needs you list and
the task sweeps never see it. `hub sql` does not name the `mentions` and `mention_items` tables,
so it denies them to everyone.

| API | Does |
|---|---|
| `GET /api/v2/mentions?since=<updated>` | `{mentions, now, connected}`: the caller's open mentions and those done in the last two weeks; with `since`, only what changed after it. `connected` says whether their Slack is set up for mentions (both tokens are in the vault), so a frontend can hide an empty section. |
| `GET /api/v2/mentions/{id}` | One, with its items and context. |
| `POST /api/v2/mentions/{id}` `{status, bot}` | `done` or `open`; `bot` records whose context drafts it, once. |
| `POST /api/v2/mentions/{id}/reply` `{text}` | Queues the reply; the gateway posts it as the human in the thread, escaped, once. The Assistant acting for the human cannot send one. |

A queued reply goes `ready` → `sending` → `sent` (with its permalink), `failed` (Slack's error) or
`uncertain` (a network fault or a restart mid-send: check the thread, never resend blindly).

## Connecting a human

The tokens live in the credential vault ([credential-vault.md](credential-vault.md)), so this
needs a vault. `connectors/slack-mentions-app-manifest.yaml` is the app, with the steps: one
app-level token for the company and one user token per human, each a credential granted to
nobody whose bot variable name is `SLACK_MENTIONS_APP_TOKEN` or `<HUMAN>_SLACK_USER_TOKEN` (their
Tico id in capitals, `-` as `_`). The gateway reads the vault every five minutes. A user token is
used only if Slack says it is a person's token in this workspace whose verified email is the one
Tico has for that human.
`python -m backend.slack_gateway --check` lists who is connected.

A credential administrator can reveal these tokens, and a user token can read everything its
human can, so only the human should store their own.
