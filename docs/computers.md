# Computers

A computer runs your bots. Each one signs in to the AI tools its bots use (Codex, Claude Code and others), and
**Settings → Computers** shows every computer, the logins and keys it runs on, and how much of each plan is left.
To add a computer, see [Install](install.md#add-computers-to-run-your-bots). Team-wide model settings (which providers
are on, the default model, **Fall back when a plan runs out**, **Check model**) stay on **Settings → AI providers**,
which says "Logins and keys are now on each computer · Computers". Subscriptions used to be on AI providers; old links
to them open Computers.

## Connections

A **connection** is one login an AI tool on a computer runs on. It is one of:

- **A subscription**: a named login to a provider plan (a ChatGPT or Claude plan), for one tool. The pop-up shows it as
  "Subscription · *name*". A computer can hold several, for example one per team. Names are labels: Tico does not record
  who owns a subscription, only which provider account the login reports.
- **The computer's login**: the tool's own sign-in on that computer, the one its bots use when they have no
  subscription of their own. It is a plan login or an API key.
- **An API key**: the computer's login can run on a model API key instead of a plan (Codex, Claude Code, Cursor Agent
  and Muse Code take one). The key is the computer's own (added from Tico), the company's shared key, or a key set on
  the computer itself. See [API keys on a Computer](#api-keys-on-a-computer).

Plan logins stay on the computer that holds them. Tico stores each connection's name, sign-in state, the account it
reports and its usage readings, never the login itself. A key added from Tico is kept sealed in Tico's credential vault
for that computer alone.

**The computer's login and a subscription can be the same account.** When both are signed in to the same provider
account, the pop-up shows one line marked **Computer's login** with one usage reading, not two. Its sign-in buttons
say which login each pair acts on.

**Shared connections.** The company's model key, stored once under Credentials and given to **Every computer**, signs
in every computer that has no login of its own for that tool ([Install](install.md#add-computers-to-run-your-bots)).
Each computer shows it as its own login, by its preview only. Replacing or clearing it from one computer asks whether
to change **This computer only** or **All of them** ([The company's shared key](#api-keys-on-a-computer)).

### Which connection a bot runs on

1. **The bot's own connection**, if it has one: the subscription chosen in **Connection** in the bot's settings
   (**Settings → Bots → Edit**). The bot runs only on it: when it is missing or signed out, the bot waits, and never
   runs on another subscription or the computer's login.
2. **Otherwise, the computer's default**: the computer's login for the bot's tool (or the computer's local default
   profile, if one is set there).

Groups no longer choose a subscription. The bot editor shows the result as "Runs on *tool* · *connection* on
*computer*", using the names on this page. When a plan runs out, a bot can move to its fallback model; that is set on
AI providers and per bot ([How it works](how-it-works.md)), not here.

### Bots sharing one ChatGPT sign-in take turns

A Codex ChatGPT sign-in is one login file whose refresh token rotates as it is used, and OpenAI allows one such login per machine or serialized job stream. So on a Computer, the bots that use one ChatGPT sign-in (the same named subscription, or the Computer's own login) run one Codex turn at a time, in arrival order, and one bot's parallel tasks on it take turns too. A waiting run reads **Waiting for <login> to be free (busy with <bot>)** until its turn comes, or runs on the bot's own fallback when one is ready ([how it works](how-it-works.md#one-turn-per-chatgpt-login)). The connection's line in the computer's pop-up says when several bots share one sign-in and that they take turns; give a bot its own subscription when it needs to run alongside the others. Bots on an OpenAI API key still run side by side. Tico never copies a login file into another bot's home to get around this, because a copy would rotate apart from the original. The rule is per runner process: two runners on one machine that share a login folder don't wait for each other.

### What "% left" means

Usage always reads as the share of the plan's weekly allowance that is **left**, never used. It is the provider's own
reading for that account, so **use outside Tico counts too**: someone chatting in ChatGPT or Claude on the same account
lowers it. Tico never estimates it from its own token counts. A reading has a reset time and an age ("as of 2 min ago");
one older than a day is marked as possibly out of date, and after its reset it reads as the previous week, never as a
fresh 100%. An API key has no plan, so it shows "No plan usage (API key)". Details are in
[Signed-in account and usage windows](#signed-in-account-and-usage-windows).

## The computers list

Each row shows only:

| Column | What it shows |
|---|---|
| **Computer** | Its name. |
| **Tico** | Its Tico version, with **Update** when an update is waiting. |
| **Operator** | The person who runs it. |
| **Bots** | How many bots run on it, and the first few names. |
| **AI tools** | One chip per connection: the tool, a thin bar and "N%" left. Hover or tap for "N% left · resets *day time* · as of *age*". With no reading yet, the chip shows the tool name only. |
| **Status** | Online, or offline · last seen *when*. |

A chip exists only for a connection that was added: a subscription signed in for Codex gives one Codex chip, and
nothing for Claude Code. A small **red dot** on a chip means bots depend on it and it needs attention (signed out, or its
login or key was rejected); a healthy chip has no badge. Other states:

- **No AI connection** with a red dot: the computer has no connection, and its bots can't run until one is added.
- **Waiting for *computer***: a connection was added and the computer has not reported it yet.
- An offline computer's chips keep their last reading; hover shows how old it is.

**Show revoked computers (N)** above the list shows computers that were revoked. **No computers yet** means none is
registered: choose **Add computer**.

## A computer's pop-up

Click a row to open its pop-up. Its sections, top to bottom:

1. **Connections**: one line per connection: the tool, "Subscription · *name*" or "Computer's login", the signed-in
   account and plan, sign-in state, usage (weekly and, where the tool reports one, the 5-hour window) and the bots using
   it. Each line has its actions: **Sign in**, **Switch account** and **Sign out** for a plan login; **Add key**,
   **Replace key** and **Clear key** for a key; for a subscription, **Refresh usage** (Codex) and **Manage** (rename,
   manual weekly reading), **Rename** and **Remove**. **Add connection** at the end opens its own pop-up to sign in an
   installed tool, add a named subscription or add a key.
2. **Screen**: **Off**, **View** or **Control** (Beta), as described in [Install](install.md#screen).
3. **Bots on this computer**: each bot with the connection it actually runs on, read-only. **Assign on the Bots page**
   opens Settings → Bots, where a bot's computer and connection are chosen.
4. **Versions and updates**: Tico, the OS and runner, each AI tool's version with **Installed** and **Enabled**, and
   **Installation and updates**.
5. **Settings**: **Keep for inbox bots only**, **Accepts members' bots** (admins), **Performance diagnostics** and
   **Performance**, and **Revoke computer** at the bottom.

When the computer is offline, the pop-up says "Computer is offline" and its sign-in, usage refresh, screen and update
actions are disabled until it is back.

### Who sees and changes what

Every member sees every working computer, its connections, their sign-in state, its named subscriptions' usage, and the
bots on it they may see. Only an admin or the computer's operator also sees the signed-in accounts, the usage of the
computer's own login and key previews (others see which kind of key it is, not the preview), sees revoked computers, and
gets the change controls: sign in and out, Add connection, rename, remove, usage readings, screen, tools, performance,
inbox-only and Revoke computer. The API refuses anyone else ([Permissions](permissions.md)). BotOps cannot sign a login
out, because that stops bots.

## Add a connection

### Subscription or API key?

Choose **Subscription** when you pay for a plan, such as ChatGPT or Claude:

- You sign in with the plan's account in the browser. There is no key to copy.
- The plan pays for the runs. The chip shows how much of the week's allowance is left ("N% left"), and use of the same
  account outside Tico lowers it too ([What "% left" means](#what--left-means)).
- When the plan runs out, bots move to a fallback model until it resets, or stop if none is ready
  ([How it works](how-it-works.md)).
- You can add several to one computer, for example one per team, each with its own name and usage. A bot runs on one
  when you choose it in **Connection** in the bot's settings.

Choose **API key** when you pay the provider for each use:

- There is no weekly allowance to run out. The chip shows only the tool's name, with no "% left"; its line in the
  pop-up reads "No plan usage (API key)". The provider bills each run.
- Use it for a tool that has no subscription sign-in here (**Subscription** is greyed out), or when a plan's weekly
  limit is too small for your bots.
- Bots on one OpenAI API key run side by side; bots on one ChatGPT sign-in
  [take turns](#bots-sharing-one-chatgpt-sign-in-take-turns).
- Where the key goes:
  - **Add key** gives this computer a key of its own, for Codex, Claude Code, Cursor Agent and Muse Code. Only this
    computer uses it.
  - **Credentials** holds a key that several computers share (the company's key given to **Every computer**), and the
    key of a tool whose key belongs to each bot, such as the Gemini CLI. **Add connection** offers **Open Credentials**
    for those.

### Add one

In a computer's pop-up, **Add connection** (at the end of Connections) opens its own pop-up:

1. **Which AI tool?** The tools installed on that computer. **Install another tool** goes to **Versions and updates**,
   where the owner installs one.
2. **Subscription** or **API key** ([which to choose](#subscription-or-api-key)). A tool with no subscription sign-in from the browser has Subscription greyed out.
   A tool that signs in only from a terminal (such as Grok Build or Cursor Agent) shows the command to run on the
   computer here, with **Copy**. A tool that signs in from the browser but has no named subscriptions offers
   **Computer's login** instead.
3. - **Subscription**: type a name, then choose **Sign in**. The provider's sign-in follows in the same pop-up: the
     browser relays the provider's link and code, and the provider writes the login on the computer. Names are saved in
     a short form, lowercase words joined by hyphens (Tico shows "Saved as *name*" when it changes what you typed).
     **Sign in the computer's own login instead** signs the tool in as the computer's login rather than a named
     subscription.
   - **API key**: for a tool that takes a key of the computer's own, **Add key** opens the key pop-up (see
     [API keys on a Computer](#api-keys-on-a-computer)). If a key already signs the tool in here, **Replace key** is on
     its line instead. For any other tool, **Open Credentials**, where model keys are added and shared with computers.
4. The new connection's chip appears on the computer's row once the computer reports it; the computer does not need a
   restart.

The computer must be online ("Computer is offline" otherwise). Adding a subscription does not change the computer's
default or move any bot: choose it for a bot in **Connection** in the bot's settings.

A new computer whose team signs in with a plan needs its own sign-in: a plan login cannot be copied between computers.
Settings > Health shows "*computer name*: sign in to Codex" (or Claude Code) until it is done.

## Change or clear a login

These are on each connection's line in a computer's pop-up, for the computer's operator and admins.

### Signing out and switching accounts

In a computer's pop-up each signed-in Codex or Claude Code login, the computer's own or a named subscription's, has
**Switch account** and **Sign out** on its line. A line that is both the Computer's login and a
subscription on the same account names which login each pair acts on.
The same people who may sign a login in see them: the Computer's operator, owners and admins. The Computer must be online
and on a current runner; an older one answers `Update <computer> to sign out from here` and is never asked.

- **Sign out** first names the bots that stop: the active bots assigned to that Computer whose runtime and subscription
  are that login. The button stays off until they are named; if they can't be looked up, the dialog says so and offers
  **Try again**. The Computer then runs the CLI's own logout (`codex logout`, `claude auth logout`) as the user that
  signs in, and checks the login again. If it is still signed in, the sign-out fails and the login is as it was. A
  shared-key sign-in already running there is waited for (up to 40 seconds) so it cannot sign the login back in; if it
  is still running, the sign-out fails with "try again in a minute". Bots and assignments stay: those bots wait, as for
  any signed-out login, until someone signs in again. A signed-out named subscription has **Sign in** on its row.
- **Switch account** is a sign-in on the side. The Computer signs the new account in to a private folder inside the
  login's home (`.tico-switch-<id>`, 0700, with copies of the files the CLI keeps beside the login). Once the new
  account is signed in there, the dialog reads **Putting the new account in place**: the Computer asks Tico to go
  ahead, and from Tico's yes the switch can no longer be cancelled (it gets two more minutes of its own to finish).
  Only then does it move the new login files in, keeping the old ones until it has checked the login again; if moving
  a file or that check fails, the old files go back. Cancelling before that point, failing or running out of time leaves
  the old account working, and the side folder is removed (kept, and named in the message, only if the old files could
  not be put back). Runs that start afterwards use the new account. The account and usage readings of the old account
  are cleared.
- A **Computer's own login that someone signed out stays signed out**. The company's model key given to **Every
  computer** does not sign it back in: the server does not hand it out for that runtime, the runner neither applies it
  again (its ten-minute retry included) nor puts it in turns, and Claude Code no longer reads "ready" because of it.
  Signing in again, or **Use the shared key** next to it, ends that. A named subscription never takes the shared key.
  A signed-out login reads **Signed out · Sign in** and its chip has a red dot, with or without bots on it. The
  confirmation names the bots in a line: "Reviewer 1, QA and 2 more will stop running until this is signed in again"
  (every name on hover).
- **Switch to subscription** sits on a Computer's own login that runs on an API key (the shared key, the Computer's
  own key or one set in its terminal): in place of **Switch account**, or next to **Replace key** and **Clear key**. It
  is the same sign-in on the side: until the subscription is signed in and in place the key stays in use, and
  cancelling, failing or running out of time leaves it so. Once the subscription is in place the key is removed as
  **Clear key** removes it (the Computer's copies, and its own key in the vault), and no key reaches that login again:
  not readiness, not the ten-minute key login and not a turn's environment, so the next turn runs on the subscription.
  The server stops handing it the **Every computer** key too. Unlike a sign-out, a later sign-in or Switch account does
  not bring a key back; **Add key** or **Use the shared key** does. Bots keep the connection. Not offered for Claude
  Code on a Mac (Keychain), and it needs a runner that knows it (`Update <computer> to switch to a subscription from
  here` otherwise).

None of this restarts the runner. `POST /api/v2/runners/{runner_id}/logouts` with `{"runtime":"codex"}` (and
`"profile"` for a named subscription) signs out; `GET /api/v2/runners/{runner_id}/logouts/bots?runtime=codex` lists the
bots it stops; `POST /api/v2/runners/{runner_id}/logins` with `"switch": true` switches (with `"to": "subscription"`,
switches the Computer's own login off its API key); `POST
/api/v2/runners/{runner_id}/shared-key` with `{"runtime":"codex"}` lets the shared key back. Each is followed like a
sign-in, with `GET /api/v2/runners/{runner_id}/logins/{id}` (`kind` says which; a sign-out ends `signed_out`). BotOps
cannot sign a login out, because it stops bots. A turn already running when the account changes is not stopped and
may finish on the old account. On a Mac, Claude Code keeps its login in the Keychain rather than in files, so there is
no side to sign in on: Switch account isn't offered for it there (the server and the runner refuse it too); sign out,
then sign in. Codex switches on a Mac as anywhere.

After a sign-in, switch, sign-out or **Use the shared key**, the page looks again until the Computer has reported since
the change finished (its heartbeat; `readiness_at` on the computer in `GET /api/v2/operations`) and that report shows
the change, redrawing **Settings → Computers**, for about a minute.

### API keys on a Computer

A Computer's own login can run on a model API key instead of a subscription (Codex, Claude Code, Cursor Agent and
Muse Code take one). Its line in the computer's pop-up says whose key it is, by its preview only (`sk-…xyz`): **this
computer's key** (added from Tico), **the company's shared key** (the credential given to **Every computer**), or **a
key set on the computer** (its environment or `secrets/_shared.env`). The same people who may sign it in (the Computer's
operator, owners and admins) see **Replace key** and **Clear key** in place of **Sign out**, and **Add key** next to a
login with none. The Computer must be online and on a current runner; an older one answers `Update <computer> to …`.

- **Add key** and **Replace key** take the key in a password field. It goes once to Tico's credential vault, sealed
  like any other credential, for that Computer alone, and is never shown again in full. The Computer fetches it over its
  own connection (the sign-in queue never carries a key) and checks it (Codex signs in with it; the others read it on
  each turn). Until the check passes and Tico agrees the key goes in, the old key stays in use for runs and in the
  Computer's key files, and the Computer's regular key fetch leaves them alone. From Tico's yes the change can no longer
  be cancelled: the key it replaces is deleted from the vault, and a key set on the computer is taken out of
  `secrets/_shared.env`. A key that does not work, a cancel, or a check that runs out of time deletes the new key and
  leaves the old one in use (for Codex, signed in again with it). Runs that start afterwards use the new key; for Claude
  Code, a key added here is used even when the computer also has a browser login.
- **Clear key** first names the bots that stop, like **Sign out**. The Computer then deletes the key from its key files
  (and, for Codex, logs out a login the key made; a browser or subscription login is not the key's and stays), and
  Tico deletes this computer's key from the vault. Bots and assignments stay. Afterwards the line reads **Signed out**
  with **Add key**: like a sign-out, the company's shared key does not sign it back in (the server does not hand it
  out, and the runner's ten-minute re-apply skips it) until someone adds a key, signs in or chooses **Use the shared
  key**.

**The company's shared key.** Replacing or clearing it from one Computer first asks **This computer only** or **All of
them**, listing every Computer it signs in and which of them are offline.

- *This computer only*: Replace gives this Computer a key of its own, which signs it in before the shared one; Clear
  stops this Computer using the shared key. The others keep it.
- *All of them*, Replace: the shared credential's value is replaced, in **Credentials** too. Only someone who may manage
  credentials can do this. Online Computers fetch it at once; an offline one gets it when it is next online. Claude
  Code takes an OAuth token or an API key, each its own credential: the **Kind** chosen is the one replaced (by default
  the one this Computer runs on), and a kind that is not shared is refused rather than written into the other.
- *All of them*, Clear: offered only to someone who may change every Computer listed (otherwise Tico names the ones they
  may not). Each online Computer clears its copy as above. **An offline Computer keeps its copy only until it is next
  online**: Tico marks it cleared at once, so its first key fetch when it comes back (at start-up, then every ten
  minutes) tells it to clear the key, and it does, with no one needing to act. That needs a current runner: while any
  Computer listed, online or offline, runs one too old to clear its key, **All of them** is not offered and Tico answers
  `Update <computer> to clear its key from here, or delete the shared key in Credentials`. A computer that
  joins later still gets the shared key, which stays in **Credentials**.

A Computer's own key is also a credential in **Credentials**. Deleting it there: the Computer falls back to the shared
key at its next key fetch (Codex signs in again with it). With no shared key, the deletion is **Clear key** on that
Computer: its next key fetch deletes its copies and logs out the login the key made, and the line reads **Signed out**
with **Add key**. A key a Computer is still checking cannot be deleted there; cancel **Replace key** or let the check
end first.

`GET /api/v2/runners/{runner_id}/keys?runtime=codex` says which key signs it in (`source`, `preview`) and, for the
shared key, the Computers it serves. `POST /api/v2/runners/{runner_id}/keys` with `{"runtime":"codex","secret":"…",
"scope":"this"}` adds or replaces a key and returns a `use_key` row to follow with `GET …/logins/{id}` (it ends `done`
or `failed`); `"scope":"all"` replaces the shared key. `POST /api/v2/runners/{runner_id}/keys/clear` with
`{"runtime":"codex","scope":"this"}` (or `"all"`) clears it and returns each Computer's `clear_key` row, or `waiting`
for one that is offline. A named subscription holds no key, so its line has none of these.

## Name a subscription

In the computer's pop-up, choose **Rename** on the subscription's line, edit **Name**, and choose **Save name**. Only the
computer's operator or an admin can rename it. Renaming does not move the login, change which bots use it, or rewrite run
history.

Each computer and local profile has a distinct, stable connection ID, even when names match. Usage groups and filters
subscriptions by this ID, showing the current name and computer. Recorded runs keep the computer and profile they ran on,
so moving a bot or renaming a subscription cannot move past usage to another connection. Older runs without a recorded
computer are labelled **computer not recorded** rather than guessed from today's assignment.

A connection ID identifies a login slot on a computer, not a verified provider account. Signing a different provider
account in to the same slot does not create a new identity; add a new subscription when you need separate history.
Matching names on different computers are never combined, because they might be different accounts.

## Remove a subscription

**Remove** on a subscription's line takes that tool's login off the computer: the computer signs it out and, once every
other tool is surely signed out of that subscription, deletes the subscription folder Tico made for it (a folder outside
`profiles/`, or one on the operator's own logins, is only unlisted). A tool the computer cannot check (not installed
there now, or its status check fails) counts as still signed in, so the subscription stays for it.

Removing is refused while any bot on that computer runs on it, paused bots included: the pop-up names them and offers
the Bots page, where they are moved to another connection first. The check holds until the removal ends. While it runs,
no other tool signs in on that subscription, and no change that would make a bot run on it goes through (giving a bot
that subscription, placing a bot on that computer, a model or tool change, the company's default model); each is
refused, naming the bots, until it is removed.

Remove is not offered for the computer's own login (**Sign out** is its way off). Only the computer's operator or an
admin can remove one, and only while the computer is online. A computer whose runner is too old to remove one is told to
update (`model_remove_v1`). The API is `POST /api/v2/runners/{runner_id}/removals` with
`{"runtime":"codex","profile":"team-chatgpt"}`; a refusal for bots answers 409 `in_use` with their names in `bots`.

## Usage readings

### Record a manual weekly reading

For a tool Tico can't read usage from directly, the computer's operator or an admin can copy what the provider shows:

1. In the computer's pop-up, open **Manage** on the subscription's line.
2. Under **Manual weekly reading**, enter **Left (%)** and **Weekly reset (local time)**.
3. Choose **Save reading**.

The line shows the newest reading, manual or from the provider; a manual one says "recorded" instead of "as of".
**Clear reading** goes back to the last provider reading, or to unknown if there is none. A manual reading changes no
provider limit and neither schedules nor blocks bots.

### Refreshing weekly allowance

In a Computer's pop-up on **Settings → Computers**, a computer operator or administrator can choose **Refresh
usage** for a named Codex subscription. The computer reads the Codex app-server's
[`account/rateLimits/read`](https://learn.chatgpt.com/docs/app-server) endpoint in that
profile. It does not start a model turn, consume a reset credit, change a subscription,
or switch to another account. Repeated clicks are coalesced for one minute; only one
read runs at a time per computer and requests expire after two minutes. Older computers
without refresh support leave a request to expire; update the computer to enable it.

Sign-in status, weekly allowance and refresh status are separate. Failed or unsupported
reads keep the last successful reading, with its original timestamp. An elapsed reset
never means a fresh allowance or a repaired sign-in. Claude and other runtimes without
a supported standalone weekly read use observations from normal runs or a manual
provider reading. Missing percentages remain unknown, not zero.

Starting a new sign-in, a switch or a sign-out through Tico invalidates earlier readings and in-flight refresh
results for that profile/runtime. Replacing credentials outside Tico under the same
local profile cannot currently be detected: refresh and confirm the provider reading
after such a change. Usage reported by a turn that was already running during sign-in
can also refer to its earlier account; avoid replacing an active profile while turns
are running. This is approximate monitoring, not a spending limit.

## Reference

The rest of this page is for administrators and integrators: where readings come from, the API, and how a computer keeps its logins.

### Signed-in account and usage windows

Weekly reports are scoped to a Computer, profile and runtime. Codex reports its seven-day window when its running host emits one. Claude Code may report the seven-day reset and limit state without a percentage; that percentage stays unknown. No API calls or token-to-percentage estimates are used to invent a provider balance. Usage elsewhere on the same provider account counts toward that allowance.

The last provider report includes its observation time and reset. Reports older than a day are labelled as possibly out of date. After reset they stay labelled as the previous week and never read as a fresh allowance. Reports survive a runner restart on the server. An updated computer can refresh Codex weekly usage directly; other providers need a supported subscription-backed run or a manual reading. An unnamed computer-default login has no named subscription snapshot; it remains unknown rather than being assigned to another profile. When that default login keeps stopping on a usage limit, **Settings → Health** says it is out of quota ([Health Monitor](health-monitor.md), `default_login_limited`).

Computers report the account each login is signed in to: email, plan and organization from `claude auth status --json`, and email and plan from Codex app-server's `account/read`, always requested with `refreshToken: false`. A Codex turn's or refresh's `planType` keeps the plan current. This deliberately reverses the earlier rule that Tico kept no account records: it is the first stored account identity, so the page can show which account each Computer and profile uses (`signed in as <email> · <plan>`). Credential files and `codex login status` output are never read for it, only allow-listed fields are kept, and a value that looks like a key or token is dropped on the computer and refused by the server, without affecting the sign-in state. The account is read alongside the existing one-minute sign-in probe; Codex's app-server read runs in the background at most every ten minutes. A signed-out login shows no account. An account stays one login's report: two profiles that show the same email are not merged.

Only owners and admins and the Computer's operator see accounts in `GET /api/v2/operations`, `GET /api/v2/computers` and `GET /api/v2/subscriptions`; other members see sign-in state only, and bots never see accounts. No new grants or permissions are added. A current server tells the runner it takes accounts in the heartbeat reply (`subscription_accounts`); until then, and against an older server, the runner leaves them out so the Computer reads as before.

Next to the weekly window, a profile reports Codex's 5-hour window (`five_hour`, from the same rate-limit events) with its own reset and observation time. A 5-hour reading whose reset is more than 5 hours 10 minutes after it was taken (10 minutes for clock skew) is unknown; the weekly window keeps its 8-day limit. The computer's pop-up shows both windows on each connection line as "% left" bars with the reading's age; each tool's installed version and Enabled switch are under **Versions and updates**. Tools without an account or usage source (Gemini CLI, Cursor Agent, pi, Grok) read "unavailable", never empty or full.

The Computer's own login (the one its bots use when no profile is assigned) reports the same two windows on its runtime row in readiness (`weekly`, `five_hour`), checked against the same limits and shown to the same people as its account. Claude reads them from each turn's `rate_limit_event`: Claude Code 2.1.30x sends both windows in `unifiedWindows` with a 0–1 `utilization`, which becomes the percentage used (the page shows what is left). That field is undocumented, so a missing or out-of-range value reads "percentage unavailable", never 0% or 100%, and an older CLI reports only the reset as before. Until a Claude turn runs on that login, the line says "Weekly usage unavailable · reported after a Claude Code turn on this computer": Tico never starts a turn just to read usage. Codex's windows come from the same background app-server session that reads its account (at most every ten minutes, `account/rateLimits/read`, no turn and no paid request), for the own login and each named profile; an API-key login has no plan usage and is not asked. A server from before this change refuses the two runtime fields; the runner then leaves them out for ten minutes and sends the rest.

### Choosing a bot's connection in the API

Groups do not choose a subscription: a bot runs on its own choice, or else on its Computer's default ([Which connection a bot runs on](#which-connection-a-bot-runs-on)). With no server assignment, the Computer keeps its local `bot_profiles` and `default_profile` behavior. Removing an assignment does not delete a login. Group assignments saved by an earlier release are kept but no longer read; they are not moved to the bots.

`GET /api/v2/subscriptions` returns `profiles_by_computer` and `assignments`. Set or clear an assignment with `PUT /api/v2/subscriptions`, for example `{"scope":"bot","target":"builder","profile":"engineering"}`; use `profile: null` to clear it. A `group` scope is refused (422). The bot's Computer operator, owners and admins change it. `GET /api/v2/bots/{bot}/subscription` returns the effective profile, its source, Computer and sign-in state for the model picker. Unknown sign-in state is `null`.

### Sign-in and profiles on the computer

The Computer operator can manage sign-in on their own Computer; owners and admins can manage it on any Computer. To sign in a named profile, use the existing Computer sign-in flow: `POST /api/v2/runners/{runner_id}/logins` with `{"runtime":"codex","profile":"engineering"}` (or `claude`). A current Computer creates the named profile if needed beside its registration (`profiles/<name>`, or beside its state directory on isolated Docker Computers when no registration path is available (inside state otherwise)). The profile is private and stays outside the team workspace. On an isolated Docker Computer the sign-in and turns run as the bot user, so each runtime's home in the profile belongs to the bot user, while the profile folder and `profile.json` stay the supervisor's (the folder is traversable only). Adding a profile does not change the local default. The browser relays the provider's link and code; the provider writes its credentials locally.

Computers report their profiles on heartbeat. A bot with a server-assigned profile waits when that profile is missing or signed out; it never runs on another profile, the local default or the operator's login. Health and subscription status show `Subscription <name> isn't on <computer>` or `Subscription <name> isn't signed in on <computer>`. The server also holds these bots on older Computers that cannot report profiles, with `Update <computer> to use subscriptions`. Bots without an assignment keep their local profile and default behavior.

A current runner retries a rejected heartbeat without only the unsupported report: subscriptions, worktrees or repository clones. Other reports stay intact, and unrelated errors retain their original message. Invalid local profile names are omitted from reports. Provider sign-in probes are cached for one minute and refreshed after sign-in or a rejection. Turns and watcher scripts use the cached sign-in state without probing at every start. A probe timeout leaves sign-in unknown and allows a turn to try. A fallback harness uses the same assigned subscription and waits with `Subscription <name> isn't signed in on <computer>` when its login is missing. Start refusals record a diagnostic with the runtime in a detail field and requeue the job without posting a chat reply. The server waits for a fresh ready subscription report before handing it out again. Assigned profiles cover Codex, Claude Code, Grok and Gemini CLI. Other runtimes, including Cursor Agent, Pi and Antigravity, wait with `Subscription <name> doesn't cover <runtime>` in Health and the model picker. A rejection affects only that runtime on that profile. Claude profiles keep their existing HOME-based login layout. Completion and usage records include the selected profile when a turn starts; empty profile fields are omitted.

Each run pins its selected local profile directory before preparing the provider environment. Changes to a bot assignment, local default, or profile directory affect later runs, not an in-progress primary or fallback attempt. Environment, host setup, session reuse, usage and rejection attribution use the same binding; fallback still checks its runtime eligibility and known sign-in state. A run started on the unnamed operator login does not acquire a newly configured profile midway through execution. Changing a profile directory starts a separate provider session. The first run after upgrading to directory-bound session keys also starts a fresh provider session for named profiles; the Tico conversation remains intact. This pins the login slot, not the provider account inside it: avoid replacing credentials in a slot while it has active runs.

Profile directories separate provider logins. Browser-created profiles live outside the bot workspace, with directories set to 0700 and owned by the same user that runs sign-in, probes and turns. Linux Docker harnesses currently retain the existing shared bot Unix user; profiles do not yet provide a Unix permission boundary between groups. Do not rely on profile assignments to isolate files from another bot on the same Computer.
