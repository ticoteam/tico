# Team chart: humans, groups, and the bots they use

The team is everyone: humans and bots. A **group** is a sub-team, and groups can hold groups. A group holds teammates, human or
bot. This page is who is in which group, which bots each group holds, and who each bot is mainly for.

The chart in the app is the **Team** sidebar: each group is a section that opens and closes, holding its humans and bots,
with the groups inside it below them. A human or bot with no group sits at the top. The built-in bots (the Assistant, BotOps, the
Librarian) stay out of the chart and the Goals tree. Assistant and BotOps have main rail entries;
the Librarian is reached from Docs and Market. Settings > Bots lists them with the CEO Bot, the fourth built-in bot.
When bots' names start with the same words (the "Head of" in **Head of Product** and **Head of Sales**), those words are
muted, and on a narrow sidebar they drop out whole so each row keeps the part that tells it apart. Hover a bot to see its
whole name. A bot waiting for its setup conversation has a small amber gear beside its name (**Needs setup**).
The built-in **CEO Bot** ([Your CEO Bot](ceo-bot.md)) is on every install, at the top of the chart under the owner. The Heads, each in its department's group, and the bots that report to no one sit under it.
The Assistant, BotOps, the Librarian and message bots stay apart. Only the chart moves: nobody's `reports_to` and
nobody's access changes, and `hub team show` still gives each bot the `reports_to` it has.
Message bots such as Inbox Manager follow in a separate **Message bots** section on the chart and Goals. The mailboxes and
A message bot's mailboxes and Slack channels are listed under it in the sidebar; any other bot's Slack channels are
under Slack in its Tools on the bot's page.
Synced Grok Bots, Dots and Muse ([External agent sync](external-agent-sync.md)) are ordinary rows, initially under the
human who linked them. Move each one under a human or bot, or into a group, with the same chart controls as other bots;
later syncs preserve that placement. Grok's own sections and pins remain source metadata and do not create nested
groups or control chart ordering. Each row keeps its runtime mark. Your own row shows **Connect** until an external
agent of yours has reached Tico ([Connect an external agent](connect-an-agent.md)).
Bots read the same chart through `hub team show` / the `hub_team_show` MCP tool (`GET /api/v2/org`): the humans, and every bot the caller
may see (see [permissions.md](permissions.md)) with its `reports_to`, its `team` (its group's id), its `department` (its group's name)
and its `template`. `hub team show --team <group>` is that group and the groups in it. `hub human list` is the humans alone.

The chart shows each original bot in its assigned group. Personal branches appear only for their operator and are
labeled **Your branch**, including in the recent-history view and mobile team switcher. Owners and admins use the
bot's branch picker or Settings to inspect other people's branches; permission to manage a branch does not add it
to their own chart. Branches keep their own reporting relationships and work.

## The order of reports

