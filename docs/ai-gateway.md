# Tico AI Gateway

The Tico AI Gateway is Tico's own model service. With a gateway key from Tico, your bots can run on **Tico Cheap 1.1**,
**Tico Mid 1.0** and **Tico Power 1.0**, and Decide asks the Gateway with the same key. You connect it once, in
**Settings → Computers → Models**. How runs reach the Gateway, for developers, is in
[Harnesses](harnesses.md#the-tico-ai-gateway).

| Model | What runs underneath |
|---|---|
| Tico Cheap 1.1 | Claude Haiku 5.5 |
| Tico Mid 1.0 | Claude Sonnet 5.5 |
| Tico Power 1.0 | Claude Opus 5.5 |

Tico Cheap 1.1 costs the same as Cheap 1.0 for a request whose prompt is up to 100,000 tokens. Above that, the whole
request costs $0.75 per 1M input tokens, $0.075 per 1M cached input tokens and $3.75 per 1M output tokens, and
**Usage** estimates it that way. Cheap 1.1 replaced Cheap 1.0, which is retired: a bot already on Cheap 1.0, or a
default model saved as Cheap 1.0, keeps it and keeps running at its old price, but nothing new can pick it.

## Choose how bots get their models

At the top of **Settings → Computers → Models**, and in the first step of setup, the owner picks one of four choices:

- **Your Claude and ChatGPT subscriptions, plus Tico Gateway** (recommended). Bots do their main work on your Claude
  and ChatGPT subscriptions. Personal plans are fine. Sign in with your own Claude and ChatGPT accounts in
  **Settings → Computers**, and don't share one login across your team. Until you sign in, bots run on Tico Mid 1.0
  through the Gateway, and they move to your subscriptions on their next turn once you do, with no restart. When a
  plan runs out, the bot finishes on the Gateway, after any other provider signed in on its computer. Decide and
  update drafts run on the Gateway.
- **Your Claude and ChatGPT subscriptions only**. Bots wait until a Claude or ChatGPT account is signed in on their
  computer, and never spend the gateway key unless you pick a Tico model for them.
- **Tico Gateway only**. Bots run on the Tico models you pick for them. A bot whose plan runs out finishes on the
  Gateway too.
- **Your company's plans or API keys**. Bring your own: tick the providers, then sign in or add API keys in
  **Credentials**. As with subscriptions only, the gateway key is spent only for a bot you put on a Tico model.

Tico's installer sets the recommended choice (`TICO_MODELS_PRESET=subscriptions-gateway` in the server's environment)
until the owner saves another one here.

