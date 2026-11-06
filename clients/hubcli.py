#!/usr/bin/env python3
"""`hub`: what a bot calls inside a turn to talk to Tico.

Thin on purpose (docs/history/hub-v2.md §5): parse the arguments, hand them to `clients/remotecli.py`,
which makes one HTTP call against the Tico API and prints JSON on stdout. Every rule lives on
the server (`backend/hubdb.py`), never here. A command is its tool's name (clients/hubtools.py) with
`hub_` dropped: `hub message send` is `hub_message_send`, `hub bot go-live` is `hub_bot_go_live`.

    hub whoami
    hub message send <bot|human> "<text>" [--fyi] [--conversation ID] [--ref task:ID ...] [--steer | --queue |
                    --defer [30m|4h|2d|never]] [--needs-action]
                                           --fyi: expects no reply, takes no conversation or references; to a bot,
                                           --steer goes into its running turn, --queue right after it, --defer with
                                           its next turn (at the latest after the time given)
    hub message list                       what waits for you
    hub message mark-read <message-id>     an external agent's own delivery
    hub message redact <message-id> [--label L]   take a secret (read from stdin) out of a message
    hub thread catchup <thread-id> [--thread-only]
                                           what is new in a thread and its sub-threads you may read, summarized into
                                           your Assistant DM (the newest 40 messages; nothing new posts nothing)
    hub conversation show <conversation-id> [--before MSG] [--since TIME]
                                           what was said in a conversation: the newest 200, oldest
                                           first; has_more says there is an older page, --before
                                           its next_before reads it. The run's prompt names it
    hub thread list [--with ACTOR] [--cursor C] [--limit N]   your threads: needs attention, then unread, then recent;
                                               DMs apart; --with: only the threads shared with that actor
    hub thread show <thread> [--cursor C] [--flat]   one thread in tree order: a message's sub-thread sits under it
                                               (--flat: it and its open sub-threads as one list in time order)
    hub thread home <actor>                    a teammate's Home thread, made on first use (needs Read on them)
    hub thread dm <actor>                      your DM with one person or bot: the open one, or a new one (needs Write)
    hub thread dm-group <actor> <actor> [...] [--body "<text>"] [--title T]   a group DM with these people or bots;
                                               --body is the first message; a new one with 2+ others needs --title
    hub thread create ["<title>"] [--audience members|team|company|personal] [--group G] [--member ACTOR ...]
                    [--body "<text>"] [--pin-for-members]   the first message goes in with --body; a title is needed
                                               unless it is a DM with one teammate
    hub thread pin|unpin <thread>              pin a thread for yourself, or unpin it (even one pinned for everyone)
    hub thread catch-up [--cursor C] [--limit N]   each thread with something new for you, mentions first, with its
                                               new messages; mark one read with hub thread read <thread> --all
    hub thread post <thread> "<text>" [--to ACTOR] [--reply-to MESSAGE] [--ref KIND:ID ...]
                                               --reply-to answers that message: yours stays in the thread, quoting it
    hub thread bot-status <thread>             the bots it waits on: working, up next or can't reply, and why
    hub thread reply <thread> <message> "<text>"   a separate side topic in that message's sub-thread (to answer a
                                               message, post --reply-to)
    hub thread edit <message> "<text>"         edit a message you wrote: a new revision
    hub thread sub <message>                   open a message's sub-thread without posting
    hub thread react|unreact <message-id> <emoji>   your reaction to a message, such as :+1:
    hub thread open-earlier <thread>           a bot's manager opens its earlier messages to its Home readers
    hub thread task <message-id> --title T --owner O   a task from a message: private unless the thread is company-wide
    hub thread delete <message-id>             delete a message you wrote (a marker stays)
    hub thread upload <thread> <file> [--text T] [--to ACTOR] [--under MESSAGE]   a file in a message
    hub thread follow|unfollow <thread> [--notify live|mentions|digest|muted]
    hub thread read <thread> [--seq N | --all]
    hub thread search "<words>" [--cursor C]   the messages in threads you can read
    hub thread members list|add|remove <thread> [<actor>] [--owner]   actors: human:<id>, bot:<slug>, group:<id>
    hub thread update <thread> [--title T] [--archive|--unarchive] [--audience ...] [--group G] [--keep-days N|--keep-forever]
                    [--pin-for-everyone|--unpin-for-everyone]   a pin for everyone: its starter or an admin
    hub thread link-slack <thread> [<channel>] [--history] [--off]
                                           a thread's Slack link (admins): with a channel, asks for it and Tico
                                           checks it; --history also brings the channel's earlier messages; --off
                                           takes it away; with the thread alone, lists its links
    hub thread slack-resume <link-id>          check a suspended or refused Slack link again (admins)
    hub note create <bot> "<text>" [--text-file f] [--defer 4h]  a deferred message: wakes nobody; the bot's next
                                           run reads it (with --defer, a run starts by then if none came)
    hub note list [--to me|X] [--from me|X] [--since 24h|ISO] [--waiting]   notes, newest first
    hub note delete <id>                   take back a note no run has carried yet
    hub feedback give message|task|approval <id> up|down [--reason R] [--tell-bot]   a person's 👍/👎 on a bot's work;
                                           again replaces it; --tell-bot (👎) leaves the bot a note
    hub feedback remove <id>               take back your feedback (and its note, if no run carried it)
    hub feedback markers message|task|approval <id> [<id>...]   your feedback on these and everyone's up/down counts
    hub feedback list [--bot B] [--rating up|down] [--since 7d] [--cursor C] [--limit N]   newest first
    hub feedback reasons                   your five latest distinct reasons
    hub feedback rule-draft <id>           a one-sentence rule drafted from your 👎; stores nothing
    hub feedback rule <id> "<text>" [--all-bots]   your 👎 as an org rule: active if you manage the bot, else suggested
    hub task create --owner <bot|human> --title "..." [--body "..."|--body-file f] [--due D] [--parent ID]
                    [--goal ID] [--model M [--effort E] [--harness H]] [--dry-run] the checks a create would fail,
                    nothing written
                    [--private [--reader X ...]]  private: only its requester, owner and readers (people, bots,
                                           group:<id>) see it
    hub task readers list|add|remove <id> [X ...]   who can see a private task besides its requester and owner
    hub task followers <id>                who follows a task, each at their level
    hub task follow|unfollow <id> [--level live|mentions|digest|muted] [--who X]   follow a task or stop; --who: for
                                           someone else who sees it, when you may change the task
    hub task model <id> [<model>] [--effort E] [--harness H] [--clear]
                                           the model this task's runs use; --clear puts it back on the bot's task model
                    [--defer [24h|2d|never]]  for a bot: a deferred task, no wake; its next run carries it, and
                                           one starts by the time given (24h when none; --next-run is the old name)
                                           blocked on a person? --owner <person> --parent <your task>
                                           for a person, the body's first paragraph is the ask (as --note below)
    hub task update <id> --status waiting --on <person> --note "<plain ask>"
                                           ask a person on your task: one small subtask for them, yours stays blocked
    hub task question "<question>" --on <person> [--why "<why it matters>"]
                    [--option "A -> what happens" --option "B=<file> -> what happens"] [--multi --recommend A]
                                           one decision as a task they own, under your task; none for Yes / No.
                                           Their answer closes it. Never to a bot: DM a bot instead.
                                           Every option says what happens (->); several picks (--multi) mark
                                           the recommended ones; no ids, paths or errors above --body
                    [--if-no-answer "Yes" --default-kind reversible|send|spend|delete] [--by D]
                                           what you do if they don't answer: a reversible step goes ahead
                                           after the team's days (3); asks expire after 14 days
    hub task comment <id> "<text>" [--steer | --queue | --defer [30m|4h|2d|never] | --goal]   on the record
                                           with your name; reaches the task's followers and whoever you @mention.
                                           --steer (the default) goes into each bot's running turn, --queue right
                                           after it, --defer with its next turn on the task (at the latest 24h, or
                                           the time given; never notifies no follower), --goal sets its goal
    hub task comment-edit <id> <comment-id> "<text>"   change a comment you wrote; wakes nobody
    hub task comment-delete <id> <comment-id>          take back a comment you wrote
    hub task delete <id>                               delete a task made by mistake, to the trash (a person only)
    hub task requesters <id>                           who asked for it (internal contacts, counts; never tokens)
    hub task requester-save <id> [--requester-id R] [--email E] [--organization O] [--important]
                                                       add a requester, or update one with --requester-id
    hub task requester-remove <id> <requester-id>      take a requester off a task
    hub task publication-preview <id> [--public-title T] [--public-summary S] [--visibility public|private]
                                 [--label L] [--comment "..."]   the exact payload a publish approval needs
    hub task comment-moderate <id> <comment-id> hide|show|delete   a public comment on a task you manage
    hub roadmap tasks [--sort requester_count|external_requesters|important|organization|size]   ranked roadmap
    hub roadmap settings [--label L] [--cors-origin O] [--action-limit N] [--suggestion-limit N]
                         [--bot-public-approval|--no-bot-public-approval] [--suggestions-bot SLUG|--automatic]
                                                       read, or change (owners and admins)
    hub task deleted                                   deleted tasks you may restore
    hub task restore <id>                              put a deleted task back
    hub task update <id> --status doing|waiting|done|declined [--note "..."] [--goal ID|--goal ""]
                    [--title "..."]        rename it: checked as a new task's title would be
                    [--on PERSON]          with --status waiting: files the person a question task under it
                                           (their Needs you); --note is your ask, one or two plain sentences:
                                           "Approve the video tool trial on Thursday? If yes, I'll send the invite."
                                           --on "" withdraws it. Not a bot: DM it, or hub task wait on its task
    hub task close <id> [--note "..."]
    hub task wait <id> --on task:<id>|pr:<url>|release:<tag> [--note "..."]
                                           wait on a task, a PR merging or a release; one wake when it happens
    hub task snooze <id> --until DATE [--note "..."]
                                           set it aside until DATE: off the lists and quiet until then
    hub task later <id> --after ID|--at DATE|--next-run|--handled
                                           a deferred task this run carried and will not do now: when to pick it up
                                           (`hub task defer` is the old name)
    hub task unsnooze <id>                 bring a snoozed task back now
    hub task waive-qa <id> --head SHA --reason "..."
                                           a person waives a Dev ticket's In QA step for that exact head; a waiver,
                                           never a QA pass (CI and both reviews on that head stay required)
    hub task attach <id> <file> [--name "..."]
                                           store a deliverable with the task; prints the link
    hub task list [--owner me|X] [--requester me] [--status open|doing|waiting|done] [--member me|X [--role R]]
                  [--all]                  the board: every task and every bot you may see, {tasks, bots}
                  [--stuck [--hours N]]    BotOps's sweep: open work untouched for a day that waits on nobody
    hub task show <id>                     <id> is the full id or its first 8+ characters (short_id in the list)
    hub file publish <path> [--title T] [--task ID] [--scope task|bot]
                                           list a file from this checkout on your page (reports/x.md);
                                           publishing it again adds a version (docs/files.md)
    hub file link <https-url> [--title T] [--task ID]   a Google Doc, Notion page, Figma file
    hub file touch <file-id|url>           you edited a linked document again: it moves to the top
    hub file import s3://bucket/key [--title T] [--task ID]  copy an object with this computer's credentials
    hub file list [--bot X] [--limit N]    what is on the page, newest activity first
    hub file search "<words>" [--limit N]  every file you may read, by title and text, with an excerpt
    hub file read <id> [--version N]       one version of a file you may read, as text, in full
    hub file delete <id>                   the owner deletes a file for good: its bytes leave storage
    hub page publish <file.html> [--title T] [--cadence none|daily|weekly|monthly] [--as-of YYYY-MM-DD]
                  [--task ID] [--replaces PAGE]
                                           a page people see with an updated line; again adds a version
                                           (docs/pages.md)
    hub page list [--bot X] [--task ID] [--archived]   pages you may read, newest first
    hub page show <id>                     one page's status: updated, by, version, cadence, stale or replaced
    hub page access <id> [--audience everyone|ben,group:legal,bot:analyst]
    hub page archive <id> [--restore]      hide a page from lists and search, or bring it back
    hub card list [--place P]              what is pinned at dashboard, growth, team:<group>, bot:<slug>, task:<id>
                                           or person:me (My dashboard)
    hub card add <template> [--place P] [--team G] [--weeks N] [--count N] [--kpi ID] [--title T] [--size S]
                                           [--metric M] [--channel C] [--months N]
                                           tasks_by_status, done_per_week, needs_you, spend, kpi, automation_runs,
                                           latest_pages, needs_attention, unread_threads, my_tasks, updates; a bot
                                           pins to its own sidebar or task. The Growth page's cards (growth_number,
                                           growth_funnel, growth_channels, growth_retention, growth_board) go on
                                           --place growth only; --metric and --channel are for growth_number
    hub card update <id> [--title T] [--team G] [--weeks N] [--count N] [--kpi ID] [--metric M] [--channel C] [--months N]
    hub card remove <id>
    hub email mailboxes                    your readable email mailboxes (hub mail is postal mail)
    hub email inbox <address> [--q Q] [--limit N] [--cursor C] [--json]
    hub email search <address> "<gmail query>" [--limit N] [--cursor C] [--json]
    hub email thread <address> <thread-id> [--json]
    hub email message <address> <message-id> [--json]
    hub email archive|unarchive|mark-read|mark-unread <address> [<id> ...] [--query Q] [--limit N] [--json]
    hub email label|unlabel <address> <label> [<id> ...] [--query Q] [--limit N] [--json]
    hub inbound audience <inbox-id> company|team [--group G]
                                           who reads a Tico address or number (owner or admin)
    hub pin add <place> <page:FILE|card:ID> [--size small|medium|large] [--position N]
    hub pin update <pin-id> [--size S] [--position N]
    hub pin remove <pin-id>
    hub doc list [--prefix sales/]         the company's internal docs by path
    hub doc read <id|path|external:id|manual:name>  one doc in full, with its revision and provenance
    hub doc search "<words>" [--manual] [--market]
                                           internal and external docs, linked metadata and the Tico manual (--manual:
                                           only it; --market: also the market's notes, entities and evidence)
    hub doc write <path> --title T (--body-file F | stdin) [--note N]
                                           create or replace a doc; every write is a version
    hub doc history <id|path>              its versions: who changed it, when and why
    hub doc link-list                      where the company's other docs live (links, never copies)
    hub doc ask "<question>" [--wait 120]  ask the Librarian about the team's docs: {answer, citations, covered}
    hub doc fetch <url> [--max-chars N]    read one public link (web page, Google Doc, Drive folder, GitHub repo,
                                           sitemap) as text; runs on this computer, public addresses only
    hub meeting search ["<words>"] [--person P] [--since D] [--until D]
    hub meeting read <id> [--offset N]     a transcript
    hub meeting pending                    your Pending queue
    hub meeting approve <id>|--all         share your meetings
    hub meeting dismiss <id>               keep a meeting out of the queue
    hub meeting import <file> [--title T] [--date D] [--participant P ...]
    hub call list <thread> [--limit N] [--before T]  the calls in a thread, newest first
    hub call start <thread> [--title T] [--speak]  start a call there; you are its connector
    hub call show <call>                   a call: its state, people, bots, how it ended
    hub call join <call>                   join: the URL and a token good for 5 minutes
    hub call token <call>                  a fresh token while you are joined
    hub call leave <call>                  leave a call you joined
    hub call end <call>                    end a call you started; its transcript goes to the thread
    hub call retry <call>                  ask again for action items the Gateway could not make
    hub call bot add <call> <bot>...       bots listen to a call and reply in its thread
    hub call bot remove <call> <bot>       take a bot out of a call
    hub assistant propose --summary "..." --path /api/v2/... [--method POST] [--body '{...}']
                                           Assistant only: ask the person to confirm a side effect (an
                                           approval, anything outside the company, spend, settings,
                                           archive/delete, activating a bot); it runs only on their click.
                                           Several actions: ONE card, --path /api/v2/assistant/batch
                                           --body '{"actions": [{"path": "/api/v2/tasks/<id>", "body": {...}}, ...]}'
                                           (task actions, approval decisions, answers to bots' questions)
    hub assistant morning --for human:<id> --body "<text>"
                                           the Assistant's background run only: the person's one morning message in
                                           their own Assistant chat, as the Assistant (no card); a second one that
                                           company day is refused
    hub assistant learn "<line>" [--company]
                                           in a person's own chat: save what they taught you, in their own
                                           layer (--company: the Strategy Bot, about the company, owner's chat only)
    hub assistant learned                  what a person's chat bot has learned (What it has learned)
    hub assistant proposals [--proposer botops] [--owner human:ana]
                                           the Confirm cards still waiting for their owner, with their ids
    hub assistant withdraw <id> [--reason "..."]
                                           take back a Confirm card that waits: it never runs, and the owner's chat
                                           says who withdrew it (only the owner confirms)
    hub assistant change list [--days N] [--limit N] [--before NEXT]
                                           what your Assistant did for you, with the words it acted on, and whether
                                           each can be undone (and why not)
    hub assistant change restore <id>      undo one of those changes as you; restoring it again changes nothing
    hub origin show <ref>                  why an item is on your list: who made it, its cause (automation, rule,
                                           parent task, bot), its siblings this week, and commands that stop it
    hub replay show [--from ISO] [--to ISO] [--bot B|--person P|--team T]
                                           what happened in a window (default the last 24 hours), as you may see it
    hub goal list [--owner me|X] [--all]   what you are for: your goals in order, the chain above
                                           them, and your reports' goals; --all is every goal
    hub goal show <id>                     the goal, its KPIs (target, colour), tasks, check-ins, history
    hub goal read-back '[{"kind":"goal_create","owner":"company","title":"..."}]'
    hub goal save POST /api/v2/goals '<body>' --on-behalf-of turn
    hub goal save POST /api/v2/goal-proposals/turned-down '<body>' --on-behalf-of turn
                                           the owner's "no" to a suggestion you made in chat: stored at once as turned down
    hub goal create --owner me|X --title "..." [--parent ID] [--body "..."|--body-file f] [--top] [--bet ID]
                                           set a goal; a parent is optional. With one, it supports that goal
    hub goal status <id> red|yellow|green|done|dropped "<one sentence>"
                                           set the colour by hand: it sticks (with your name) until handed back
    hub goal status <id> auto              let the Strategy Bot set the colour again (ends a colour set by hand)
    hub goal refresh [--goal ID ...]       the Strategy Bot's status pass: work automatic colours out again
    hub goal checkin <id> "<words>" [--signal on_track|at_risk|off_track] [--from X] [--kpi ID]
                                           how the owner says it is going, in their words (colours a goal with no KPI)
    hub goal checkin-list <id>             a goal's check-ins, newest first
    hub goal needs-you                     red KPIs on your goals, stale KPIs you own, proposals to confirm
    hub goal company                       the company's Vision and Description (read-only)
    hub goal strategy-bot                       the Strategy Bot: its level (1 Passive to 3), regular checks and today's
                                                Needs you count
    hub goal strategy-bot-merge <person> "<title>" "<question>" <task-id> <task-id>...
                                           the Strategy Bot at level 2+: one item for several bots' same ask of a person
    hub goal strategy-bot-level3-offer          the Strategy Bot: record the level-3 offer its Monday note made (when
                                                is due)
    hub goal strategy-bot-decide "<reason>" '<changes json>' [--goal-id ID]
                                           the Strategy Bot at level 3: one decision alone on bot tasks, logged with
                                           Undo
    hub goal strategy-bot-log [--before CURSOR] the Strategy Bot log (the owner only): what it decided alone, proposed
                                                instead
    hub goal update <id> [--title ...] [--body ...|--body-file f] [--parent ID|--parent ""] [--owner X]
                    [--rank N|--top] [--bet ID|--bet ""]
    hub kpi list [--goal ID] [--owner me|X] [--unlinked] [--bot SLUG] [--archived]
                                           KPIs with latest reading and colour; --bot: that bot's five automatic KPIs
    hub kpi show <id> [--effective]        definition and versions, the goals using it, every reading, check-ins
    hub kpi archive <id> | restore <id>    hide from active lists, or restore; history stays
    hub kpi create "<name>" [--goal ID] [--definition "..."] [--unit %] [--direction up|down|range]
                [--cadence daily|weekly|monthly] [--owner me|X|company] [--source-note "..."] [target flags]
                                           a KPI of its own; with --goal it is linked, the target on the link
    hub kpi update <id> [--name ...] [--definition ...] [--unit ...] [--direction ...] [--cadence ...]
                    [--source-note ...] [--owner X] [--parent ID] [--role north_star|input|guardrail]
                    [--stage reach|acquire|activate|retain|refer] [--loop NAME:STEP]
                                           a change to what it measures is a new definition version
    hub kpi link <goal-id> <kpi-id> [target flags]   link a goal to a KPI, or change the target on the link
    hub kpi unlink <goal-id> <kpi-id>      take a KPI off a goal
        target flags: --baseline N --target N --deadline YYYY-MM-DD (improve; a deadline is required)
                      or --min N --max N (a range to stay in); none: just linked
    hub kpi log <kpi-id> <value> ["<note>"] [--period-start D] [--period-end D|--at D] [--collected-at T]
                [--evidence "url or note"] [--quality measured|estimate|partial|--estimate] [--source posthog]
                [--definition-version N] [--supersedes READING-ID]
                                           a reading: a fact with its period; to correct one, supersede it
    hub proposal create --kind goal_wording|goal_kpi|kpi_definition|kpi_target|flag|goal_why|recovery|strategy_draft|
                               brand_draft
                        [--goal ID] [--kpi ID]
                        (--payload '{json}'|--payload-file f.json) [--reason "..."]
                                           a change you may not make yourself, for the owner to confirm
    hub proposal list [--status pending|confirmed|rejected|all] [--goal ID] [--kpi ID]
    hub proposal decide <id> confirm|reject [--note "..."] [--option N]
                                           a person's own decision; a bot is refused. --option: a recovery's other option
    hub rules list [--kind internal|external] [--status active|suggested|retired|any] [--who N] [--with N]
                   [--source N] [--bot N] [--mine] [--cursor C] [--limit N]
                                           the org rules in force; --mine: your bot's rules and its settings as sentences
    hub rules read <id>                    one rule: its sentence, Who and With, From, due dates, the quote it cites
    hub rules read <file-id|url> --from F [--kind K] [--note "..."]   add a document: Counsel reads it and suggests rules
    hub rules ask "<question>"             can I...? Counsel answers with the rules that apply, cited
    hub rules suggested                    the suggestions waiting for a person
    hub rules add --kind internal|external --text "..." [--who N ...] [--everyone] [--all-bots] [--with N ...]
                  [--where thread:ID|team:GROUP|kind:dm|has_outside_members ...]
                  [--from SOURCE] [--description "..."] [--if-broken block|escalate|warn] [--reviewer P]
                  [--file FILE_ID --version N --section S --quote Q]   external: the words the rule rests on
    hub rules edit <id> --version N [--text ...] [--who N ...] [--everyone] [--all-bots] [--with N ...] [--from SOURCE]
                   [--where TERM ...]
    hub rules accept <id> --version N [--who N ...] [--everyone] [--all-bots]   a person; a bot is refused
    hub rules skip <id> --version N        set a suggestion aside
    hub rules retire <id> --version N      retire a rule in force; its due dates come back
    hub rules due [--next 90d] [--status overdue|due_soon|upcoming|satisfied] [--limit N]
                                           the due dates of rules in force (`hub rules due list` is the same)
    hub rules due add <rule-id> --first YYYY-MM-DD --repeats once|weekly|monthly|quarterly|yearly --owner N
                      [--timezone Z] [--task-days N]   --task-days: a routine opens the owner's task N days before
    hub rules due edit <id> [--first ...] [--repeats ...] [--owner N] [--timezone Z] [--task-days N | --no-routine]
    hub rules due satisfied <id> [--on YYYY-MM-DD]   a due date's or its rule's id; --on when two are open
    hub rules check "<draft>" --to <teammate> [--as message|comment|task|assign]
                                           a bot checks a draft against its rules; sending it then costs no second check
    hub rules appeal <hold-id> "<why>"     a bot asks the reviewer to look at its held output
    hub rules withdraw <hold-id>           take back your held write to edit it and send it again
    hub strategy show                      the published strategy, its bets with their goals and colour, the website
    hub strategy summary                   the text every run carries
    hub strategy versions [N]              published versions, newest first; N: that version whole
    hub strategy diff --from N --to N      what changed between two versions
    hub strategy guides                    deprecated: the Strategy Bot's drafting questions; the exercises are lenses
    hub strategy draft [--summary ...] [--vision ...] [--section KEY=TEXT ...] [--revision N]
    hub strategy publish [--note "..."] [--revision N]
    hub strategy bet add --statement "..." [--why ...] [--rules-out ...] [--status proposed|active] [--rank N]
                         [--wrong-if-kpi ID --wrong-if-direction above|below --wrong-if-threshold N |
                          --wrong-if-thesis MARKET-ID | --wrong-if-text "..." --check-by YYYY-MM-DD] [--cite ID ...]
    hub strategy bet update <id> [same flags]
    hub strategy bet retire <id>
                                           draft, publish and bets: the owner only; a bot proposes with
                                           proposal create --kind strategy_draft
    hub brand show [--section S] [--version N] | summary | check <file|->
                                           the brand; check: avoided words, wrong names, never-made claims in a draft
    hub brand draft --section S --body JSON [--revision N] | publish [--note "..."]
                                           owner or admin; a bot proposes with --kind brand_draft
    hub growth channel list [--retired] | show SLUG | add SLUG --name ... | update SLUG | retire SLUG
                                           a channel's open marketing tasks are labelled channel:<slug>
    hub growth source list [--retired] | add --truth-for ... --system ... --reach-kind ... | update ID | retire ID
    hub growth cohort list [--channel SLUG] [--month M] [--limit N] | set --channel SLUG|unknown --month YYYY-MM
                           [--spend N --bot-cost N --new N --d30 PCT --d90 PCT --d180 PCT --revenue N
                           --margin PCT --quality actual|estimate --source FIELD=TEXT]
    hub growth economics [--month M] | numbers [--months N] | kpis | settings [--set KEY=VALUE]
    hub lens list [--market]               the lenses: thought exercises run against the strategy or the market
    hub lens run <id> [--answers-file f.json] [--draft] [--no-wait]
                                           run one (the owner, the Strategy Bot; a market lens also the Librarian);
                                           waits up to 60 s and prints the run with its summary; --draft: the owner's
                                           draft
    hub lens show <run-id>                 one run: answers, result, changes
    hub lens runs [--lens ID]              runs, newest first, with their summaries
    hub approval request --kind send|spend|publish|merge|support|task_cap|spend_limit --payload-file f.json [--task ID] [--supersedes ID]
                                           if the owner already said send in Tico, skip this and
                                           `mail send --approve <their-message-id>`
    hub approval show <id>
    hub approval list [--status pending|approved|declined] [--from ACTOR] [--before DATE] [--since DATE] [--limit N] [--offset N]
                                           pending is what Needs you counts; the rows you may open, newest first
    hub approval read-back <id>... [--note "..."]
                                           the Assistant: the list it will approve, as its reply; the person's yes runs it
    hub approval approve <id> [--note "..."] [--on-behalf-of turn]
                                           a person approves one addressed to them (the Assistant: when they let it
                                           decide approvals and asked)
    hub approval decline <id> [--note "why"] [--on-behalf-of turn]
                                           a person declines one addressed to them (the Assistant: when they asked)
    hub listening save --file run.json     Listening: one sweep and the posts it saw (status ok|blocked|
                                           rate_limited|error; a post already saved is kept as it was)
    hub listening decide [--limit 20] [--item ID]
                                           put the listening-item decisions to new posts and route them to the message bots
    hub listening show <item-id>           a post, the run that saw it, its decisions, where it went
    hub listening runs [--since DATE] [--source x]
    hub listening stats [--since DATE]     coverage by source, precision by message bot
    hub listening item list [--destination D] [--status new]
                                           posts Listening routed to you, oldest first
    hub listening item resolve <id> --status accepted --ref <your record> | rejected --reason "..." |
                    duplicate --ref <existing>
    hub tool list [--bot X] [--json]       the team's tools: how to reach each, credentials, counts; --bot: what
                                           that bot uses (model, repository, each declared tool)
    hub tool show <service> [--json]       the page, its query index and the learnings (plain text)
    hub tool index [--cursor C] [--limit N] Tools: every API tool, mailbox, number and connection you may see,
                                           its status and who can use it (`hub tools index` too)
    hub tool check <id>                    check an API tool's keys now: Working, Key rejected or Check failed
    hub tool call <tool> <METHOD> <path> [--data JSON] [--query k=v ...]
                                           call a proxied API tool from a turn (Tico adds the real key)
    hub tool query-search <service> [term] [--id ID]   search a query catalog; --id prints the SQL and params
    hub tool learn <service> "<text>"      add a shared learning under a tool page
    hub tool add <bot> <service> --can read[,post] [--identity "..."] [--scope database=warehouse ...]
                    [--env VAR_NAME] [--note "..."]
                    [--mcp-url https://... [--transport http|sse] [--header "Authorization: Bearer ${VAR_NAME}" ...]]
                                           register a tool (with --mcp-url, a remote MCP server the bot's harness uses): BotOps gets a task with the entry; it shows pending
                                           until the computer reports it. A variable's name, never its value
    hub tool update <tool-id> --bot <bot> [--can read,draft,send] [--scope KEY=VALUE ...] [--note "..."]
                    [--mcp-url URL] [--transport http|sse] [--header "Name: value" ...]
                                           change a declared tool in place (BotOps: as the requester); never remove
                                           and add it again. `--scope KEY=` takes a key off, `--note ""` clears it
    hub tool remove <bot> <tool-id>        ask BotOps to remove one (or withdraw a pending request)
    hub automation list [--owner O]        automations you may see, each with can_edit
    hub automation show <id>               trigger, steps, version, today's counts, next run
    hub automation create --file F         a new one from a JSON flow ({name, owner, trigger, steps}); - reads stdin
    hub automation update <id> [--file F] [--name N] [--enable|--disable] [--summary S]
                                           new trigger/steps/batch = a new version; a bot without rights proposes one
                                           (BotOps: the asker's rights; what it creates is off unless enabled is sent)
    hub automation delete <id>
    hub automation run <id> [--input JSON] run now with a sample trigger output; prints the run
    hub automation runs <id> [--limit N] [--before RUN]
    hub automation held <id> release|drop  end a run-guard hold: queue its held runs, or skip them
    hub automation recent-runs [--range 5m|1h|24h|7d] [--status S] [--trigger T] [--owner O] [--limit N] [--before B]
                                           runs across every automation you may see, newest first
    hub automation stats [--range 5m|1h|24h|7d]
                                           runs, failures and cost per automation over the range
    hub automation versions <id> [--pending]
    hub automation restore <id> <n>        make version n current (a pending one keeps its number)
    hub automation discard <id> <n>        drop a pending version (a proposal); the automation stays as it is
    hub automation question-sets           the question sets a Decide step may name (set: id@version)
    hub automation guide                   how BotOps builds automations, with the flow format
    hub automation templates               the inbox templates: founder, support, sales, blank
    hub automation template <id> --owner <ref> --inbox <inbox>
                                           one filled in for that owner and inbox; saves nothing
    hub audit [--object O] [--limit N] [--before B]
                                           who changed caps, spend, groups, automations and inboxes; the owner sees all
    hub automation inbox <inbox> [--send on|off]
                                           an inbox's send switch (email, slack:dm, slack:#chan); off = drafts only
    hub trigger list                       webhooks people and bots made; an automation runs on one
    hub trigger create <name> [--id X]     make one; the address (with its secret) is shown once
    hub trigger show <id>                  address, settings, 24-hour counts, automations
    hub trigger update <id> [--name N] [--preset none|hmac|github|standard|stripe|slack] [--type-from H|/pointer]
                    [--hourly 1-600] [--keep-days 1-30] [--unheard log|note]
    hub trigger rotate <id>                a new secret; the old address stops
    hub trigger sign <id> [--preset P] [--secret S] [--off]
                                           verify each call (preset default hmac); the secret is shown once
    hub trigger delete <id>
    hub trigger deliveries <id>            the latest calls: delivery id, type, outcome, stored
    hub trigger delivery <id> <delivery> [--part N]
                                           one stored call, headers redacted; long bodies read in parts
    hub trigger replay <id> <delivery>     run a stored call again as a new occurrence
    hub trigger recover <id> [--since HOURS]
                                           run again the calls refused in the last hours (a fixed secret) whose body is kept
    hub event-types [--group task]         every event Tico names, with an example payload (automations, wake rules, webhooks)
    hub trigger relay create|rotate|delete <trigger>
                                           a second address through Tico Gateway (shown once); hub relay status
    hub relay set <trigger> --wake on|off  whether a delivery to that address starts a sleeping server
    hub relay wake-url show|off|set <url>  the https address Tico Gateway knocks on to start a server you run when a webhook
                                           waits for it; the owner (set shows the signing secret once)
    hub webhook list|create|show|update|rotate|pause|resume|delete|send|sends|retry|example
    hub webhook recover|replay <id> --since DATE|enable <id>
                                           send again what gave up / what was never sent since a date; turn an endpoint back on
                                           outgoing webhooks: endpoints Tico posts events to (hub webhook create --help)
    hub sleep status                       stop this hosted server after quiet hours; show the setting
    hub sleep set --hours N [--runners on|off]
                                           1-24 quiet hours, 0 = off; bot computers stop with it unless --runners off
    hub notice list                        notices from Tico Team, newest first (tico.notice in rules)
    hub settings find "<words>"            a control in Settings by its words: key, place, who may change it, class
    hub settings show <key> [--bot B|--computer C|--person P|--id I]
                                           its value, who may change it and the link to its card
    hub settings set <key> <value> [--bot B|--computer C|--person P|--id I] [--on-behalf-of turn]
                                           change it as yourself (BotOps, the Assistant: as the person who asked; a
                                           confirm one is their Confirm card, a person-only one says why and links the
                                           card; the Assistant cites their words for a direct one)
    hub settings ask <key> <value> [--to P] [--note "..."] [--bot B|...] [--on-behalf-of turn]
                                           one you may not change: on your yes, its Confirm card and a request go to
                                           one person who may (an admin, or the owner for the owner's); you're told
                                           when they confirm or say no
    hub release now [--note "..."]         ask the Release Manager to release what is at Pending Release now
    hub release schedule [--on|--off] [--time HH:MM ...] [--manager BOT] [--writer BOT]
                                           show or change the daily release (company time; an owner or admin)
    hub bot status set "<focus>" [--state waiting_human|blocked|...] [--task ID] [--bot X]
    hub bot status list [--team marketing]
    hub bot status history <bot> [--since 7d]
    hub bot recent [--days N] [--limit N]  the bots you have been working with lately and where each stands
    hub run list <bot> [--since 24h]       a bot's recent runs
    hub team budget [--hour N] [--day N]    the team default runs per hour and per day (owner and admins)
    hub team show [--person ID] [--team NAME]
                                           humans and bots: who they are, Slack, what they own; each bot
                                           with its reports_to, group and template (the bots you may see)
    hub seat list                          Recruiting seats: roles on the chart with no bot yet; they hold no work
    hub seat create <title> [--description D] [--reports-to R] [--team G]
                                           add a seat under you or someone below you; update <seat> / close <seat>
    hub org order <manager> [--set a,b,c]  a manager's direct reports in the chart's order; --set puts them in
                                           that order (the ones left out keep their places)
    hub org reports-to <bot:slug|human:id> <manager> [--on-behalf-of turn]
                                           change who a teammate reports to, as the person who asked
    hub health check                       what is wrong with the bots, most urgent first, each with its fix
    hub health metrics [--minutes N]       the server's flight recorder (owners, admins and bots)
                                           (the Assistant gets the live snapshot)
    hub update list [--kind daily|weekly] [--bot X] [--scope all|direct] [--unread] [--limit N]
                                           the bots' updates, newest first
    hub update show <id>                   one update with its thread
    hub update create "<- bullets>" [--kind daily|weekly] [--learnings-saved N] [--suggestion TEXT ...] [--delivery-batch ID]
                                           post your own update when Tico asks (three bullets)
    hub update create --skip               nothing real to report: posts nothing, the request closes as skipped
    hub update create --slides-file week.json [--day D]   a week in review: {goal, kpis, done, focus, blockers}
    hub update redo --day D [--kind weekly|daily] [--bot B ...] [--with-managers]   ask the bots to redo a past day (owner)
    hub update mark-read [ids...] [--all] [--unread]
    hub update reply <id> "<text>"
    hub update settings <bot> [--daily on|off] [--weekly on|off]
    hub changelog list [--unread] [--since-version V] [--q TEXT] [--kind product|activity|all]
    hub changelog mark-read <entry-id> [entry-id ...]   acknowledge changes shown to you
    hub brief [--since ISO]                alerts, who needs the person, what bots said since --since
    hub mcp stats [--days N] [--via LABEL] per-tool timing and answer size of assistants' calls
    hub needs-you start|next|respond|commit|abandon   a person's walk through what needs them (backend/batch.py)
    hub needs-you show [--by bot|kind] [--limit N]    what needs you, counted as the board counts it, per kind and
                                                      per bot, with each bot's newest items
    hub calendar list [--calendar EMAIL]
                                           appointments visible on a company calendar
    hub calendar schedule --title "..." --start ISO --end ISO [--calendar EMAIL]
                    [--attendee EMAIL ...] [--description "..."] [--no-meet]
                                           queue one idempotent calendar invitation
    hub calendar status <action-id>        whether the provider confirmed the event
    hub sql "<select>" [--json|--csv] [--max-rows N] [--param key=value ...]
                                           read-only SQL, what you may see (docs/hub-sql.md)
    hub db list | doctor [name]            the company databases this bot may read, and a health check
    hub db <name> "<select>" [--param k=v ...] [--json|--csv] [--max-rows N] [--timeout S]
    hub db <mongo> find <collection> ['<filter>'] [--projection J] [--sort J] [--limit N] | aggregate <collection> '<pipeline>'
                   | count <collection> ['<filter>'] | distinct <collection> <field> | collections
    hub db <name> --query <id> [--param k=v ...]
                                           read-only SQL on a company database, run on this computer
                                           with its credential; every query is audited (docs/databases.md)
    hub classify [--file F]                spam / injection check on outside text from stdin or F: {verdict, reason}
                                           (legit|spam|injection_risk|unchecked); the message bots and Support gate on it
    hub decision ask --set <name> --state-file s.json [--option covered=opts.json] [--label L]
                                           ask the decision model typed questions about a state (questions/README.md);
                                           --questions-file q.json instead of --set; --list shows the sets
    hub decision outcome --ref R --verdict V --action A [--label L]
                                           after acting on a decision ask or hub classify answer (its `ref`):
                                           what you did with it, once per call
    hub gateway catalog [QUERY]            the Tico AI Gateway's lineup: models, decision levels, services (skills/gateway)
    hub gateway search QUERY [--max 5]     web search through the Gateway: results, answer, cost
    hub gateway fetch URL [--max-tokens N] read a web page or PDF through the Gateway: title, content, cost
    hub gateway image PROMPT --category C [--ref FILE]... [--size WxH] [--count N] --out PATH
                                           make an image (storyboard, photo, character or graphic) and save it;
                                           PATH.json beside it says how it was made
    hub decision readout [--days 7]        per question set: calls, cost, Jev or fallback, answer spread; and the
                                           month's spend against the cap (`text` is the readout in plain words)
    hub template list                      the bot templates this company can pick from
    hub bot create --template T [--name "Display"] [<slug>]
                                           set a chosen bot up in the workspace (BotOps only); the slug is picked
                                           from the name (else the template's) and printed. It also registers
                                           the bot with Tico as the requester when a person's message started the run,
                                           and asks for its empty <org>/bot-<slug> when GitHub is connected (exits
                                           non-zero when a person must first let BotOps create repositories)
    hub bot create --record-only --name N [<slug>] [--description D] [--reports-to R] [--template T]
                                           only register a new bot with Tico (planned), as the requester (BotOps)
    hub bot check <slug>                   what preflight would still refuse about that repository
    hub bot check-names <slug|folder> [--fix]
                                           old column, field and command names left in a bot's files; --fix rewrites
                                           the ones with one new spelling (runs here: no server)
    hub bot update <slug> [--new-slug S] [--reports-to R] [--display-name N] [--description D] [--status S]
                                           apply a person's bot-settings request as them (BotOps)
    hub bot access <slug> [--see V] [--read V] [--write V]
                                           show or set who sees, reads, writes (V: everyone, or ben,group:legal,bot:x)
    hub bot owners <slug> [--add P ...] [--remove P ...]
                                           add or remove the humans who own a bot, as the requester (BotOps)
    hub bot mail <slug> [--send|--no-send]
                                           show or set whether the bot sends mail without approval and its forward
                                           targets; a person who manages the bot only, never a bot (docs/mail.md)
    hub bot setup-done [slug]              mark a bot's onboarding complete: a person above it on the org chart
    hub bot ready --summary S              an onboarding bot says it is ready, with what it learned
    hub bot place <bot> [--computer <label|id>]
                                           put a bot on a computer: the one named, or the best one that takes it
    hub bot go-live <bot> [--computer C] [--no-setup]
                                           place it if needed, turn it on, start its setup; waits for the computer
                                           to get its repository and finishes by itself (`--help`: the file's shape)
    hub bot model <bot> [<model>] [--effort E]
                                           list the models, or change the bot's
    hub bot task-model <bot> [<model>] [--effort E] [--harness H] [--clear]
                                           the model the bot's tasks run on; --clear puts them back on its own
    hub bot pause|resume <bot>             stop or restart a bot (resume places one that has no computer)
    hub bot budget <bot> [--hour N] [--day N] | release
                                           read or set a bot's runs per hour and per day (0 no limit, default follows the
                                           team), or release it after Tico stopped it
    hub bot release <bot>                  release a bot Tico stopped (failed runs, budget, or runs it could not start)
    hub bot profile <bot> [doer|reviewer|lead|manager|responder|monitor|custom] [--batch-seconds N] [--digest-hour H]
                                           read or set what wakes a bot and which tasks it takes (--sweeps/--pulls/--takes
                                           are for custom; --confirm-drop-rules when leaving custom deletes its own rules)
    hub bot scope <bot> <task>|--release   limit a bot's claims to one task and its subtasks, or lift that
    hub bot copy <bot> [--slug S] [--name N] [--with-memory] [--computer C]
                                           copy a bot into a new one the requester owns: its instructions, skills, playbooks and
                                           tools, never a secret; notes and memory only with --with-memory (BotOps)
    hub bot update-from-original <bot> [--resolved]
                                           bring a copy's instructions up to date with its original (three-way merge, one commit)
    hub bot suggest-to-original <bot> [--paths P ...] [--title T]
                                           a pull request (or a task with the diff) for the original from the copy's changes
    hub skill copy <skill> --from <bot> --to <bot> [<bot> ...] [--replace]
                                           copy skills/<skill>/ into other bots' repositories, a commit in each
    hub skill submit <skill> [--path DIR] [--note TEXT] [--base N]
                                           propose a skill folder to the company's Skills directory; an admin approves it (docs/skills.md)
    hub bot repo-create <slug> [--template OWNER/REPO | --empty]
                                           owner or BotOps: create <org>/bot-<slug> from a template (docs/github-app.md)
    hub human add <email> [--name N] [--title T] [--reports-to P]
                                           add a human to the roster and sign-in list (a Confirm card first, unless they are in the team's domain)
    hub human list                         the humans on the roster
    hub human access <human> [--see V] [--read V] [--write V]
                                           show or set who sees, reads (their Home), writes (DMs) to a person
    hub group list                         the groups (they nest), with their humans and bots
    hub group update [<group>] [--name N] [--parent G|''] [--lead bot:B|person:P|''] [--add-human H ...]
                     [--add-bot B ...] [--remove-human H ...] [--remove-bot B ...] [--on-behalf-of turn]
                                           an owner or admin (a lead in its groups), or BotOps / the Strategy Bot as
                                           the person who asked: with no <group>, create one from --name; else
                                           rename, move, lead or fill it. A teammate is in one group at a time
    hub group delete <group> [--on-behalf-of turn]
                                           delete a group; its teammates and groups move up to its parent
    hub group access <group> [--see V] [--read V] [--write V]
                                           show or set who sees, reads, writes to a group (a lead or an admin)
    hub api GET|POST|PUT|PATCH|DELETE <path> ['{json}']
                                           BotOps: any v2 route, as the person who asked; a Confirm card for what
                                           always needs their click. Never a secret in the body
    hub computer list [--computer C]       the computers a bot may go on, one short row each; one in full
    hub computer performance <label|id> [--hours 24]
                                           its CPU, memory and disk over time, overloads and what ran then
    hub credential request <ENV> [--for-bot B] [--label "your Jira login"] [--format "you@x.com:API token"]
                    [--help-url https://...] [--kind api_key|token|password]
                                           open a card in the chat for the person to type the secret into
    hub credential set <ENV> --for-bot B [--name N] [--kind K] [--username U] [--no-redact]
                                           store a secret a person gave you (read from stdin, never the command line)
    hub credential list                    names, variables and which bots have each; never a value
    hub credential grant <name> --to <bot>       give a bot a stored credential (a credential admin; a bot has only what is granted)
    hub credential revoke <name> --from <bot>    take it away again
    hub credential import <VAR> --from-bot <bot> [--name N] [--wait S]
                                           move one variable from that bot's own secrets file into Credentials, granted to
                                           that bot: its computer sends the value itself, it is never shown
    hub slack channel list                 the Slack channels bots may read and post in, who reads each
    hub slack channel add <channel> [--reader BOT ...] [--post|--no-post] [--note "..."] [--digest-hours N]
                                           list a channel (#name or id) or add readers to one already listed; an owner
                                           or an admin (BotOps: as the requester). Posting is on unless --no-post
    hub slack channel remove <channel> [--reader BOT]   take the channel off the list, or only that reader off it
    hub slack channel import               store the channels of the old registry/slack-channels.yaml, once
    hub support file "<message>"           tell the Tico team about a gap or fault (a Confirm card first)
    hub support report --message "..." | --file report.md [--no-ids]
                                           send a Tico bug report to the Tico team: at once when the team lets bots send
                                           without asking, else drafted for a person to approve in Needs you (the
                                           version and install ID go with it unless --no-ids). Prints its id or ticket
    hub support send --approval <id>       send the report once a person approved it, filed as them; again is the
                                           same ticket, never a second one
    hub health findings                    what Check health found on this install, each problem with its report
    hub health report <key>                report a finding as a Tico bug, one ticket per problem
    hub finance search [--kind entry|feed|bill|invoice|contact|rule|card|account] [--q words] [--status S] [--vendor V]
                    [--start D] [--end D] [--cursor C] [--limit N]   the books you may see; name a kind to page
    hub finance report <pnl|balance_sheet|cash_flow|budget_vs_actual|ap_aging|ar_aging|spend_by|close|sales_tax|1099>
                    [--start D] [--end D] [--period month] [--compare prior] [--top-n N] [--by vendor] [--month YYYY-MM]
                    [--year YYYY]
    hub finance metric <name> [--budget-id B] [--start D] [--end D] [--days N]   one number, with its as-of time
    hub finance act <kind|help> [--payload JSON|-] [--dry-run] [--operation-id KEY]
                                           ask for one Finance action; help lists kinds, `help --payload
                                           '{"kind":"bill_create"}'` one kind's fields. The key makes a retry land once
    hub finance action <action-id>         an action's status and result
    hub finance inbox [--kind K] [--cursor C] [--limit N]   your Finance work queue, with counts per kind; --kind pages one kind
    hub finance card-fill                  fill your card into a checkout (not available yet)
    hub task-manager status                the Task Manager: bot, hourly report switch and schedule, last run, each department
    hub task-manager on|off                the hourly department report (the owner or an admin)
    hub task-manager schedule <business|hourly|4h|daily> [--at HH:MM]
                                           when the report runs (business = hourly, weekdays 08:00 to 18:00)
    hub task-manager run                   run the department check now
    hub task-manager bot on|off            turn the Task Manager bot itself on or off (the owner)
    hub maintenance status                 schedule, pause, monthly judge cap, and each step's switch, frequency and last run
    hub maintenance set [flags]            change them (owner or admin): --step KEY with --on/--off, --frequency,
                                           --every, --unit or --max-bots for a step; --schedule, --pause/--resume, --cap
    hub maintenance run [--step KEY]       run now, all steps or one (owner or admin)
    hub maintenance runs [--limit N]       recent runs and each step's status
    hub service-key create --label "Billing backend"
                                           the owner or an admin: a key another system uses to file, update and close
                                           tasks (POST /api/v2/inbound/tasks, docs/service-keys.md); shown once
    hub service-key create --label "Release Manager" --scope update
                                           the owner: a key a release bot on another install uses to check for,
                                           start and follow an update of this one (/api/v2/system/update), and nothing else
    hub service-key list | revoke <id>     every service key, never its secret; stop one at once
    hub grokbot sync --file f.json         sync your Grok Bots into Tico
    hub external sync --provider dots --file f.json   sync your Grok Bots or Dots into Tico

The commands of the last release keep working for one more release, hidden: each prints a one-line "renamed to"
notice on stderr and runs the new command (RENAMED below). A command that is gone (REMOVED below) prints one
line naming what to use instead and exits 1.

Exit codes: 0 fine, 2 `{"refused": <rule>, "detail": "..."}` or a non-retryable API error,
1 `{"error": "..."}` (bad arguments, no identity, or the API could not be reached).

Identity (§3): a bot is `HUB_BOT` + `HUB_TOKEN` from the run's environment (`HUB_EMPLOYEE` is the old name for
`HUB_BOT`, still read); the runner sets both, with `HUB_API_URL` and `HUB_WORKSPACE` (where this company's bot
repositories live), for every run. There is no `--as`. `--human <id>` is still
parsed so an old command line is not misread, and the API refuses it: remote identity comes
from authentication. Outside a run, `hub sql` and the tool reads (`tool list`, `tool show`,
`tool query-search`) fall back to this Mac's runner credential (`~/.config/tico/runner.json`); `sql`
queries as the person who registered the computer. A person's own script sets `HUB_API_URL` and a personal
API token as `HUB_TOKEN` (Settings, Computers, API tokens) and no `HUB_BOT`: every command is
then that person (docs/how-it-works.md, "Calling the API from a script").
"""

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script: python clients/hubcli.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clients.hubcli_core import (  # noqa: F401
    TASK_STATUSES,
    TASK_UPDATE_STATUSES,
    APPROVAL_KINDS,
    GOAL_STATUSES,
    ASKED,
    CliError,
    task_update_status,
    GO_LIVE_HELP,
    body_of,
    DEFER_FOR,
    defer_for,
    delivery_flags,
    quiet_hours,
    on_off,
    _finance_payload,
    thread_page_flags,
)


