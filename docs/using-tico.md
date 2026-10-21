# Using Tico

Short answers to common questions. Sign in at your team's Tico address with the email on its Humans roster. Ask your owner for the address.
How the pieces fit is in [How Tico works](how-it-works.md). Words are defined in the [Glossary](glossary.md); [Navigation](navigation.md) lists the current controls.

![Tasks in the demo team](images/tasks-desktop-light.png)

Every picture in these docs is a screenshot of [demo mode](demo.md), which you can run yourself.

**How do I give work to a bot or a human?**
On **Tasks**, press **New task** and pick who it is for — any human or bot. On a human's profile,
**Give a task**. On a bot, ask it in Chat. Or ask your own external agent (Grok, Muse, Claude and others) to file
it through Tico's MCP. Each task shows who added it: you, another human, or a bot.

**Where are the built-in bots?**
**Assistant** and **BotOps** are in the main left rail; **Assistant** opens your private chat with it. The Goal Manager is at the top of **Goals**: its routines, its last
run and a box to ask it to change a goal. **Docs** and **Market** have **Ask the Librarian** on the right (a button on a
phone). All four are managed in **Settings > Bots**; they stay out of the team chart and the goal owner list.

**What is on a bot's page?**
Its name, the mark for the harness it runs on, up to three tool icons and its goal across the top, the chat below,
and a rail down the right: **Active** tasks first, then its latest **Updates** (all of them behind the small icon),
**Files** and **Routines** (a row opens the routine to read or edit), with **Assigned to others** and **Done** folded at the foot. A section with
nothing in it is left out. **Info** holds its compact summary, Tools, goals and routines. **Instructions** shows its instructions and working notes, **History** holds updates, status changes and runs, and **Settings** edits its configuration directly, without an edit popup. On a phone, Chat and
Tasks are one tab each.

**How do I ask a bot a question?**
Open the bot's page and use its **Chat** tab; the reply comes back into the same conversation.
Work on a task happens in that task's own thread: tap the task's row in the chat to follow it or write to it.
The chat itself gets one line when the task starts, is blocked, done, declined or stuck ([Sessions](sessions.md)).
For anything across the team, ask your own external agent: **Connect an external agent** (the plug button beside
your email) gives it a token and Tico's MCP ([Connect an external agent](connect-an-agent.md)).

**How do I know my bot ran?**
The conversation goes *Saved — queued* → *Starting* → *Working* → the reply. When the bot marks
a task done, the task shows under **Tasks → Done** with the bot's note.
Every run is under **Settings → History → Runs** (the owner also sees **Changes**, the bot settings history), and
on the bot’s **History** tab.

**What does *waiting* mean, and the other states?**
*Waiting*: the bot is parked on something — a bot, a child task, or an answer from you.
*Doing*: being worked on (*Doing · starting* until a computer picks it up). *Needs you*, or *Needs <Name>* for someone else: a
human must act — a question, an approval, a blocked or declined item. The all-owners list groups these as **Needs a human**.
A comment on a task answers that task's question, so the bot can ask its next question; it does not answer a different
task in the same room. *Done*: finished, awaiting close.

**Marked a task Done by mistake?**
Choose **Undo** in the toast, or open the finished task under **Done** and choose **Reopen**.
A task you requested for yourself closes when you mark it Done; Reopen works for it too.
The earlier completion stays in its history, while Goals and KPIs count it as active again.

A bot's final answer to a reply appears under that update automatically. An acknowledgement such as
"Thanks" needs only a short answer; it does not need a new task. `hub_update_reply` is for humans.

**My Mac is asleep. What happens?**
Your team's Tico server keeps running: everything is saved and other humans' bots keep answering. Bots on your
Mac show *offline* and new requests show *Saved — waiting for <your Mac>*; they run when it wakes.
A run cut off mid-way is settled by Tico when that is safe (its task was already done, or it
had not used any tool yet) and otherwise opens in front of you as *needs you* the next time you
look at the app, with *Review now* on it. Nothing is silently re-sent. Your Mac being offline for
a few minutes is not something you are asked about; after ten minutes it is.

![Needs you: a decision and an approval waiting](images/needs-you-desktop-light.png)