A manager's reports, humans and bots, are listed in the order someone put them in, the same for everyone who views the
chart. Once someone has ordered a manager's reports, a new report, or one that moves in from another manager, goes
last there, after the ones before it, whatever its name. Under a manager nobody ordered, reports follow the chart's
own order: humans first, then bots (temporary ones last, a bot's `order` lowest first), then by name, accents and
case aside. The Goals tree, a person's page, **Updates** and `hub org order` list reports in the same order. A
manager's reports shown in a group keep that order there, and move up and down among the ones beside them.

- **In the app**, drag a human or a bot onto the top or bottom of another report of the same manager to put it above or
  below; dropping onto the middle of a row still makes it report there. Each report's row also has a menu with **Move
  up** and **Move down**: Tab to it from the row, Enter opens it, the arrow keys choose and Escape closes it.
- **Who orders them**: whoever may put reports under that manager, as for `reports_to`: the owner, the manager or
  someone it reports up to, and for a bot manager the person who runs it. A bot orders its own reports. Everyone else
  sees the order without the handles or the menu, and the API refuses them. A drop onto someone you may not put that
  human or bot under says so and moves nothing.
- **In the API and the tools**: `GET /api/v2/org/order/{manager}` lists a manager's direct reports in the chart's order
  (`manager` is a bot's slug or a human's id; put `bot:` or `human:` in front when one could be either);
  `PUT /api/v2/org/order/{manager}` with `{"reports": [...]}` puts them in that order. Reports left out keep their
  places, so naming two swaps those two; a name that is not a direct report is refused. `hub org order <manager>` and
  `hub org order <manager> --set a,b,c` (`hub_org_order`) do the same. Ordering never changes who reports to whom.

Groups and reporting are two things. `reports_to` says who a human or bot works for; a group says who is on which sub-team. Changing
one never changes the other.

Updates follow the chart: the bots lowest on it report first, and each manager, bot or person, writes its update from its
direct reports' ([Updates](using-tico.md#updates)).

## Groups

The **+** beside **Team** offers **Add section**, **Add bot** and **Add human**, according to your permissions.
**Add section** opens the inline group-name field. **Add bot > From template** opens the template picker;
**Ask BotOps** opens its chat with an editable request, preserving any draft already there. Describe a custom bot or
share an existing agent's folder path and computer, or its GitHub URL. Nothing is sent until you send the message;
BotOps helps work out the setup rather than automatically importing an arbitrary folder.

A group is an id, a name, an optional parent group, and its teammates. A teammate is in one group at a time: putting a human or bot in a
group takes it out of the one it was in. Groups are stored with the roster (`org_groups` in `registry/people.yaml` seeds them; from then on
the app and the API change them):

```yaml
org_groups:
  marketing: {name: Marketing}
  seo: {name: SEO, parent: marketing}
people:
  - {id: cara, name: Cara Mendes, team: seo}     # a human's group is `team`
```

A bot's group is `team` in its entry in `registry/employees.yaml`, and after that in the app. A bot with no group of its own is in its
manager's; giving it an empty `team` takes it out of every group.

Owners and admins change groups; everyone else reads them.

- **In the app**, **Team > + > Add section** adds a group, and the **+** on a group adds one inside it. The pencil renames a group in place.
  The delete button removes a group after confirmation: its people, bots and child groups move to its parent, or **No group** at the top.
  Teammates, their histories and reporting lines stay.
  Drag a human or a bot onto a group to put it there, onto **No group** (it appears while you drag) to take it out, and drag a group onto
  another to nest it. Members see the groups but have none of these handles.
- **In the API**: `GET /api/v2/groups` lists them with their humans and the bots you may see; `POST /api/v2/groups` adds one
  (`{"name", "parent", "add": {"people": [], "bots": []}}`); `PATCH /api/v2/groups/{id}` renames it, moves it (`"parent": ""` is the top) and
  adds or removes teammates (`"add"`, `"remove"`); `DELETE /api/v2/groups/{id}` removes it, and the groups and teammates in it move up to
  its parent. A group cannot go under itself or a group inside it, and a built-in bot cannot join one.
- **With the tools**: `hub group list` and `hub group update [<group>] [--name] [--parent] [--add-human] [--add-bot] [--remove-human]
  [--remove-bot]` (`hub_group_list`, `hub_group_update`); with no group given, `update` creates one from `--name`. BotOps does the same as the
  person who asked it, with their own rights: a member is refused, an owner or an admin is not.

Naming a group in an access list (`group:legal`, [permissions.md](permissions.md)) or in a human's `primary_for` names everyone in that
group and in the groups nested in it.

**The team builder.** Finish setup builds the chart by group: each bot goes in the group its template belongs to (Marketing, Product,
Engineering and so on), and the group is made if the team has none yet. A group with the same name that the team already has is reused.

**Older rosters.** Before groups there were three overlapping ideas: `teams` (a group with a root bot, everything under it), org groups
(labels on the chart) and a bot's department (its template's group in the team builder, or its manager's). The first time a team
starts on this version, each becomes a group and each bot becomes a member of the group it had; a human's `team` that named no group
becomes one too. Nobody changes group and `reports_to` is not touched. It runs once and is safe to run again.

## The humans

The example team, Acme, has Ana Rivera at the root. Its groups are, for example, **Marketing**, **Product**, **Engineering**, **Sales** and
**Operations**.

Each human has a manager (`reports_to`), a group (`team`), and optionally a personal message bot. In the web
team chart, personal message bots appear as messaging icons beside their humans; the icons open the
matching bot under **Message bots**. Shared mailboxes such as `shared@acme.example` can stay on the roster for mail routing with
`hidden: true`, so they do not appear in the tree.

Every Acme address is `<firstname>@acme.example`. Personal message bots come from the `inbox`
template and read only the assigned mailbox by default. The template seeds one weekday 07:30 Routine in America/Los_Angeles, initially disabled.
Extra passes and reading reports' mail (`org_read: true`) are optional owner choices. Each mailbox has its
own rules in `registry/mail-rules.yaml`. Photos prefer a Google Workspace Directory thumbnail
when domain-wide delegation includes `admin.directory.user.readonly`; otherwise the Slack
profile image stored on the roster.

## Bots in groups

| Group | Bots |
|---|---|
| marketing | The CMO bot and the SEO, analytics, listening and content bots. |
| product | The Product Manager bot and everything under it. |
| sales | Sales operations and the sales-process bots. |
| engineering | The CTO bot, which monitors CI, deploys, alerts, security and cost. |

A group's lead bot is a member like the others. Bots in no group belong to none; the human they are mainly for is `default_user`.

An operations bot such as a **COO** can span every group. It is in none, because its job is to serve all of them: it reads every bot's runs,
tracks what each is still missing, ages the queue of what needs a human, and publishes a weekly status broken out by group.
Routing a note, and writing the notes when a meeting is handed over, are **functions Tico
performs itself**, in seconds, with no run behind either.

## Who a bot is mainly for

The human to ask when that bot needs a human, and the human its work is for.

1. Every human whose `primary_for` names the bot's **group** (or a group it is nested in) or the bot's own **slug** is a primary
   human for it. More than one human on a bot is fine.
2. If nobody claims it that way, the primary human is `default_user` (the root human).

So in Acme, Ana can be primary for the marketing group, Ben Okafor for the product group, and Ana
for everything outside the groups. Cara Mendes can also claim engineering so she can talk to the
CTO bot. To hand one specific
bot to someone without giving them the whole group, put that bot's slug in their `primary_for`.

## Where notes go

A human's `bot` is where Tico sends a meeting when they choose **Send to bot > Auto**. If they have no bot, Auto uses the
default bot (Assistant when active, otherwise BotOps). There is no separate routing model call. Choose a named bot in the dialog
when the work belongs elsewhere. See [Meetings](meetings.md) for the resulting task and `backend/media.py` for the API.

## Adding a human

Choose **Team > + > Add human** for an email and optional name, or open **Settings > Humans > Add manually**. No email is sent.
Owners and admins can add humans outside the team's domain;
members with **Can add humans** can add coworkers in the domain. See [Humans](people.md) for sign-in and directory sync.

### First-boot seed files

`registry/people.yaml` and `registry/hub-access.yaml` seed a new team only. Editing them and redeploying does not add a human
or change sign-in rights on an initialized team. Use Settings or the Humans API for later changes.