def out(obj):
    print(json.dumps(obj, indent=2, default=str, sort_keys=False))
    return 0


from clients.hubcli_dryrun import (  # noqa: F401
    words_outside_quotes,
    owner_from_registry,
    task_problems,
    cmd_task_dry_run,
)


def delivery_of(args):
    """{delivery, defer_for} for a request body from delivery_flags' arguments; {} when none was given."""
    if getattr(args, "defer_for", None) is not None:
        return {"delivery": "defer", **({"defer_for": args.defer_for} if args.defer_for else {})}
    return {"delivery": args.delivery} if getattr(args, "delivery", None) else {}


def server_release(timeout=3):
    """The release the hub server reports on its unauthenticated /healthz, or "" when it cannot say (no hub set, or it
    does not answer within `timeout` seconds). A turn's runner strips TICO_* from the environment, so a released CLI
    has no version of its own and asks the server it talks to."""
    api = os.environ.get("HUB_API_URL", "")
    if not api:
        return ""
    origin = api.split("/api/", 1)[0].rstrip("/")
    try:
        from clients import gateway

        with gateway.branded_opener().open(origin + "/healthz", timeout=timeout) as reply:
            return str(json.loads(reply.read().decode()).get("release") or "")
    except Exception:
        return ""


class _Version(argparse.Action):
    """`hub --version`: the Tico version this CLI was stamped with, else the release of the server it talks to."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings, dest, nargs=0, default=default, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        from clients.hubtools import release_version

        version = release_version()
        if version == "dev":
            version = server_release() or "unknown (this CLI build is not stamped)"
        print(version)
        parser.exit()


def command_paths(p, prefix=()):
    """Every command under `p` as a tuple of words, e.g. ('task', 'list'). Only commands that parse are listed."""
    paths = []
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            seen = set()
            for name, sub in action.choices.items():
                if id(sub) in seen:  # an alias: the same command as the name before it
                    continue
                seen.add(id(sub))
                paths.extend(command_paths(sub, prefix + (name,)) or [prefix + (name,)])
    return paths


def did_you_mean(words, names):
    """A one-line hint naming the closest of `names` (commands that exist), or "" when none is close."""
    close = difflib.get_close_matches(" ".join(words), names, n=2, cutoff=0.5)
    return f" Did you mean: {', '.join('hub ' + name for name in close)}?" if close else ""


def typed_words(argv):
    """The command words on a line: everything before the first flag."""
    words = []
    for a in argv:
        if a.startswith("-"):
            break
        words.append(a)
    return words


def unknown_flag_hint(p, argv, unknown):
    """The closest flag of the command the line names, for a flag it does not take (`--where-status` is `--status`)."""
    node, words = p, []
    for a in argv:
        sub = next((act for act in node._actions if isinstance(act, argparse._SubParsersAction)), None)
        if a.startswith("-") or not sub or a not in sub.choices:
            continue
        node = sub.choices[a]
        words.append(a)
    flags = []
    for flag in unknown:
        if flag.startswith("--"):
            flags += difflib.get_close_matches(flag, list(node._option_string_actions), n=1, cutoff=0.6)
    if flags:
        return f" Did you mean {', '.join(flags)}?"
    takes = [flag for flag in node._option_string_actions if flag.startswith("--") and flag != "--help"]
    if takes and words:
        return f" It takes: {', '.join(sorted(takes))}"
    return did_you_mean(words, [" ".join(path) for path in command_paths(p)]) if words else ""


def example_line(p):
    """`hub task show <id>`: the command's name with a placeholder for each positional it takes."""
    parts = [p.prog]
    for a in p._actions:
        if a.option_strings or isinstance(a, argparse._SubParsersAction):
            continue
        if a.choices:
            parts.append("{" + "|".join(str(c) for c in a.choices) + "}")
        else:
            parts.append(f"<{a.metavar if isinstance(a.metavar, str) else a.dest}>")
    return " ".join(parts)


