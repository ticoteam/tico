# Subscriptions

A subscription profile is a named provider login on a Computer. Credentials stay on that Computer; Tico stores a stable connection ID, editable display name, and sign-in state. Each profile has its own login directory.

## Names and weekly allowance

Open a computer on **Settings → Computers** to name and sign in subscriptions and see how much weekly allowance is left; each row's chips show it at a glance. Names are labels; Tico does not record who owns a subscription, only which provider account a login reports (below). Each bot can choose one in Bot settings; without a choice it runs on its computer's own login. Logins and credentials remain on the selected Computer.

In the computer's pop-up on **Settings → Computers**, open **Manage** on the subscription's line, edit **Name**, and choose **Save name** to change its display name without moving credential directories, changing assignments, or rewriting run history. Each Computer and local profile key has a distinct, stable connection ID, even when names match. Only the Computer operator or an administrator can rename it.

Usage groups and filters subscriptions by this connection ID, showing the current display name and Computer. Recorded runs retain their execution Computer and local profile key, so moving a bot or renaming a subscription cannot move past usage to another connection. Older runs without an execution Computer are labelled **computer not recorded** instead of guessed from today's assignment.

A connection ID identifies a local login slot, not a verified provider account. Signing in a different provider account under the same slot does not create a new identity; use a new profile when you need separate history. Names never imply ownership or combine accounts across Computers.

Weekly reports are scoped to a Computer, profile and runtime. Codex reports its seven-day window when its running host emits one. Claude Code may report the seven-day reset and limit state without a percentage; that percentage stays unknown. No API calls or token-to-percentage estimates are used to invent a provider balance. Usage elsewhere on the same provider account can count toward that allowance. The page does not combine matching names across Computers because they might be different accounts.

The last provider report includes its observation time and reset. Reports older than a day are labelled as possibly out of date. After reset they stay labelled as the previous week, never automatically become zero. Reports survive a runner restart on the server. An updated computer can refresh Codex weekly usage directly; other providers need a supported subscription-backed run or a manual provider reading. Shorter windows are shown on **Settings → Computers**, not here. An unnamed computer-default login has no named subscription snapshot; it remains unknown rather than being assigned to another profile. When that default login keeps stopping on a usage limit, **Settings → Health** says it is out of quota ([Health Monitor](health-monitor.md), `default_login_limited`).

A Computer operator or administrator can open **Manage** on the subscription's line and use the provider's manual weekly reading form to copy the percentage left and the reset time. This is a labelled manual observation, not an editable provider quota. The newest manual or provider observation is shown. Clear manual report restores the last provider report, or unknown if none exists. This neither changes provider limits nor schedules or blocks bots, and introduces no new permissions.


### Signed-in account and usage windows

Computers report the account each login is signed in to: email, plan and organization from `claude auth status --json`, and email and plan from Codex app-server's `account/read`, always requested with `refreshToken: false`. A Codex turn's or refresh's `planType` keeps the plan current. This deliberately reverses the earlier rule that Tico kept no account records: it is the first stored account identity, so the page can show which account each Computer and profile uses (`signed in as <email> · <plan>`). Credential files and `codex login status` output are never read for it, only allow-listed fields are kept, and a value that looks like a key or token is dropped on the computer and refused by the server, without affecting the sign-in state. The account is read alongside the existing one-minute sign-in probe; Codex's app-server read runs in the background at most every ten minutes. A signed-out login shows no account. An account stays one login's report: two profiles that show the same email are not merged.

Only owners and admins and the Computer's operator see accounts in `GET /api/v2/operations`, `GET /api/v2/computers` and `GET /api/v2/subscriptions`; other members see sign-in state only, and bots never see accounts. No new grants or permissions are added. A current server tells the runner it takes accounts in the heartbeat reply (`subscription_accounts`); until then, and against an older server, the runner leaves them out so the Computer reads as before.

