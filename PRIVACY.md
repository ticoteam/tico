# Privacy

What a Tico install sends outside your own server, and what the receiving side keeps: the anonymous usage count, the
suggestions while you build your team chart, and, only when you press Send, a support ticket. Each has its own switch.
The setup page on tico.team, which you use before installing, has its own section ([below](#the-setup-page-on-ticoteam)).
Ask Tico, the chat on tico.team's pages, has its own section too ([below](#ask-tico-the-chat-on-ticoteam)).
Separately, the server calls AI model providers with your own keys for a few features ([below](#the-servers-own-model-calls)). Bots you set to use Tico Cheap, Mid or Power send their model calls through Tico HQ to OpenRouter ([below](#tico-cheap-mid-and-power-through-tico-hq)). Tico's source is available, so you can read the code that
sends them (`backend/census.py`, `backend/releases.py`, `backend/recruit.py`, `backend/support.py`). The service that receives them,
Tico HQ, is run by the Tico team from a separate, private repository; its code is not published. What it is sent and what it keeps is described in [docs/tico-hq.md](docs/tico-hq.md).

## The anonymous usage count

Tico counts how many installs are in use, so the project can tell whether it is helping anyone. It does this the way
Homebrew, Next.js and Astro do: anonymously, in the open, and with a switch you control.

### What is sent

Your Tico looks for a new release about every six hours. With counting on, it asks Tico HQ instead of GitHub, and the
request carries exactly these five fields and nothing else:

```
GET https://updates.tico.team/v1/latest?install_id=6f1c2a9e-3b7d-4c58-9a10-2d4e8b7f5a63&version=0.2.15&active_people=true&active_bots=false&first_result=true
```

| Field | What it is |
|---|---|
| `install_id` | A random UUID made once and stored in your database. It is not derived from your domain, team, computer or anything else. You can replace it at any time. |
| `version` | The Tico release you run, for example `0.2.15`. |
| `active_people` | `true` if at least one human used Tico in the last 7 days, otherwise `false`. |
| `active_bots` | `true` if at least one bot run finished in the last 7 days, otherwise `false`. |
| `first_result` | `true` once a run of one of your own bots has ever finished on this install (the built-in Assistant, BotOps, Librarian and Strategy Bot do not count), otherwise `false`. It lets the project measure how long a new install takes to reach its first working bot. |

That is all. No team name, domain, email address, humans, bot names, counts, content, hostnames or file names. Your
IP address is unavoidably seen by any server you connect to (GitHub sees it today), and HQ handles it as described below.

### When

Only while counting is on, only from a real install (never in demo mode, never from a source checkout that reports
`dev`), and only after the owner has been shown the notice. A new install shows it in the installer and once in the web app, on the
first page after the setup wizard (never over the wizard or the screen after **Create**); an existing install shows it in the web app when it updates to 0.2.15, and nothing is
sent before the owner has seen it.

### What HQ stores

For the usage count, one row per install ID, and nothing else:

| Column | Value |
|---|---|
| `install_id` | the random ID |
| `first_seen_at` | the UTC day the ID first arrived |
| `last_seen_at` | the UTC day it last arrived |
| `last_version` | the version in the last request |
| `last_people` | the last UTC day `active_people` was true |
| `last_bots` | the last UTC day `active_bots` was true |
| `first_result` | the first UTC day `first_result` was true |
| `result_from_start` | whether the ID's first request already carried `first_result`, so its wait for a first result is known |

- **No IP addresses are stored or logged.** HQ uses the address in memory only, as a salted hash forgotten when the
  process restarts, to limit each address to 60 requests an hour. Its access log is switched off, so no address and no
  query string reaches a log. There is no ping history, user agent or other column.
- **Request counts, in memory only.** HQ counts how many requests it answered per route group (support, support
  access, recruit, everything else) and result class (2xx to 5xx): 16 numbers and nothing else, with no install ID,
  address, path or time. They are never logged or written, and they are gone when HQ restarts.
- Every field is checked strictly (a UUID, a version number, the words `true` or `false`); a request with anything
  else is refused and stores nothing. Extra fields are ignored and never stored.
- **Retention:** an install row not heard from in 13 months is deleted, by a job that runs daily. This rule is for these rows only;
  support tickets are kept as described below.
- The database is on one server run by the Tico team, separate from any customer's Tico.

### What we publish

`https://updates.tico.team/v1/stats` is public. It shows the number of installs that have tried Tico (a human and a bot
active at some point), that were active in the last 7 days, and that are still running bots 30 or more days after they
first appeared, plus a count by major version. Counts by version under 5 are left out. Those are the numbers behind
Tico's goal of 100 teams trying it and 100 still using it after 30 days.

### Turn it off

Any one of these is enough. When counting is off, the update check still works: it asks GitHub directly, exactly as
before, with no ID and no flags.

1. **In the app:** Settings > General, switch off "Help count active installs" (the owner).
2. **Environment:** `TICO_TELEMETRY=off` in your `.env`.
3. **The standard:** `DO_NOT_TRACK=1` in your environment ([consoledonottrack.com](https://consoledonottrack.com/)).
4. **The command line:** `docker compose exec server python -m backend.manage usage-count /data/hub.sqlite off`.

Other controls: **Reset install ID** in Settings > General (or `... usage-count /data/hub.sqlite reset-id`) makes a new
random ID, so the old one no longer connects to your install; do it when you clone a server.
In the app, **Reset install ID** asks you to confirm first. **Cancel** keeps the current ID. `TICO_UPDATE_CHECK=off`
stops the update check altogether, and with it the count. `TICO_HQ_URL` points the check at your own HQ.

At each start of a release image, the server also asks GitHub which commit that release's tag names
(`GET api.github.com/repos/ticoteam/tico/commits/v<version>`, nothing else in the request) to check its image was built from it
([flight recorder](docs/observability.md#the-flight-recorder)). `TICO_UPDATE_CHECK=off` stops this too.

### Check it yourself

- `TICO_TELEMETRY_DEBUG=1` prints the exact request Tico would send to the server log and sends nothing.
- `docker compose exec server python -m backend.manage usage-count /data/hub.sqlite payload` prints the same five fields.
- The sending code is `backend/census.py` and `backend/releases.py`; the collector is Tico HQ ([docs/tico-hq.md](docs/tico-hq.md)). A test
  (`backend/tests/test_usage_count.py`) fails if the request carries any field beyond these five.

More on why it is built this way: [docs/telemetry.md](docs/telemetry.md).

## Suggestions while you build your team chart

When you set up Tico, the team chart step asks one short question per group ("What kind of sales do you do today?") and
suggests bots for it. The team chart builder has one toggle, **Suggestions from Tico HQ**, and its setting covers every group you answer. While it is on, your
Tico server (never your browser) sends the group, your answer, team description, customer type, software choice,
catalog version and, when counting is on and its notice has been shown, install ID to Tico HQ. The exact fields are listed below. HQ ranks the group's bot templates with a language
model and says why each fits. While it is off, your own server ranks them locally and nothing is sent. If HQ does not answer
within 6 seconds, your server ranks them locally after attempting to send the request.

### What is sent

One request per group you answer, and only while the toggle is on:

```
POST https://updates.tico.team/v1/recruit
{"department": "sales",
 "briefing": "Inbound demos and a few big accounts",
 "about": {"what": "We sell scheduling software to clinics", "sells_to": "businesses", "software": true},
 "catalog_version": "ee9999fd8f2a",
 "install_id": "6f1c2a9e-3b7d-4c58-9a10-2d4e8b7f5a63"}
```

| Field | What it is |
|---|---|
| `department` | Which of the nine groups you are answering for |
| `briefing` | Your answer to that group's question, at most 500 characters |
| `about.what` | "What you do" from About the team, cut to 500 characters |
| `about.sells_to` | `businesses`, `consumers`, `both`, or empty |
| `about.software` | Whether software is your product |
| `catalog_version` | Which release's bot templates you have, so HQ answers with templates you have |
| `install_id` | Only while the anonymous usage count is on and its notice has been shown: its random install ID, used for a rate limit and nothing else |

Nothing else: no team name, domain, humans, email addresses or bot names. HQ answers with template ids and a one-line
reason for each; your server keeps only ids that are in its own templates.

### What HQ keeps

**Nothing.** HQ has no table for these requests and writes nothing to disk for them.

- It never logs a request body. If the model call fails, only the kind of error is logged.
- It keeps an answer in memory for an hour, under a one-way hash of the question, so the same question costs nothing twice; the
  question itself is not kept even there. A restart forgets all of it.
- Your address is used in memory, as a salted hash, only to limit how many requests it can make; it is never stored or logged.
- The model is called with storage switched off (OpenAI's `store: false`). It sees the group's template list, your answer and
  the three facts above, and nothing else. HQ calls it at most a fixed number of times a day; past that, HQ ranks locally.

### When it is off

The toggle starts on and you can switch it off. It is off, and cannot be switched on, when any of these holds;
the toggle says which:

- the anonymous usage count is switched off in Settings;
- `TICO_TELEMETRY=off` is set;
- `DO_NOT_TRACK` is set (to any value other than empty, `0`, `false` or `no`);
- `TICO_REHEARSAL=1` is set (rehearsal mode);
- Tico runs in demo mode.

With it off, the suggestions come from `backend/recruit_rank.py` on your own server, and nothing about your answers leaves it.
Your answers are saved in your own database with the rest of setup, so the bots you create can read them.

`TICO_HQ_URL` points your server at another HQ, such as one you run yourself.

## The setup page on tico.team

[tico.team/setup](https://tico.team/setup) helps you plan your AI team before you install anything. It runs in your
browser and calls Tico HQ at `https://updates.tico.team/v1/onboarding`. No install is involved, and nothing here comes
from or goes to a Tico server you run.

### What is sent

- `GET /v1/onboarding/health`: whether Tico's assistant can answer. Nothing is sent with it. Until the assistant
  launches it answers `{"ok": false}`, and the page plans in your browser.
- `POST /v1/onboarding/turn`, only while Tico's assistant is on: your message, the tab you are on and your plan, with the
  chat's last 8 turns, which HQ sends through OpenRouter to the chat's model (Inception Mercury 3 by default) for the
  answer, with data collection denied and zero retention; key-shaped text is removed first, and HQ keeps none of it and
  logs no text.
- `POST /v1/onboarding/event`, when you send a message or download your company file, and nothing else:

```
{"session_id": "3f6c1a2e-9b7d-4c58-9a10-2d4e8b7f5a63", "kind": "download"}
```

| Field | What it is |
|---|---|
| `session_id` | A random ID the page makes for this planning session (8 to 64 letters, digits, `-` or `_`). One shaped like a key or token is refused before anything is done with it |
| `kind` | `message` (that you sent one; not what it said), `download`, or `step` with `step`, a number from 0 to 9 for how far you got |

No message text, plan, company name, email address or cookie. Only `https://tico.team` may call these routes from a page.

### What HQ keeps

What we keep: counts for each session: steps reached, whether the file was downloaded, how many turns, and what the
session cost. No message text.

| Column | Value |
|---|---|
| `session_hash` | a salted one-way hash of the session ID, so a session is one row |
| `day` | the UTC day the session was first seen |
| `step` | the furthest step reached |
| `downloaded` | whether the company file was downloaded |
| `messages` | how many messages you sent; never what they said |
| `turns`, `cost_micro_usd` | how many turns the assistant answered, and what they cost; zero until the assistant launches |

- **Counts are off unless HQ is told to keep them** (`HQ_ONBOARDING_COUNTS=on`). While off, HQ answers the same and
  stores nothing.
- **No message text is stored anywhere**, and the table has no column for it. Keys and passwords are never stored, in
  any form: a session ID shaped like one is refused before it is hashed.
- **No IP addresses are stored or logged.** HQ uses the address in memory only, as a salted hash, for the same limit
  of 60 requests an hour as every other route.
- **Retention:** a session row is deleted 13 months after its day, by the daily job.
- The Tico team reads daily totals only (sessions, sessions with 2 or more messages, how many of those downloaded the file, downloads, cost), never a session's row.

## Ask Tico, the chat on tico.team

**Ask Tico** is the chat panel on tico.team's pages (not on /setup). It answers questions about Tico and suggests one
way to use it: Tico Open Core, Tico Services or Fully managed. It runs in your browser and calls Tico HQ at
`https://updates.tico.team/v1/sdr`. No install is involved. It has no tools: it cannot look anything up about you, act
for you or reach a Tico server you run. Only `https://tico.team` may call these routes from a page.

### What is sent, and to whom

- **To Tico HQ:** each message you type (up to 2,000 characters; a longer one is refused and sent nowhere) with a
  random ID the panel makes for this chat (`crypto.randomUUID()`), and nothing else: the panel adds no name, email
  address, page history or cookie. Anything you type yourself, a name or an email address included, is part of the
  message.
- **To TypeSafe** (api.typesafe.ai, the provider of the Jev decision model), only when the Tico team has set Ask
  Tico's screening key on HQ (`HQ_SDR_DECIDE_KEY`): your current message as you wrote it, and nothing else (no chat
  ID, no earlier messages), once, to check whether it is spam, abuse, an attempt to instruct the chat, or off-topic
  before the model sees it. A message it judges so is answered with a fixed line and sent nowhere else. If TypeSafe
  does not answer within 3 seconds, or is not sure, the message goes on unchecked. With no key set, nothing is sent to
  TypeSafe.
- **To OpenRouter** (openrouter.ai), which passes it to a provider that serves the model (Inception's Mercury 3 today;
  the Tico team can change it): a fixed instruction, Tico's sales brief, up to four excerpts of Tico's public manual
  that match your message, your current message and up to 20 previous messages from this chat, as you wrote them.
  HQ adds nothing about you (not the chat ID, your IP address or any other detail): anything personal in the call is
  what you typed. HQ calls OpenRouter with one of its own OpenRouter keys (Ask Tico's own key, or HQ's shared one
  when that is not set; either way OpenRouter receives the same call) and the same two filters on every call, which
  are not a setting: no provider that may keep or train on data (`data_collection: deny`), and only endpoints
  OpenRouter classes as zero data retention (`zdr: true`). If no provider meets them, the call is refused and the
  chat rests; it is never sent again with looser filters. OpenRouter's policy does not count a provider's in-memory
  prompt caching as retention
  ([OpenRouter: zero data retention](https://openrouter.ai/docs/guides/features/zdr#caching)).

### What HQ keeps

- **The chat, in memory only,** under a salted hash of its ID, so the next message has the conversation. It is
  erased 30 minutes after its last message, and a restart or deploy of HQ erases every chat. Nothing of the chat is
  written to HQ's database, and no message text is logged.
- **No counts** of Ask Tico chats are kept yet, except how many messages the screening passed, refused or could not
  check, in memory until HQ restarts: numbers only, never the text, the chat ID or TypeSafe's answer.
- **No IP addresses are stored or logged.** HQ uses your address in memory only, as a salted hash, for the limit of
  60 requests an hour every HQ route has.
- **No cookie or tracker** is set by HQ.

## The server's own model calls

Bots call their models from the computers they run on. The server itself calls a model for a few things, only with keys
you set on the server, and never in rehearsal mode. Each provider keeps what it receives under its own terms.

| What | What is sent | To whom |
|---|---|---|
| **Decisions** (`POST /api/v2/decisions`, `hub_decision_ask`, scoring saved listening posts, live-meeting routing, the nightly learning run) | The JSON state and the typed questions the caller sends: whatever it puts there, which can be a mail's subject and body or a stretch of a transcript. The nightly learning run sends each of the day's items (chat and task text, Slack channel messages, mail and meeting excerpts) with the profiles of the bots it may reach | In this order, each one only when the one before is not set up or fails: the Tico AI Gateway on Tico HQ (Tico Decide 1.0, which asks TypeSafe's Jev; HQ keeps the label, counts, tokens, latency and cost per gateway key, never the state or the questions) when the server holds a gateway key (`TICO_GATEWAY_KEY`); TypeSafe (api.typesafe.ai, the Jev model) directly when you set your own `TYPESAFE_API_KEY`; the team's own provider from Settings > Computers > Models whose key the server holds: Anthropic, OpenAI, Google (Gemini), xAI or OpenRouter (which also serves the DeepSeek, Kimi, Llama and Mistral models). With none of them nothing is sent |
| **Slack routing** (`backend/slack_gateway.py`, when the Slack gateway is on) | The message's text; the sender's name, roster id, team and the bots they are primary for; the channel's name and registry purpose; the last twelve exchanges in the thread and their previous routing; and the active roster (each bot's name, team, description and manager) | The same decision model as above |
| **Topic changed** (`backend/topic_change.py`, when Settings > General > **Start fresh when the topic changes** is on) | Before a message in a person's direct chat with a bot is sent, and only when the chat is long or idle: the chat's last 3 turns since its last clear (what the person said and what the bot answered, each cut to about 1,500 characters) and the new message. Task lines, task chats, group chats and shared bots' rooms are never sent. The audit keeps the answer, never the text | The same decision model as above |
| **Market ask** (`POST /api/v2/market/ask`) | The question, and up to about seventeen excerpts from the market graph that match it: entity names, aliases and summaries, evidence quotes and our reads, and the start of matching market pages | Google's Gemini API (generativelanguage.googleapis.com, `gemini-3.7-flash`) with the server's `GEMINI_API_KEY`, whatever Settings > Computers > Models says. Without a key nothing is sent and the pulled excerpts are the answer |
| **Strategy research** (`POST /api/v2/strategy/draft-from`, the owner during setup) | The website address and the description the owner typed, and the text of up to four of that website's public pages (home, about, pricing, customers; 8,000 characters each). Fetching those pages is a plain request from the server to that website, with no credentials | Tico Mid 1.0 on the Tico AI Gateway (Tico HQ, then OpenRouter, [below](#tico-cheap-mid-and-power-through-tico-hq)) while the gateway is connected and chosen for the gateway models, else the team's own provider from Settings > Computers > Models whose key the server holds. With neither nothing is sent and setup is a plain form. The server keeps the job (address, description, page addresses and titles, and the model's answer) in its database, the last five jobs |

What the server keeps of each call is an audit event, never the text that was sent: `judge.call` (the label, the number of
questions, the value and confidence per answer, the model, token usage and latency; Slack routing adds the Slack event id)
and `market.ask` (the model, the latency, how many excerpts and how long the question was). Each actor has a daily cap on
these calls (`TICO_DECISIONS_DAILY_CALLS_OWNER`, `_HUMAN`, `_BOT`; README.md).

## Tico Cheap, Mid and Power through Tico HQ

Only when your team has a Tico AI Gateway key (Settings > Computers > Models, or `TICO_GATEWAY_KEY`) and a bot is set to
a Tico model (Tico Cheap, Mid or Power). That bot's computer then sends each of its model calls, with the gateway key, to Tico HQ
(`https://updates.tico.team/v1/gateway/chat/completions`), and HQ sends it on to **OpenRouter** (openrouter.ai) with
HQ's own OpenRouter key. OpenRouter passes it to a provider that serves the Claude model behind that Tico model (Haiku,
Sonnet or Opus). Without a gateway key, or with the bot on another model, nothing goes this way.

**What OpenRouter is sent:** the call's messages (the system, user, assistant and tool text the bot sends, which can be
anything it has read: a task, a chat, a mail, a file's text) and its tools, with the tool calls, tool choice, output
limit, stop words, reasoning effort and answer format. That is all. Not your gateway key, its label or your install ID,
and not a caller's own id or metadata (`user`, `metadata` and fields like them are dropped at HQ). Images, audio and
files are refused before anything is sent.

**What HQ keeps:** none of the call's content. No message, tool or answer is stored or logged, and an error from
OpenRouter is answered with a fixed code, never its words. Separately, HQ keeps your gateway key's record: a hash of the
key and the label the Tico team gave it, which the team sees in its key list and usage reports. Per call, HQ writes
the Tico model, its token counts, cost and price (added up per key and month, kept 13 months, for the monthly cap) and
one route-log row: the time, the route, the outcome code and how long it took (kept 13 weeks). Your address is used in memory only, as a salted hash, to slow down an address that keeps
sending unknown keys; it is never stored or logged.

**What OpenRouter and the provider keep** is under their own terms. Zero retention is enforced only when a call asks
for it (`"filters": {"zero_retention": true}` in the request): then HQ sends OpenRouter's zero-retention preference, so
only an endpoint OpenRouter classes as zero data retention may serve the call, with data collection denied; if none can,
the call fails rather than going to one that keeps data. OpenRouter's policy does not count a provider's in-memory
prompt caching as retention, so such an endpoint may still cache the prompt in memory
([OpenRouter: zero data retention](https://openrouter.ai/docs/guides/features/zdr#caching)). A call that does not ask
gets OpenRouter's and the provider's normal retention, and Tico's bots do not ask for it today.

**Where it runs:** HQ's route through OpenRouter has no confirmed region, so these calls may be handled outside the
US. A call that asks for the US (`"filters": {"region": "us"}`) is refused with `no_route` and sent nowhere, until
a route confirmed to stay in the US exists.

**A server Tico hosts** (`TICO_HOSTED`) also tells Tico HQ, with the same gateway key, the release it runs, every 15
minutes (`POST <Gateway URL>/servers/update {"version"}`), and after an update Tico support scheduled for it, how that
went (`updated_at`, `failed`, `rolled_back` or `timed_out`, with the updater's message). HQ answers with the release
support scheduled, if any (`backend/hosted_updates.py`). A self-run server never sends it.

## The AI accounts your computers are signed in to

This stays on your own server; nothing here is sent to HQ or added to a support ticket's diagnostics.

A current computer reports which account each AI tool is signed in to, per named profile, so **Settings → Computers**
can show it: the account's email address, plan (for example Max or Pro), organization name, whether it is an API key
rather than a subscription, and when that was checked. It is the first time Tico stores an account identity; until
now it kept only whether a login was signed in. The computer reads these only from the tool's own status
answer: `claude auth status --json`, and Codex app-server's `account/read`, which it always asks without a token
refresh. It never reads a credential file (`.credentials.json`, `auth.json`, the keychain) or the text of
`codex login status`. Every other field in those answers is dropped, and a value that looks like a key or token is
left out on the computer and refused again by the server. The server keeps the latest report with the computer's
readiness and profiles, replaces it on each heartbeat, and drops it when the tool signs out.

Only owners, administrators and the computer's operator see an account. Other members see that a tool is signed in,
not as whom, and bots never see one. An older computer reports none of this, and the page shows what it showed before.

## Computer performance

This stays on your own server. It reaches Tico only inside a support ticket's diagnostics, when you leave it in.

Every 30 seconds a computer reports its CPU and memory use, load, swap, the free space on its volumes, how many turns
were running, and its busiest processes by name: the program's own file name (`cargo`, `node`), cleaned to letters,
digits and `._+-`, never its arguments, path, environment or owner. The server keeps one-minute points for two days and
ten-minute points after that, and deletes everything 14 days old, or at once on **Delete history**. Every person and bot
of the company can read it. The computer's operator or an admin turns it off per computer; an off computer reports nothing.

## Support tickets

Help > **Support** sends the Tico team a message. It is separate from the count, and it works whether counting is on or
off: it is a message you write and send yourself, never automatic, never in demo mode, and only from a signed-in human's own
browser (not a token, the Assistant or a bot). A bot, the Assistant or a person's token may instead draft a report as an
approval: it is sent only after a person reads the whole message in Needs you and approves it, then is filed as that person,
with the message and, unless the draft says otherwise, the version and install ID (never an email or diagnostics;
[docs/support.md](docs/support.md#a-bots-report-after-a-persons-yes)). `TICO_SUPPORT=off` removes both from an install.

**What is sent**, shown in one line on the form before you press Send, and only these fields:

| Field | |
|---|---|
| `message` | What you wrote, up to 4000 characters. Required. |
| `email` | Only if you leave one in the field, so the team can reply by email. It is prefilled from your account and you can clear it. |
| `version`, `install_id` | Only while "Include version and install ID" is ticked, which it is by default. The install ID is the same random ID as the count's; it is made for a ticket if the count never made one. |
| `kind` | `setup` when you started from **Want help setting up?**, `feedback` when you answered **How is Tico working for you?**; left out for an ordinary request. |
| `diagnostics` | Only while "@diagnostics" is ticked: on by default for new requests, off for replies. **Review and edit** shows the exact attachment; edits are validated and redacted again before use. |

**From the installer or `tico-setup doctor`:** when setup fails at a terminal, it asks "Send this to Tico support?
[y/N]" and sends only on `y`: the failing checks and their fixes, OS, architecture and Tico version, redacted with the
diagnostics rules and the install's own names, an email only if you type one, and `kind: setup`. No install ID and no
diagnostics bundle. It prints a link to the request whose secret is in the part after `#`, which browsers do not send to
HQ. `--no-support` or `TICO_SUPPORT=off` turns the question off.

**From tico.team/setup (Talk to a person):** a visitor planning their team on the public setup page, with no Tico yet, can
ask for a person. The page sends only these, from that page's own address (HQ refuses the kind from anywhere else): `kind:
setup-website`, the email they type (required, so the team can answer), the tab they were on, whether the page's assistant
was connected (`mode`), how many messages they had sent, a random id for that visit, a note if they wrote one (up to 1,000
characters), and their plan (tico.company.yaml, up to 32 KB) only if they ticked to send it. Never the chat: HQ has no field
for it. Key-shaped strings (API keys, tokens, passwords) are removed from the note and the plan before anything is checked
or kept, and the page says so when one was. HQ keeps the email, the tab, the mode and the count as the ticket's text, and
the note and plan apart from it as an attachment marked Untrusted public input. The visit's id is used in memory only, as a
salted hash, to allow one ticket per visit (an id shaped like a key is refused, never hashed); HQ also allows 3 a day per address and per email address, and a daily total
(`HQ_SETUP_TICKETS_DAILY_CAP`, 50 by default). It is a support ticket like the others: kept until someone deletes it, as
below, not with the setup page's 7-day conversation log. The answer is a link to the request's page, with its secret after
the `#`, where the team's reply shows and where the visitor can answer or delete it.

**What HQ keeps**, per ticket: those fields (the diagnostics with the ticket, to a limit of 256 KB, shown to the Tico team only), a hash of a secret that only your Tico holds (it lets your Tico read this ticket's replies
and no other), the status (open, answered, closed), the times, the team's replies and any message you write back. No IP address is
stored or logged, and HQ's access log is off. The address is used in memory to rate limit (5 tickets an hour per address, 10 a day per
install).

**The spam and injection check:** when the Tico team has set a decision-model key on HQ (`HQ_JUDGE_KEY`), the text of each new ticket's `message`
(and only that: no email, install ID or diagnostics; for a setup-website ticket, its note and plan instead, after key-shaped
strings are removed, a long plan in parts) is sent once to TypeSafe (api.typesafe.ai, the provider of the Jev decision
model) to classify it as real, spam or an attempt to instruct a bot. HQ keeps the answer (one word and a short reason) with the
ticket and logs the same, never the text. With no key set, nothing is sent and the ticket is filed as usual. The same check runs, by the
Tico team's own bots, on GitHub issues and on email the team receives; it does not run in your Tico.

**Who reads it:** the Tico team, through a staff key, and the project's Support bot (a bot in the project's own Tico) that drafts a
reply which a human approves before it is posted. Ticket text is treated as untrusted text: stored as plain text, never rendered as
HTML, and never followed as an instruction.

**How long:** tickets are kept **until someone deletes them**. Nothing deletes a ticket by itself, closed or not. You can delete your own
with **Delete** in the selected Support request, which removes it from HQ and from your Tico; or ask the team to delete it (say so in the ticket, or
open an issue) and they do it with a staff route. A deleted ticket is overwritten in HQ's database file. HQ's optional Litestream backup
keeps older copies of the file for as long as its retention says, if you turned it on.

**Your Tico** keeps, per ticket: your message, the ticket's ID and secret at HQ, the replies, and whether you have read them. Only you
see your tickets; another human on the same install does not.

**Email:** HQ sends no email. If you gave an address, the reply is also drafted for a human on the team to send from their own email.

How the team works tickets: [docs/support.md](docs/support.md).

### Letting Tico support in

Only if an owner or admin starts it, from one of their own requests, for the time they choose (24 hours unless they pick
another; 30 days at most). A banner on every screen says it is on, and Stop ends it at once
([docs/support.md](docs/support.md#letting-tico-support-in)).

- **App only:** the Tico team sees what an admin sees in your Tico: settings, Health, computers, bots, tasks and their
  messages, run history and the audit log. It does not see credential values or a run's raw provider stream, and it changes
  setup only.
- **Full access** (the owner only): what the owner sees and does in Tico, except personal tokens, service keys and credential
  values.

Your Tico logs every request support makes (staff or the Support Agent, method, path, status) and shows the list under the
request. HQ carries each request and its answer through its memory without storing them; it keeps the session (ticket,
tier, start, end) and the same one-line record of each request, with the ticket, until the ticket is deleted. The Support
Agent treats everything it reads from your Tico as untrusted data, never as instructions. The whole feature is off unless the
server runs with `TICO_SUPPORT_ACCESS=on`, and also with `TICO_SUPPORT=off`, in demo mode and when the owner's team rule is
off. With `TICO_SUPPORT=off` or in a rehearsal, a session that was live ends without the install sending HQ anything.

### Support diagnostics

The bundle is a fixed list of facts, written out by name in `backend/diagnostics.py`. A field that is not on the list cannot be in
it. Collection never reads a task, message, doc, meeting or ticket body. You can edit the allowed fields before attaching them. Every string in it is redacted first: emails,
keys and tokens (`sk-`, `ghp_`, `gho_`, `xox[abpr]-`, `AKIA`, bearer tokens, JWTs, long base64 or hex runs), IPv4 and IPv6
addresses, URL query strings, your team's domain and every hostname that is not one of the product's own (`tico.team`, GitHub,
the model providers), and the names and emails of your bots and humans, which become labels (`bot-3`, `person-1`) that are the same
all through one bundle and mean nothing outside it. A name or slug is relabeled as a word from four characters; a shorter one (`coo`)
is relabeled only as an exact `bot:<slug>` or `human:<id>` reference, and an email is always relabeled or redacted. It is built when you press **Review and edit** (or Send) and what you previewed is what
is sent: Send names the preview by its SHA-256 and your Tico sends those exact bytes. Untick the box and none of it is sent. A local preview already built remains in memory for at most 15 minutes. Edited previews expire too; refreshing is an explicit action and never happens silently on send. HQ keeps follow-up attachments with their messages until the ticket is deleted.

Every field:

| Field | What | Redacted sample |
|---|---|---|
| `capture` | Server capture start, event/repeat/eviction counts, omitted entries, memory-only retention, and whether the sender edited the attachment | `{"server_repeats": 12, "truncated": 0, "edited": true}` |
| `browser` | Up to 20 in-memory browser failure groups: kind, UTC time, known app script filename, line/column, count. No error/rejection text, stack text, raw URL, console output or user input. Uploaded to your server only when building the attachment, and to HQ only on Send. | `{"kind": "error", "file": "app.bundle.js", "line": 42, "count": 2}` |
| `format`, `created_at` | The bundle's version and when it was made | `1`, `2026-10-01T09:30:00Z` |
| `versions` | Tico, server, each computer's release, the updater's | `{"tico": "0.2.18", "runners": ["0.2.17"], "updater": "v0.2.18"}` |
| `system` | The server's OS and architecture, Docker and compose versions if the updater is there, whether it runs in Docker | `{"os": "Linux", "arch": "x86_64", "docker": "27.1.2", "compose": "2.29.1", "in_docker": true}` |
| `containers` | Each container's name, state, health and restart count, from the updater | `{"name": "server", "state": "running", "health": "healthy", "restarts": 0}` |
| `update` | The last update's result: state, versions, the updater's message, whether the database was restored | `{"state": "rolled_back", "from": "0.2.17", "to": "0.2.18", "message": "Not updated: ..."}` |
| `health` | Each Health check by name and status; no summary text | `{"name": "Computers", "status": "warn"}` |
| `runners` | Per computer, numbered `runner-1`: online, last heartbeat time (`last_seen_at`), platform, kind, release, update state and error, each installed runtime (name, version, ready or not, its sign-in state and detail), how many bots are ready, their problem lines, and the computer's last 50 warning or error log lines | `{"label": "runner-1", "online": true, "runtimes": [{"name": "codex", "ready": true, "detail": "Signed in with ChatGPT"}], "problems": ["bot-2: repository missing at /Users/person-1/work"]}` |
| `database` | The migration level of the database | `{"migration": 12, "cloud_migration": 44}` |
| `features` | Which of these are on, as true or false only: `demo`, `updater`, `update_check`, `usage_count`, `backups`, `github_app`, `slack`, `sign_in_proxy`, `blob_storage`, `scheduler`, `observability`, `assistant`, `librarian` | `{"slack": false, "backups": true}` |
| `counts` | How many bots, humans and routines | `{"bots": 12, "people": 5, "routines": 30}` |
| `performance` | Only for the window chosen on the form (1 day by default; None sends none). Per computer, numbered `runner-1`: whole computer or container, memory and disk size, CPU and memory points (ten-minute points up to two days, hourly beyond), the overload summary and each overload's time, reasons, peaks, the bots running and the busiest process names, the bots with the most turns and their models, and the busiest processes | `{"label": "runner-1", "summary": "Overloaded once in the last 1 day (CPU over 90%) ...", "bots": [{"bot": "bot-2", "model": "claude-opus-5-5", "runs": 14}]}` |
| `logs` | Up to 200 server failure/warning entries, with adjacent repeats grouped, and the updater's last failures. Exceptions include only type and up to three application module/line locations, never values, locals or raw tracebacks | `"2026-10-01T09:29:58Z WARNING tico.support: HQ did not answer (ConnectTimeout)"` |

A Tico from before this feature, or a server whose updater is not the Docker one, leaves out what it cannot see. Log lines come
from the programs' own messages, which avoid task and message text, and go through the same redactor as everything else. The
redactor is a filter, not a guarantee: read the preview, and untick the box if you would rather not send it. The Tico team's Support
bot reads the bundle before the ticket. It is kept with the ticket until the ticket is deleted; there is no separate expiry.

### Optional company Email & Phone inboxes

When an owner configures `TICO_INBOUND_KEY`, Tico HQ receives inbound email and SMS for the
company's allocated addresses and rented numbers. This is separate from connected human Google
mail, tico.dev login/transactional mail and the provider-profile magic-link service. No outbound
email or SMS is sent by this feature. Textverified handles monthly-or-longer physical-SIM US
number rentals; SES handles the allocated `@tico.team` email namespace.

HQ holds the message content to deliver it to this company's server. Normalized content remains
seven days after acknowledgment, at most thirty days without acknowledgment. Raw email objects
are deleted once all routed deliveries are acknowledged or expire, with a 35-day storage backstop.
Delivery tombstones and record-only usage estimates remain thirteen months. HQ staff endpoints
return health and usage counts, never bodies, codes, subjects or recipient addresses. Supplier
credentials stay at HQ. Each company's server keeps its own inbound key; HQ stores only its hash.

The company server keeps bodies and extracted codes for 90 days, then leaves metadata and an
explicit retention marker. Every current company member and agent can read the company inbox;
reading does not hide a message. Each body/code read is audited by identity and message id, with
no content in the audit. Attachments are not downloaded to the install, remote content is not
loaded, and messages are rendered as text. Demo and rehearsal modes never call this service.