class HubParser(argparse.ArgumentParser):
    """The parser of the root and of every command: argparse builds each subparser from this class, so one error
    method covers all of them. Each error says what would have worked: the closest command, the closest flag, or the
    example of a command that was missing its id."""

    def error(self, message):
        if message.startswith("the following arguments are required"):
            self.print_usage(sys.stderr)
            print(f"Example: {example_line(self)}", file=sys.stderr)
            self.exit(2, f"{self.prog}: error: {message}\n")
        hint = ""
        argv = getattr(self, "hub_argv", None)
        if argv is not None and message.startswith("argument cmd: invalid choice"):
            names = [" ".join(path) for path in command_paths(self)]
            hint = did_you_mean(typed_words(argv), names)
        elif argv is not None and message.startswith("unrecognized arguments: "):
            hint = unknown_flag_hint(self, argv, message[len("unrecognized arguments: ") :].split())
        elif "invalid choice" in message and self.prog != "hub":  # a bad word under a command, e.g. `task lst`
            bad = message.split("invalid choice: '", 1)[-1].split("'", 1)[0]
            under = self.prog.split()[1:]
            names = [" ".join(under + list(path)) for path in command_paths(self)]
            hint = did_you_mean(under + [bad], names)
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}{hint}\n")


from clients.hubcli_inbox import add_inbox
from clients.hubcli_messages import add_messages
from clients.hubcli_content import add_content
from clients.hubcli_tasks import add_tasks
from clients.hubcli_goals import add_goals
from clients.hubcli_strategy import add_strategy
from clients.hubcli_market import add_market
from clients.hubcli_automation import add_automation
from clients.hubcli_runs import add_runs
from clients.hubcli_company import add_company
from clients.hubcli_agents import add_agents
from clients.hubcli_platform import add_platform