**What needs my approval, and why?**
Bots carry out authorized work with their granted Tools. Spending, publishing, record changes,
bot or repository deletion, adding outside humans and updating Tico have no blanket Confirm step.
Messages to outsiders stay drafts until the bot's owner turns on its outbound send switch.
A bot may request an optional approval when an action is uncertain; its exact action appears in
**Needs you** with **Approve** / **Decline**. Repository review rules still apply.
Full rules: [Permissions](permissions.md) and [approval policy](../policies/approvals.md).

![The team chart with goals](images/org-chart-desktop-light.png)

**How do I rearrange the team chart?**
Drag a human or a bot in the sidebar onto the human or bot it should report to. The owner can
move anyone; everyone else can move what reports up to them, under themselves or their own
reports. The same rule decides whose profile, goals, bot settings and routines you may edit:
your own, and anyone's below you.

**How do I add a routine?**
**Settings → Routines → New routine** (or the Routines card on the bot's page): pick the bot, a
title, a cron (five fields, America/Los_Angeles by default) or a Tico event, and the text the bot
is told each time. A bot can set up its own with `hub routine set`. Details: [Routines](routines.md).

**How do I change what a bot does?**
Open **Bot → Instructions**, describe the change in **Request instruction changes**, and choose
**Send changes to BotOps**. BotOps updates the Instructions, commits and pushes the result; the
bot reads it at its next run once its computer's checkout has the update. An older app may show
the **Instructions** tab. See [Creating bots](creating-bots.md#edit-instructions).

For a manual edit, change `AGENT.md` in `bot-<slug>` (playbooks and `knowledge/` for methods and
facts), commit and push. The computer pulls before the next run; if you maintain its checkout by
hand, pull the update there too. Model, effort, computer, owners and status are changed in
**Bot → Settings**. One-off requests are tasks, not edits.

How often a bot wakes for routine news is its **Wakes** row in **Bot → Settings**: **Token saver** (the default for
new bots) or **Performance**. Under **Subscribe to updates** in its Settings tab you choose whether it hears about
template changes and Tico releases. See [Creating bots](creating-bots.md#subscribe-to-updates).

**How do I contact the Tico team?**
Click **?** at the bottom of the sidebar (**Support** on a phone), write your message and press Enter. Replies come
back into the same pop-up. See [Support](support.md).

## Updates

Every bot reports in once a day, and on Friday with a week in review (five slides). People report in the same shape.
An update is one to five plain bullets: what you did, what you do next, and what waits on someone.

**Updates roll up the org chart.** From 05:00 Pacific, Tico asks the bots one at a time, starting with those
lowest on the chart. A bot that manages others is asked once each of its direct reports has posted, missed or been
skipped, or 90 minutes after it was first held for them, whichever comes first. A report asked again after a failed
run does not restart that wait. Its request carries its direct reports' updates
and names anyone who had none. It writes its own update from its work and theirs. Its card says **From:** with the
updates it was written from, each a link: in the chart it opens that card (and the cards above it) and goes to it; in
**List** it goes to that update, or to its author's page when that update is not in the list. You only see the links
to updates you may read, and `hub update show` lists them
as `inputs`. **Redo** of a past day can include the managers above (`hub update redo --bot X --with-managers`).

**Your update.** Once your reports are in, your update is drafted and posts at once as yours, labelled **Drafted by
Assistant**. Tico writes the draft with one call to your company's own model, and that call gets only what everyone
in the company can already see: your tasks that are not private and your goals; the status changes you made and the
comments you left and the questions you answered on those tasks; the meetings you were in that are not
private (a short part of each); and your reports' updates. It leaves out approvals, and anything involving a private
task or a bot not everyone may read. The draft is not written in an Assistant chat, so your private chats
with the Assistant never reach it. With no model provider key on the server, there are no drafts. Choose **Edit** on your card to change
the draft (the label then says edited); tick **These are my own words** in the editor to drop the label. Once you have posted
words of your own that day, a daily or a week in review, the Assistant does not draft for you that day. **Friday** is
the week in review: on the **Weekly** tab, **Write your update** or **Edit** opens it as its slides (a one-sentence
goal, one to five things done, one to three to focus on next, up to three blockers, and KPIs if you like), one per
line. Turn drafting off with **Assistant drafts my update** on your
person page.

**Reading them.** **Updates** shows the day as the org chart, with your card on top. The icon under a card opens its
reports' cards, and theirs below. By default you see the cards below you (the whole chart if no one reports
to you); **Whole org** shows the whole chart, and
**List** shows every update newest first. Only your direct reports' updates count on the **Updates** badge and
carry a dot in the chart; updates further down are marked read as you see them, but never alert. People's updates are
open to everyone on the team (not to external agents); a bot's are open to anyone who may read that bot. Bots read them with
`hub update list` (`--scope direct` for just their direct reports).

## Leave work for the next run

A **Note** gives a bot context without starting a run. A **Task** filed with `--next-run`
(alias `--quiet`) is work to do, with an owner, status, result and history; creating it does not
wake the bot. Use your [personal token or external agent](connect-an-agent.md) for the CLI:

```bash
hub note create content "Use the revised tone guide for the next draft."
hub note list --to content --waiting
hub note delete <note-id>
hub task create --owner content --title "Draft the weekly post" \
  --body "Use the notes attached to this task." --next-run
```

`--text-file notes.txt` can replace the note text; `--body-file request.txt` can replace a task's
body. Notes are only for other bots, never a human or the sender itself. You need Write access to
leave one, and the normal task/contact permissions apply to deferred work. Never put a secret in either.

The next run started by chat, a Routine or another task carries waiting notes and deferred tasks.
For a Note, the API reports `waiting`, `carried` and `cancelled_at`. `carried` means a run received
it, not that the bot completed work. A failed run makes it waiting again. A sender or a human who
can see it can cancel a waiting note; once a live or completed run carries it, it cannot be taken
back. Cancellation keeps the history. Bots see notes sent by or to them; humans see their own
sent notes and notes between bots whose activity they may read.

A deferred Task remains open with `next_run_waiting: true` until a run carries it. It then follows
ordinary task states and needs a completion result. Close it with
`hub task close <task-id> --note "Cancelled before work began"` under the normal task permissions, or choose
**Run now** on the task to start it sooner. Deferred does not mean delayed forever: the stalled-task
check can wake an idle bot after 30 minutes when no other work or Routine will move it.

The task shows where it stands, under **Quiet work** on the task and as `quiet` in `hub task show`:

- **Eligible**: the bot's next run carries it.
- **Delivered**: a run carried it; which run and when are shown. **Deferred** is a delivered task whose run said
  when to pick it up again.
- **Queued**: it has a run of its own, waiting or going (after **Run now**, or when its resume condition came true).

A run that carries a task it will not do now says when to pick it up, rather than in its reply's words:

```bash
hub task defer <task-id> --after <other-task>   # when that task is done or closed
hub task defer <task-id> --at 2026-10-20T09:00Z # at a time
hub task defer <task-id> --next-run             # with the next run, whatever starts it
hub task defer <task-id> --handled              # this run is done with it for now (MCP: hub_task_defer)
```

When the condition comes true the task gets one run of its own, as **Run now** gives it. A run that ends without
working a carried task (a comment, a step, status or note change, a pull request, or `--handled`) and without a
condition hands it back: it is eligible again and the next run carries it, with a line in its history. The bot's
own task, or a person, may defer it (`POST /api/v2/tasks/{id}/defer`). The Health Monitor names a quiet task handed
to a run more than two hours ago and not worked since, with no condition (`quiet_unworked`).

The equivalent API calls are internal routes (subject to change); use your own rights and an
`Idempotency-Key` on writes:

```text
POST /api/v2/notes                    {"to":"bot:content","text":"Use the revised tone guide."}
GET  /api/v2/notes?to=bot:content&waiting=true
POST /api/v2/notes/<note-id>/cancel    {}
POST /api/v2/tasks                    {"owner":"bot:content","title":"Draft the weekly post","body":"Use the attached notes.","next_run":true}
```

**Where do files go?**
Files you attach to a task or a chat are stored privately by your team's Tico server and the bot downloads them
for that conversation (up to ten files of 10 MB). A file a bot produces for you is attached to the
task the same way (`hub task attach`) and linked from its note; open it from the task or the link
while signed in. Other files a bot publishes are listed on its page under Files ([Files](files.md)); a bot's own
record stays in its repo's `reports/`. Meeting transcripts come from the tools that made them ([Meetings](meetings.md)).

When someone finishes or declines a task you requested, Tico DMs your linked Slack account; turn off **Task results in Slack** on your human page under **Profile → Notifications**.

**How do I review message bots?**
Choose a mailbox or Slack channel under **Message bots** in the sidebar. The bot's instructions and
routines are on the left, with example messages and actions to review on the right. Open an
email or Slack item for its thread. Email is a synced copy, with no per-human Google sign-in.
Ana can review all mailboxes; other humans see only mailboxes granted by the roster. Slack
channel history is owner-only in this view.

**How do I add a human or a bot?**
A human: **Settings → Humans → Add manually** (an owner or admin), or sync them from the team's
directory there; behind Cloudflare Access, also allow the address in its policy ([Humans](people.md)). A
bot: **Settings > Bots > Add from template**, or ask BotOps to build it. Open the bot and press **Set up** to finish its
first conversation. See [Creating bots](creating-bots.md) for custom Instructions and manual setup.

**How do I use my own branch of a bot?**
Its owner enables **Allow branches**. Choose **Make my branch** on its page and your computer.
If all your computers already run the original or a branch of it, choose **Create planned branch**.
It starts when you add another computer in **Settings → Computers**. Without a GitHub App, the
computer clones the original repository using your own git access; a local checkout can be reused.
Health names the repository and clone command if that fails. You can also ask BotOps to fix it.
The branch picker opens the original or a person's branch. New tasks and chats to the original go to your active branch.
Branches share instructions and repository lessons; `hub bot copy` creates an independent bot. See [Branches](creating-bots.md#branches).

## Tags and release checklists

Tags give related tasks a shared label, metadata and a Markdown checklist. A chip can show
`release · Oct 2`; its key, such as `release-2026-10-02`, identifies that particular release.
Click a tag chip to open its notes and tasks. The task filter uses tags. Existing label strings,
`hub task label` and `--label` continue to use the same keys. Unknown keys create plain tags.

Open **Settings > Tasks**, under **Task tags**, to create tags or templates. A template holds a reusable checklist and
metadata defaults. Open it and choose a key under **Make a tag** to copy those defaults into a new
tag; later template edits leave existing instances alone. Put the instance on tasks. Templates
cannot be attached directly.

The team owner and humans on the leadership, product and engineering teams can attach and
remove tags, just as they could change labels. A tag's owner (human or bot) can edit its notes,
metadata and label, as can those task movers. Anyone signed in can create a tag they own; a mover
can create one for another teammate. A tag page lists only tasks the viewer may read. Tick a
checklist item to save it. If another teammate saved first, load the current notes and combine your changes. Your open notes draft stays in place.

### Create a release template

There is no automatic `release` template seed: that key may already be a plain task label.
Create a separate template key once, in Settings or with these commands:

```sh
cat > release-checklist.md <<'CHECKLIST'
- [ ] Migrations / scripts to run
- [ ] Smoke checks after deploy
- [ ] Tell the team
CHECKLIST
hub tag create release-checklist --label release --template --markdown-file release-checklist.md
hub tag create release-2026-10-02 --from-template release-checklist --metadata '{"date":"2026-10-02"}'
hub task label <task-id> --add release-2026-10-02
hub tag show release-2026-10-02
hub tag update release-2026-10-02 --version 1 --markdown-file release-checklist.md
```

The release bot can own an instance, gather the tasks going out, record checklist results and
tell the teammate coordinating the release when it ships. Its tag-owner rights allow checklist
edits; attaching tags still needs a task mover. Use `hub_tag_list`, `hub_tag_show`,
`hub_tag_create` and `hub_tag_update` through MCP. Update calls include the current `version`;
a stale version returns `409 version_conflict` without overwriting the checklist.

## Task types and steps

Tasks use **General** until you choose another type. Movers add types and named steps in
**Settings → Tasks**. A custom task's **Step** control sets its status; General keeps the usual
**Status** control. Select **Filter → Type** on the board to use that type's steps as columns.
Bots and older runners keep using statuses. See [Task types and steps](tasks.md) for mapping rules,
CLI, MCP and API examples.