Next to the weekly window, a profile reports Codex's 5-hour window (`five_hour`, from the same rate-limit events) with its own reset and observation time. A 5-hour reading whose reset is more than 5 hours 10 minutes after it was taken (10 minutes for clock skew) is unknown; the weekly window keeps its 8-day limit. **Settings → Computers** shows each AI tool on its own row (installed version, enabled, sign-in and account, usage) with one line per named profile, and both windows as bars with the reading's age. Tools without an account or usage source (Gemini CLI, Cursor Agent, pi, Grok) read "unavailable", never empty or full.

The Computer's own login (the one its bots use when no profile is assigned) reports the same two windows on its runtime row in readiness (`weekly`, `five_hour`), checked against the same limits and shown to the same people as its account. Claude reads them from each turn's `rate_limit_event`: Claude Code 2.1.30x sends both windows in `unifiedWindows` with a 0–1 `utilization`, which becomes the percentage used. That field is undocumented, so a missing or out-of-range value reads "percentage unavailable", never 0% or 100%, and an older CLI reports only the reset as before. Until a Claude turn runs on that login, the row says "Weekly usage unavailable · reported after a Claude Code turn on this computer": Tico never starts a turn just to read usage. Codex's windows come from the same background app-server session that reads its account (at most every ten minutes, `account/rateLimits/read`, no turn and no paid request), for the own login and each named profile; an API-key login has no plan usage and is not asked. A server from before this change refuses the two runtime fields; the runner then leaves them out for ten minutes and sends the rest.

Groups do not choose a subscription: a bot runs on its own choice, or else on its Computer's default. With no server assignment, the Computer keeps its local `bot_profiles` and `default_profile` behavior. Removing an assignment does not delete a login. Group assignments saved by an earlier release are kept but no longer read; they are not moved to the bots.

`GET /api/v2/subscriptions` returns `profiles_by_computer` and `assignments`. Set or clear an assignment with `PUT /api/v2/subscriptions`, for example `{"scope":"bot","target":"builder","profile":"engineering"}`; use `profile: null` to clear it. A `group` scope is refused (422). The bot's Computer operator, owners and admins change it. `GET /api/v2/bots/{bot}/subscription` returns the effective profile, its source, Computer and sign-in state for the model picker. Unknown sign-in state is `null`.

The Computer operator can manage sign-in on their own Computer; owners and admins can manage it on any Computer. To sign in a named profile, use the existing Computer sign-in flow: `POST /api/v2/runners/{runner_id}/logins` with `{"runtime":"codex","profile":"engineering"}` (or `claude`). A current Computer creates the named profile if needed beside its registration (`profiles/<name>`, or beside its state directory on isolated Docker Computers when no registration path is available (inside state otherwise)). The profile is private and stays outside the team workspace. On an isolated Docker Computer the sign-in and turns run as the bot user, so each runtime's home in the profile belongs to the bot user, while the profile folder and `profile.json` stay the supervisor's (the folder is traversable only). Adding a profile does not change the local default. The browser relays the provider's link and code; the provider writes its credentials locally.

Computers report their profiles on heartbeat. A bot with a server-assigned profile waits when that profile is missing or signed out; it never runs on another profile, the local default or the operator's login. Health and subscription status show `Subscription <name> isn't on <computer>` or `Subscription <name> isn't signed in on <computer>`. The server also holds these bots on older Computers that cannot report profiles, with `Update <computer> to use subscriptions`. Bots without an assignment keep their local profile and default behavior.

A current runner retries a rejected heartbeat without only the unsupported report: subscriptions, worktrees or repository clones. Other reports stay intact, and unrelated errors retain their original message. Invalid local profile names are omitted from reports. Provider sign-in probes are cached for one minute and refreshed after sign-in or a rejection. Turns and watcher scripts use the cached sign-in state without probing at every start. A probe timeout leaves sign-in unknown and allows a turn to try. A fallback harness uses the same assigned subscription and waits with `Subscription <name> isn't signed in on <computer>` when its login is missing. Start refusals record a diagnostic with the runtime in a detail field and requeue the job without posting a chat reply. The server waits for a fresh ready subscription report before handing it out again. Assigned profiles cover Codex, Claude Code, Grok and Gemini CLI. Other runtimes, including Cursor Agent, Pi and Antigravity, wait with `Subscription <name> doesn't cover <runtime>` in Health and the model picker. A rejection affects only that runtime on that profile. Claude profiles keep their existing HOME-based login layout. Completion and usage records include the selected profile when a turn starts; empty profile fields are omitted.