def parser():
    p = HubParser(prog="hub", description="the company hub: messages, tasks, status")
    p.add_argument("--version", action=_Version, help="print the Tico version")
    sub = p.add_subparsers(dest="cmd")

    add_inbox(sub)
    add_messages(sub)
    docs = add_content(sub)
    add_tasks(sub)
    add_goals(sub)
    add_strategy(sub)
    add_market(sub)
    add_automation(sub)
    add_runs(sub)
    add_company(sub)
    add_agents(sub, docs)
    add_platform(sub)

    return p


RUNNER_CONFIG = Path.home() / ".config" / "tico" / "runner.json"
# Read-only commands a person or a script on the Mac may run outside a turn with the runner credential.
# The commands that work in this computer's workspace of bot repositories.
WORKSPACE_COMMANDS = (
    "bot check",
    "bot copy",
    "bot update-from-original",
    "bot suggest-to-original",
    "skill copy",
    "skill submit",
)
READ_OUTSIDE_A_TURN = ("sql", "tool list", "tool show", "tool query-search")


# ----------------------------------------------------------------------------- the last release's spellings
def _goal_auto(rest):  # hub goal auto <id>  ->  hub goal status <id> auto
    return rest[:1] + ["auto"] + rest[1:]


def _context_search(rest):  # --source all|docs|market  ->  --market (docs are always searched)
    out, market, i = [], True, 0
    while i < len(rest):
        if rest[i] == "--source" and i + 1 < len(rest):
            market, i = rest[i + 1] != "docs", i + 2
        elif rest[i].startswith("--source="):
            market, i = rest[i].split("=", 1)[1] != "docs", i + 1
        else:
            out.append(rest[i])
            i += 1
    return out + (["--market"] if market else [])