While the recommended choice waits for a sign-in, the owner sees one **Needs you** item, "Sign in to Claude and
ChatGPT, or add API keys", with **Open Settings → Computers**. It goes away by itself once both are signed in on a
computer. Gateway work runs only on a computer that runs bots as their own user (Tico's Docker runner, see
[below](#pick-a-tico-model-for-a-bot)). When no computer here can, the item also says "This computer can't run Gateway
work without the Docker runner; sign in to Claude and ChatGPT or add API keys", and bots wait for a sign-in instead of
running on the Gateway. They never fail for it. The missing sign-in is the only thing the Gateway stands in for: a bot
whose checkout is missing, whose settings differ from the server's, or whose computer doesn't have pi waits as before,
and Health says which.

When a used-up login has its bots on the Gateway, **Settings → Health** says so in one line, for example "Team box's
Claude login is used up. Bots are on Tico Gateway until it resets." When the bot's computer can't run Gateway work,
Health says the bot stopped and what to do: sign in another login, or add a Docker runner. When the gateway key reaches
its monthly cap, the Gateway work stops and Health says **Gateway cap reached** ([What the card says](#what-the-card-says)).

## Connect the Gateway

Only the owner can do this.

1. Open **Settings → Computers → Models**. The **Tico AI Gateway** card is under the providers.
2. Paste the key Tico gave you in **Gateway key**.
3. Leave **Gateway URL** empty to use the default, `https://updates.tico.team/v1/gateway`. Enter another only if Tico gave
   you one; it starts with `https://` (plain `http://` only for `localhost`). A URL set in the server's environment
   (`TICO_GATEWAY_URL`) follows the same rule, or the key is not sent and the card says **Not working**.
4. Press **Connect**.

The card shows **Connected**, and Tico Cheap 1.1, Mid 1.0 and Power 1.0 appear in the bot model list. The key field empties as
soon as you press **Connect**, and the key is never shown again: the field shows only a short preview, its first and
last three characters. If the card says **Not working** or **Not ready**, see [What the card says](#what-the-card-says).

Once connected, the card has three buttons:

- **Save**: saves a new key or URL. Leave the key field empty to keep the saved key.
- **Check now**: asks the Gateway about the key again.
- **Clear key**: removes the key. Bots on Tico models stop until a key is saved again, and the Tico models leave the
  model list.

If the server's environment sets the key (`TICO_GATEWAY_KEY`), that key wins: the card shows **Connected**, says the
key is set by the server's environment, and has only **Check now**. Change it where the server is configured.

People other than the owner see only whether the Gateway is connected.

### Connected automatically

When Tico invites your company, the install command carries an invite code (`TICO_GATEWAY_INVITE`). The server then
gets its own gateway key from Tico when it starts, with nothing to paste, and the card says **Tico AI Gateway: connected
automatically**. Nobody types or sees the code or the key. The server replaces the key with a new one every 30 days by
itself. The invite is only needed for this first sign-up: after it, the server keeps its key with no invite in the
environment.

When the key rotates, a running bot keeps its previous key for up to 24 hours, with the same install spending cap.
Turning the install off still stops all its keys immediately. A run that could exceed that grace is limited to just
under 24 hours, including any fallback; its run details explain the shorter limit.

- A key you paste on the card, or one the server's environment sets, wins over the automatic key.
- **Clear key** removes the automatic key, and the server does not get a new one by itself. **Connect automatically**
  on the card turns it back on.
- If Tico turns the Gateway off for your install, Health says "Tico AI Gateway was turned off for this install by
  Tico. Contact support." Decide and bots on Tico models stop, and the server stops asking for a key.
- If the invite code doesn't work (expired, used by all the installs it allows, or cancelled), Health says so. Ask
  Tico Team for a new code and run the install command again with it, or paste a gateway key on the card.

The Gateway counts each call against the install, its key, the model and the bot. It gets an id for the bot that only
your server can make, never the bot's name.

## Pick a Tico model for a bot

While the Gateway is connected, pick **Tico Cheap 1.1**, **Tico Mid 1.0** or **Tico Power 1.0** in **Settings → Bots**
like any other model ([Harness, model, effort and fallback](creating-bots.md#7-harness-model-effort-and-fallback)). The
model list says what runs underneath, for example "Tico Mid 1.0 (Claude Sonnet 5.5 underneath)".

No bot ever holds the gateway key. For a bot on a Tico model, or with a Tico model as its own fallback, the computer
running it fetches the key and passes the bot's model calls on to the Gateway. The bot gets a pass that works only for
that run and only for model calls. That is why Tico models need a computer that runs bots as their own user: Tico's
Docker runner does, and says so when it checks in. On a computer that says it runs bots as itself (a Mac), the model
list marks the Tico models **not on this computer** and Tico doesn't save one for a bot there: the reason says
"<computer> can't run Tico Gateway work (it needs Tico's Docker runner)". A runner from before it said either way is
treated the same, with "<computer> hasn't said it can run Tico Gateway work": update its runner. A bot already on a
Tico model there doesn't start and doesn't fail: its work waits in the queue, and Health says why, until you pick
another model, move the bot or update the runner. A Tico fallback is left out there: the bot's own model's outcome
stands.

If a runner refuses a Tico turn anyway (it said it runs bots as their own user, but can't when the turn starts), the
bot stops once with "Tico models need a runner that runs bots as their own user (Tico's Docker runner). This computer
can't, so pick another model or move the bot", and the run is saved for later rather than tried again. The bot's
status and **Needs you** say so.

## What the card says

Tico asks the Gateway about the key when you save it, when you press **Check now**, when the owner opens the page
(at most once a minute) and every hour. The card shows the Gateway's last answer, and **Settings → Health** shows the
same warnings to the owner, with **Open Models**.

| The card says | What it means | What to do |
|---|---|---|
| **Connected** | The key works. | Nothing. |
| "This key has used 80% of its monthly Gateway cap. Bots stop at 100% until next month." | The key is near its monthly cap. The number is the share used. | Nothing yet, or ask Tico for a higher cap. |
| **Gateway cap reached** | The key has used its monthly cap. Bots on Tico models stop until next month, or until Tico raises the cap. | Wait for the next month (UTC), or ask Tico to raise the cap. |
| **Not working**: key not recognised | The Gateway doesn't accept the key: it was revoked, or it was mistyped. | Check you pasted the whole key, or ask Tico for a new one and save it. |
| **Not ready** | The Gateway at that URL doesn't serve Tico models yet. | Check the **Gateway URL**, or wait until Tico turns the models on. |
| "Not checked: the Gateway did not answer." | The Gateway was unreachable. This says nothing about the key. | Nothing. Bots keep trying; check again later. |

## When the Gateway refuses a bot's run

The bot's status says why, in plain words, and the run waits or is saved for later. The same holds when a Tico model ran as the bot's own fallback:

| The bot's status says | What happens to the run |
|---|---|
| **Gateway cap reached** | Waits until the next month (UTC). |
| The Tico AI Gateway key is not working; save a new one in Settings > Computers | Waits for a new key. |
| Tico AI Gateway rate limited | Retries after the Gateway's requested wait, for the key's minute rate window or an address block. Update the server and its computers to use that wait; older computers without it wait at least an hour. |
| The Tico AI Gateway doesn't offer this model | Saved for later, not retried. Pick Tico Cheap 1.1, Mid 1.0 or Power 1.0. |
| Out of credit | Your company's balance is at its minimum. Tico models stop and the bot falls back to its other models ([Credit and the minimum balance](#credit-and-the-minimum-balance)). |
| The Tico AI Gateway refused the request as sent, with the Gateway's reason | Saved for later, not retried. |
| No Tico route for this request | Saved for later, not retried. |
| The Tico AI Gateway doesn't serve Tico models at its URL yet | Saved for later, not retried. Check the **Gateway URL**. |
| Tico AI Gateway is unavailable, try again | Back in the queue if nothing ran yet: tried up to three times, waiting a minute, then two. After the third try, or if the run had already acted, saved for later. |

If the Gateway cuts a reply off after the bot started answering, the bot posts what it had written, with a note
saying the Tico AI Gateway cut the reply off.

## Credit and the minimum balance

Gateway use is charged to your company's balance once an hour: one charge for each model price line used in that hour.

Companies on the waitlist have the balance enforced:

- When the balance reaches the minimum, Tico models stop. The minimum is $0 unless Tico staff set another. The next
  call is refused with "out of credit", and each bot falls back to its other models. Use can run over the minimum by
  up to about 5 minutes before the stop takes hold.
- A company is paused at the minimum exactly: $0.00 with a $0 minimum is paused. It runs only while the balance is
  above the minimum.
- Add credit, or ask Tico staff to lower the minimum, and Tico models work again within about 5 minutes, usually
  within a minute.

Companies that are only recorded are never stopped; their use is charged to the balance and nothing more.

The stop sends no email by itself. The out-of-credit email is a separate feature, Credit notices.