Each run pins its selected local profile directory before preparing the provider environment. Changes to a bot assignment, local default, or profile directory affect later runs, not an in-progress primary or fallback attempt. Environment, host setup, session reuse, usage and rejection attribution use the same binding; fallback still checks its runtime eligibility and known sign-in state. A run started on the unnamed operator login does not acquire a newly configured profile midway through execution. Changing a profile directory starts a separate provider session. The first run after upgrading to directory-bound session keys also starts a fresh provider session for named profiles; the Tico conversation remains intact. This pins the login slot, not the provider account inside it: avoid replacing credentials in a slot while it has active runs.

Profile directories separate provider logins. Browser-created profiles live outside the bot workspace, with directories set to 0700 and owned by the same user that runs sign-in, probes and turns. Linux Docker harnesses currently retain the existing shared bot Unix user; profiles do not yet provide a Unix permission boundary between groups. Do not rely on profile assignments to isolate files from another bot on the same Computer.

### Bots sharing one ChatGPT sign-in take turns

A Codex ChatGPT sign-in is one login file whose refresh token rotates as it is used, and OpenAI allows one such login per machine or serialized job stream. So on a Computer, the bots that use one ChatGPT sign-in (the same named subscription, or the Computer's own login) run one Codex turn at a time, in arrival order, and one bot's parallel tasks on it take turns too. A waiting run reads **Waiting for <login> to be free (busy with <bot>)** until its turn comes, or runs on the bot's own fallback when one is ready ([how it works](how-it-works.md#one-turn-per-chatgpt-login)). **Settings → Computers** and **Subscriptions** say when several bots share one sign-in and that they take turns; give a bot its own subscription when it needs to run alongside the others. Bots on an OpenAI API key still run side by side. Tico never copies a login file into another bot's home to get around this, because a copy would rotate apart from the original. The rule is per runner process: two runners on one machine that share a login folder don't wait for each other.

The Engineering Manager template gives owners and admins an initial read grant to all enabled repositories. Member-created bots keep the team default. Its child-task and worktree instructions require phases 2 and 3 to ship first.

### Signing out and switching accounts

In a Computer's pop-up on **Settings → Computers** each signed-in Codex or Claude Code login, the Computer's own or a
named subscription's, has **Switch account** and **Sign out**. A line that is both the Computer's login and a
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
Muse Code take one). Its row on **Settings → Computers** says whose key it is, by its preview only (`sk-…xyz`): **this
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
  Tico deletes this computer's key from the vault. Bots and assignments stay. Afterwards the row reads **Signed out**
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
Computer: its next key fetch deletes its copies and logs out the login the key made, and the row reads **Signed out**
with **Add key**. A key a Computer is still checking cannot be deleted there; cancel **Replace key** or let the check
end first.

`GET /api/v2/runners/{runner_id}/keys?runtime=codex` says which key signs it in (`source`, `preview`) and, for the
shared key, the Computers it serves. `POST /api/v2/runners/{runner_id}/keys` with `{"runtime":"codex","secret":"…",
"scope":"this"}` adds or replaces a key and returns a `use_key` row to follow with `GET …/logins/{id}` (it ends `done`
or `failed`); `"scope":"all"` replaces the shared key. `POST /api/v2/runners/{runner_id}/keys/clear` with
`{"runtime":"codex","scope":"this"}` (or `"all"`) clears it and returns each Computer's `clear_key` row, or `waiting`
for one that is offline. A named subscription holds no key, so **Manage subscription** has none of these.

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