def _gtm_how(rest):
    """`hub gtm show|summary` is the How we sell section now: the old page's flags are gone, `--json` stays."""
    return [a for a in rest if a == "--json"]


# (old words, new words, words added at the end, rewrite of what follows). The old spellings are not in the parser, so
# they are not in `hub --help`; `rename_argv` maps a command line onto the new one and says so on stderr. They are removed
# one release after the rename. The longest old spelling that matches wins.
RENAMED = [
    (("say",), ("message", "send"), (), None),
    (("notice",), ("message", "send"), ("--fyi",), None),
    (("inbox",), ("message", "list"), (), None),
    (("ack",), ("message", "mark-read"), (), None),
    (("history",), ("conversation", "show"), (), None),
    (("notes",), ("note", "list"), (), None),
    (("unnote",), ("note", "delete"), (), None),
    (("board",), ("task", "list"), ("--all",), None),
    (("task", "stuck"), ("task", "list"), ("--stuck",), None),
    (("goals",), ("goal", "list"), (), None),
    (("goal", "auto"), ("goal", "status"), (), _goal_auto),
    (("goal", "checkins"), ("goal", "checkin-list"), (), None),
    (("goal", "checkin", "list"), ("goal", "checkin-list"), (), None),
    (("kpi", "add"), ("kpi", "create"), (), None),
    (("kpi", "readings"), ("kpi", "show"), (), None),
    (("context", "search"), ("doc", "search"), (), _context_search),
    (("context", "show"), ("doc", "read"), (), None),
    (("docs", "links"), ("doc", "link-list"), (), None),
    (("docs",), ("doc",), (), None),
    (("files", "add-link"), ("file", "link"), (), None),
    (("files",), ("file",), (), None),
    (("meetings", "transcript"), ("meeting", "read"), (), None),
    (("meetings",), ("meeting",), (), None),
    (("listen", "judge"), ("listening", "decide"), (), None),
    (("listen",), ("listening",), (), None),
    (("intake",), ("listening", "item"), (), None),
    (("tools",), ("tool",), (), None),
    (("integrations",), ("tool", "list"), (), None),
    (("integration",), ("tool", "show"), (), None),
    (("queries",), ("tool", "query-search"), (), None),
    (("learn",), ("tool", "learn"), (), None),
    (("status",), ("bot", "status"), (), None),
    (("recent",), ("bot", "recent"), (), None),
    (("turns",), ("run", "list"), (), None),
    (("org",), ("team", "show"), (), None),
    (("fleet",), ("health", "check"), (), None),
    (("fleet-check",), ("health", "check"), (), None),
    (("computers",), ("computer", "list"), (), None),
    (("catalog",), ("template", "list"), (), None),
    (("bot", "register"), ("bot", "create"), ("--record-only",), None),
    (("bot", "set"), ("bot", "update"), (), None),
    (("bot", "onboarded"), ("bot", "setup-done"), (), None),
    (("github", "create-bot-repo"), ("bot", "repo-create"), (), None),
    (("people",), ("human",), (), None),
    (("person",), ("human",), (), None),
    (("update", "post"), ("update", "create"), (), None),
    (("update", "read"), ("update", "mark-read"), (), None),
    (("updates",), ("update", "list"), (), None),
    (("batch",), ("needs-you",), (), None),
    (("live", "brief"), ("brief",), (), None),
    (("live", "stats"), ("mcp", "stats"), (), None),
    (("calendar", "upcoming"), ("calendar", "list"), (), None),
    (("decisions",), ("decision", "ask"), (), None),
    (("judge",), ("decision", "ask"), (), None),
    (("gtm", "show"), ("strategy", "show", "--section", "how"), (), _gtm_how),
    (("gtm", "summary"), ("strategy", "show", "--section", "how"), (), _gtm_how),
    (("gtm", "sources"), ("growth", "source", "list"), (), None),
]
RENAMED.sort(key=lambda rule: -len(rule[0]))

