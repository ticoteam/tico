# Subscriptions

A subscription profile is a named provider login on a Computer. Credentials stay on that Computer; Tico stores a stable connection ID, editable display name, and sign-in state. Each profile has its own login directory.

## Names and weekly allowance

Open a computer on **Settings → Computers** to name and sign in subscriptions and see how much weekly allowance is left; each row's chips show it at a glance. Names are labels; Tico does not record who owns a subscription, only which provider account a login reports (below). Each bot can choose one in Bot settings; without a choice it runs on its computer's own login. Logins and credentials remain on the selected Computer.

Open **Manage subscription**, edit **Name**, and choose **Save name** to change its display name without moving credential directories, changing assignments, or rewriting run history. Each Computer and local profile key has a distinct, stable connection ID, even when names match. Only the Computer operator or an administrator can rename it. Group defaults still use the immutable local profile key across their Computers; a display rename does not redirect them.

Usage groups and filters subscriptions by this connection ID, showing the current display name and Computer. Recorded runs retain their execution Computer and local profile key, so moving a bot or renaming a subscription cannot move past usage to another connection. Older runs without an execution Computer are labelled **computer not recorded** instead of guessed from today's assignment.

A connection ID identifies a local login slot, not a verified provider account. Signing in a different provider account under the same slot does not create a new identity; use a new profile when you need separate history. Names never imply ownership or combine accounts across Computers.

Weekly reports are scoped to a Computer, profile and runtime. Codex reports its seven-day window when its running host emits one. Claude Code may report the seven-day reset and limit state without a percentage; that percentage stays unknown. No API calls or token-to-percentage estimates are used to invent a provider balance. Usage elsewhere on the same provider account can count toward that allowance. The page does not combine matching names across Computers because they might be different accounts.

The last provider report includes its observation time and reset. Reports older than a day are labelled as possibly out of date. After reset they stay labelled as the previous week, never automatically become zero. Reports survive a runner restart on the server. An updated computer can refresh Codex weekly usage directly; other providers need a supported subscription-backed run or a manual provider reading. Shorter windows are shown on **Settings → Computers**, not here. An unnamed computer-default login has no named subscription snapshot; it remains unknown rather than being assigned to another profile.

A Computer operator or administrator can open **Manage subscription** and use the provider’s **manual weekly report** form to copy the provider's percentage used and reset time. This is a labelled manual observation, not an editable provider quota. The newest manual or provider observation is shown. Clear manual report restores the last provider report, or unknown if none exists. This neither changes provider limits nor schedules or blocks bots, and introduces no new permissions.


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

The Engineering Manager template gives owners and admins an initial read grant to all enabled repositories. Member-created bots keep the team default. Its child-task and worktree instructions require phases 2 and 3 to ship first.

### Refreshing weekly allowance

On **AI providers**, a computer operator or administrator can choose **Refresh weekly
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

Starting a new sign-in through Tico invalidates earlier readings and in-flight refresh
results for that profile/runtime. Replacing credentials outside Tico under the same
local profile cannot currently be detected: refresh and confirm the provider reading
after such a change. Usage reported by a turn that was already running during sign-in
can also refer to its earlier account; avoid replacing an active profile while turns
are running. This is approximate monitoring, not a spending limit.
