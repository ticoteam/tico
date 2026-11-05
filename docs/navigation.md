# Navigation

The current paths in Tico. Settings is under your email and opens on General; each tab has its own address,
shown in the address bar while the tab is open (`#/settings/people`, `#/settings/gateway`), to bookmark or share, and a tab you can't see opens General with **That page is for admins**.
Tools is in the sidebar.
Controls appear when your rights allow the action. A card you can read but cannot change names who can.
For example, Repositories says **Ask Ana Rivera to connect GitHub** when Ana owns the team.

**Picks and services** opens Gateway. **Key and connection** opens Models.

| To | Open |
|---|---|
| Give work or review a result | Tasks → New task, List, Board or Done |
| Talk to a bot about one task | Bot's Home → tap the task's row ([Sessions](sessions.md)) |
| See or link a task's sessions | Task → Sessions +; ⋯ → Copy session ID |
| Add or edit a Routine | Automations → New automation ([Routines](routines.md)) |
| See what Tico runs on its own | Settings → System → What Tico runs on its own ([System routines](system-routines.md)) |
| Receive a webhook, or send events out | Settings → Webhooks → Incoming or Outgoing ([Webhooks](webhooks.md)) |
| Keep webhooks while the server is off | Settings → Webhooks → Through Tico Gateway |
| Stop a hosted server when idle | Settings → Computers → Sleep when idle ([Account](account.md#sleep-when-idle)) |
| See or change what wakes a bot | Automations → What wakes bots, or What wakes <bot> |
| Add, assign or set up a bot | Settings → Bots → Add from template; then the bot → Set up |
| Change Instructions | Bot → More → Instructions → Edit Instructions → Ask BotOps to change |
| Change model, effort, owners or status | Settings → Bots |
| Register or remove a Computer | Settings → Computers → Add computer; open a computer → Revoke computer |
| Add a subscription (Connect or paste) and give it to computers | Settings → Models → Add subscription; then Settings → Computers ([Subscriptions](computers.md#subscriptions)) |
| Sign a computer in to an AI tool | Settings → Computers → open a computer → Connections ([Computers](computers.md)) |
| Change company turn limits | Settings → Models → Limits |
| Check problems and recovery | Settings → Health |
| Store a Credential and grant it | Tools → Credentials → Add credential; then Access → Grant access |
| Connect or review a Tool | Tools; Slack channels are under Tools → Slack channels |
| Add or manage people and member rules | Settings → People |
| Connect your Google account, or the whole Workspace (admins) | Settings → Inboxes → Settings → Email: pick One account at a time or Whole Workspace |
| See and manage every mailbox, Tico address, number and mailing address | Settings → Inboxes |
| Choose AI providers | Settings → Models |
| Connect the Tico AI Gateway | Settings → Models → Tico Gateway → Advanced ([Tico AI Gateway](ai-gateway.md)) |
| Connect your External agent | Connect an external agent beside your email |
| Review message work | Message bots → mailbox or channel |
| Read or write team knowledge | Docs → Internal docs; outside sources are Linked docs |
| Find a bot, human or page | ⌘K, or the search icon at the end of the sidebar's **Ask Tico** row (phone: the Search tab) |
| Find a file or page any bot made | Library → Files (`#/library/files`); search finds text inside ([Files](files.md#library--files)) |
| See every tool and connection, who uses it and its status | Tools (`#/tools`) ([Connect tools](connect-tools.md#tools-list)) |
| Arrange your own landing page | Overview → Mine (`#/home`) ([Overview](overview.md#my-dashboard)) |
| See rules or goals on the team chart, by bot | Overview → Graph (`#/overview/graph`, `?view=goals` for goals) ([Overview](overview.md#graph)) |
| Open a page full size | Tap its card, or `#/page/<id>` ([Pages](pages.md)) |
| Read or write the team's strategy, publish a version | Strategy (`#/strategy`); the owner writes and publishes, the Strategy Bot proposes ([Strategy](strategy.md)); Lenses on that page run a framework against it ([Lenses](lenses.md)) |
| See the market map, or ask the Librarian about the market | Strategy → Market (`#/strategy/market`; `#/market` redirects there) |
| See growth numbers and their sources, or marketing tasks | Growth (`#/growth`; Sources is a link on the page, or in Settings for the owner and admins) ([Growth](growth.md)). Marketing tasks are on the Tasks page, type Marketing |
| Review goals and KPIs | Goals; a goal's **Bet** picker links it to a strategy bet |
| See the Company page, or share it | Overview → Share (`#/overview/company-page`); the share icon has transparency, the address and readers. Owners and admins only ([Company page](company-page.md)) |
| Change the Company page: editions, disclosure, stats, audiences, accent, logo | Overview → Share → Manage (`#/company-page`) |
| Message the Tico team | **?** beside Search (phone: More drawer, beside Search), or any `#/support` link |

All four system bots appear as **Built-in** in Settings > Bots. Neither Assistant nor BotOps has a rail entry: **Ask**
(the sidebar's bottom row, ⌘J; the phone's middle tab) opens your Assistant over any page as **Tico Assistant**, `#/assistant`
opens it full page, and the Assistant hands BotOps what needs BotOps;
the Strategy Bot is on Goals and proposes Strategy, and Ask the Librarian is on Docs and on Strategy's Market tab. The Strategy Bot is at the top of the team chart; the other three stay out of it.
Inbox Manager and other repeating message work appear under **Message bots**.
An older app may show Instructions under the bot's Docs tab; the action is still **Edit Instructions**.
Removing a Computer revokes its registration and leaves its local files and bots in place.
See [Using Tico](using-tico.md), [Credentials](credential-vault.md) and [Updates](updates.md).

![General selected in Settings, with Team, Tico company, Assistant and Privacy cards](images/settings-stage1/settings-general.png)

*Synthetic Acme example. Settings opens on General.*

## Tags

**Settings > Tasks** (under **Task tags**) lists templates and task tags and creates new ones. Task chips show the
label and metadata; open a chip in task details to see its checklist and related tasks. A
**Tag** filter narrows the task board by key. Templates have **Make a tag** to create a separate
checklist with its own metadata. Tag owners and task movers can tick and edit its notes.