# Commands taken away with asks (Tico 0.5): each prints one line naming what to use instead and exits 1. The longest
# old spelling that matches wins.
ASK_INSTEAD = (
    'ask a person with: hub task question "<question>" --on <person>; '
    'ask a bot with: hub message send <bot> "<question>" --needs-action'
)
ANSWER_INSTEAD = "a question to a person is a question task: answer it in Tico (Needs you); a bot's question is a DM"
REMOVED = [
    (("question", "ask"), ASK_INSTEAD),
    (("question", "answer"), ANSWER_INSTEAD),
    (("question",), ASK_INSTEAD),
    (("ask",), ASK_INSTEAD),
    (("answer",), ANSWER_INSTEAD),
    (("task", "ask"), ASK_INSTEAD),
    (("task", "ask-resolve"), ANSWER_INSTEAD),
]
REMOVED.sort(key=lambda rule: -len(rule[0]))
NOTICE_COMMANDS = ("list",)  # the subcommands `hub notice` has now


MARKETING_TYPE = (
    "idea"  # the marketing task type's id (backend/marketing_tasks.py TYPE_ID); its name is a team's to set
)
# The old status words of the growth idea lines, and the step each one is now (backend/marketing_tasks.py).
LEGACY_IDEA_STEP = {"idea": "On deck", "testing": "Live", "scaling": "Working", "won": "Succeeded", "lost": "Failed"}


def legacy_idea_step(word):
    """The step a legacy status word names: the new step name for an old one, else the word capitalized (Paused)."""
    return LEGACY_IDEA_STEP.get(word.lower(), word.capitalize())


LEGACY_IDEA_FLAGS = (
    "--title",
    "--hypothesis",
    "--prediction",
    "--bet",
    "--kpi",
    "--segment",
    "--source-kind",
    "--source-ref",
    "--i",
    "--c",
    "--e",
    "--follow-up-of",
    "--assignee",
    "--body",
    "--status",
    "--q",
    "--limit",
    "--result",
    "--learning",
    "--bet-effect",
    "--confidence",
)


def legacy_idea(argv):
    """The old `hub growth idea create|list|verdict` lines as the task commands they are now, run in order, with the
    notice to print; None for any other line. Ideas are tasks of the idea type (id, not name: a team's own Marketing type
    does not change where they go): a channel is its label, a verdict moves the task to its step with what it found as
    the note. The ICE scores, segments and the idea's source have no task field and are read and dropped; the bet and
    the KPI stay as lines in the body."""
    if argv[:2] != ["growth", "idea"] or argv[2:3] not in (["create"], ["list"], ["verdict"]):
        return None
    p = argparse.ArgumentParser(prog="hub growth idea", allow_abbrev=False)
    p.add_argument("verb")
    p.add_argument("id", nargs="?")
    p.add_argument("--channel", action="append")
    for flag in LEGACY_IDEA_FLAGS:
        p.add_argument(flag)
    old, _ = p.parse_known_args(argv[2:])
    json_out = ["--json"] if "--json" in argv else []
    if old.verb == "create":
        # A task needs a body, so an idea given only a title uses the title as its body.
        body = (
            "\n".join(
                line
                for line in (
                    f"Hypothesis: {old.hypothesis}" if old.hypothesis else "",
                    f"Prediction: {old.prediction}" if old.prediction else "",
                    f"Bet: {old.bet}" if old.bet else "",
                    f"KPI: {old.kpi}" if old.kpi else "",
                    old.body or "",
                )
                if line
            )
            or old.title
            or ""
        )
        line = [
            "task",
            "create",
            "--type",
            MARKETING_TYPE,
            "--owner",
            old.assignee or "me",
            "--title",
            old.title or "",
            "--body",
            body,
        ]
        for channel in old.channel or []:
            line += ["--label", f"channel:{channel}"]
        return f'"hub growth idea create" is now "hub task create --type {MARKETING_TYPE}"', [line + json_out]
    if old.verb == "list":
        line = ["task", "list", "--type", MARKETING_TYPE]
        if old.status:
            line += ["--step", legacy_idea_step(old.status)]
        if old.channel:
            line += ["--label", f"channel:{old.channel[0]}"]
        return f'"hub growth idea list" is now "hub task list --type {MARKETING_TYPE}"', [line + json_out]
    if not old.status:
        p.error("verdict needs --status won|lost|inconclusive|dropped|paused")
    if not old.id:
        p.error("verdict needs the idea's task id")
    update = ["task", "update", old.id, "--step", legacy_idea_step(old.status)]
    found = "\n".join(
        f"{label}: {value}" for label, value in (("Result", old.result), ("Learning", old.learning)) if value
    )
    if not found and old.status == "paused":
        found = "Test paused"  # a pause needs a note; with no result or learning, the note says only that
    if found:
        update += ["--note", found]  # the verdict's words are the note of every outcome
    return ('"hub growth idea verdict" is now "hub task update --step --note"', [update + json_out])


def parse(p, argv):
    """`p.parse_args(argv)`, except that a `--json` no command on the line takes is ignored, so a bot that adds it out of
    habit gets the command's normal answer instead of a usage error. One parse: a value read from standard input is read
    once, a `--json` after `--` stays a value, and a command with its own `--json` (`hub tool list`) still gets it."""
    p.hub_argv = list(argv)  # the errors below name the closest command the line could have meant
    args, extra = p.parse_known_args(argv)
    left = [a for a in extra if a != "--json"]
    if left:
        p.error("unrecognized arguments: " + " ".join(left))
    return args


def removed_notice(argv):
    """The one line for a command that is gone (REMOVED), or None."""
    for old, instead in REMOVED:
        if tuple(argv[: len(old)]) == old:
            return f'hub: "{" ".join(old)}" is gone; {instead}'
    return None


def rename_argv(argv):
    """`argv` for the new spelling of an old command line, and the notice to print (or None when nothing was renamed)."""
    argv = list(argv)
    if (
        argv[:1] == ["note"]
        and len(argv) > 1
        and argv[1] not in ("create", "list", "delete")
        and not argv[1].startswith("-")
    ):
        return ["note", "create"] + argv[1:], '"hub note <bot> <text>" is now "hub note create <bot> <text>"'
    if argv[:1] == ["notice"] and len(argv) > 1 and (argv[1] in NOTICE_COMMANDS or argv[1].startswith("-")):
        return argv, None  # `hub notice list` is new; only `hub notice <text>` is the old send
    if argv[:2] in (["org", "order"], ["org", "reports-to"]):  # `hub org` alone is the old `hub team show`
        return argv, None
    for old, new, tail, rewrite in RENAMED:
        if tuple(argv[: len(old)]) == old:
            rest = argv[len(old) :]
            rest = (rewrite(rest) if rewrite else rest) + list(tail)
            return list(new) + rest, f'"hub {" ".join(old)}" is now "hub {" ".join(new)}"'
    return argv, None


def runner_credential(path=None):
    """This Mac's runner credential as (url, token), or None when it is not registered.

    `hub sql` and the integration reads use it; the API answers a runner's SQL as the person
    who registered the machine. That is how a script or an agent on the Mac reads without a
    browser session.
    """
    try:
        config = json.loads(Path(path or RUNNER_CONFIG).read_text())
        return str(config["url"]), str(config["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def split_human(argv):
    """Pull `--human <id>` out of anywhere in the line, so it works before or after the command."""
    argv, who = list(argv), None
    for i, a in enumerate(argv):
        if a == "--human" and i + 1 < len(argv):
            who = argv[i + 1]
            return argv[:i] + argv[i + 2 :], who
        if a.startswith("--human="):
            return argv[:i] + argv[i + 1 :], a.split("=", 1)[1]
    return argv, who


def main(argv):
    argv, who = split_human(argv)
    old = legacy_idea(argv)
    if old:
        notice, lines = old
        print(f"hub: {notice}; the old name stops working in the next release", file=sys.stderr)
        for line in lines:
            code = run(line, who)
            if code:
                return code
        return 0
    gone = removed_notice(argv)
    if gone:
        print(gone, file=sys.stderr)
        return 1
    argv, renamed = rename_argv(argv)
    if renamed:
        print(f"hub: {renamed}; the old name stops working in the next release", file=sys.stderr)
    return run(argv, who)


def run(argv, who):
    """One command line in its current spelling: parsed here, then answered here or sent to the hub."""
    p = parser()
    try:
        args = parse(p, argv)
    except SystemExit as e:  # argparse already said what was wrong, or printed help
        return 0 if not e.code else 1
    if not getattr(args, "fn", None):
        p.print_help(sys.stderr)
        return 1
    try:
        if args.fn == "task create" and args.dry_run:  # never reaches remotecli: nothing to send
            return cmd_task_dry_run(args, who)
        if args.fn == "decision ask" and args.list:  # the sets are files in this checkout, no API needed
            from clients import judge

            print(json.dumps(judge.list_sets(), indent=2))
            return 0
        if args.fn == "bot check-names":  # reads the bot's own files here: no hub, no credential
            from clients import bot_names

            return bot_names.command(args.target, args.fix, os.environ.get("HUB_WORKSPACE"))
        if args.fn == "doc fetch":  # runs here, beside the bot: no hub, no credential
            from clients import doc_fetch

            try:
                print(json.dumps(doc_fetch.fetch(args.url, args.max_chars), indent=2))
                return 0
            except doc_fetch.FetchError as e:
                print(json.dumps({"error": e.code, "detail": e.message}, indent=2))
                return 1
        if args.fn == "tool call":  # runs here, through the tool proxy: no hub API, the run's token
            from clients import tool_call

            try:
                data = json.loads(args.data) if args.data is not None else None
                print(json.dumps(tool_call.call(args.tool, args.method, args.path, data, args.query), indent=2))
                return 0
            except (tool_call.CallError, ValueError) as e:
                print(json.dumps({"error": str(e)}, indent=2))
                return 1
        if not os.environ.get("HUB_API_URL"):
            local = args.fn in READ_OUTSIDE_A_TURN and not (args.fn == "tool list" and args.bot)
            credential = runner_credential(os.environ.get("HUB_RUNNER_CONFIG")) if local else None
            if not credential:
                raise CliError(
                    "HUB_API_URL is not set: `hub` talks to the Tico API and runs inside a "
                    "bot turn, where the runner sets HUB_API_URL, HUB_TOKEN and HUB_BOT"
                    + (
                        f"; outside a turn `hub {args.fn}` needs this Mac's runner credential (scripts/setup-runner.sh)"
                        if local
                        else ""
                    )
                )
            os.environ["HUB_API_URL"], os.environ["HUB_TOKEN"] = credential
        if (args.fn in WORKSPACE_COMMANDS or args.fn == "bot create" and not args.record_only) and not os.environ.get(
            "HUB_WORKSPACE"
        ):
            raise CliError(
                "HUB_WORKSPACE is not set: bot repositories live in the workspace this "
                "machine was enrolled with, which the runner gives every turn"
            )
        from clients import remotecli

        return remotecli.main(args, who)
    except CliError as e:
        print(json.dumps({"error": str(e)}, indent=2))
        return 1
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
