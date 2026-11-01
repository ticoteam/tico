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
    hub thread show <thread> [--cursor C]      one thread in tree order: a message's sub-thread sits under it
    hub thread home <actor>                    a teammate's Home thread, made on first use (needs Read on them)
    hub thread dm <actor>                      your DM with one person or bot: the open one, or a new one (needs Write)
    hub thread dm-group <actor> <actor> [...] [--body "<text>"]   a group DM with these people or bots; --body is the first message
    hub thread create ["<title>"] [--audience members|team|company|personal] [--group G] [--member ACTOR ...]
                    [--body "<text>"]          the first message goes in with --body
    hub thread post <thread> "<text>" [--kind say|ask] [--to ACTOR] [--ref KIND:ID ...]
    hub thread reply <thread> <message> "<text>"   a side topic: the reply goes in that message's sub-thread
    hub thread edit <message> "<text>"         edit a message you wrote: a new revision
    hub thread sub <message>                   open a message's sub-thread without posting
    hub thread react|unreact <message-id> <emoji>   your reaction to a message, such as :+1:
    hub thread open-earlier <thread>           a bot's manager opens its earlier messages to its Home readers
    hub thread task <message-id> --title T --owner O   a task from a message: private unless the thread is company-wide
    hub thread follow|unfollow <thread> [--notify live|mentions|digest|muted]
    hub thread read <thread> [--seq N | --all]
    hub thread search "<words>" [--cursor C]   the messages in threads you can read
    hub thread members list|add|remove <thread> [<actor>] [--owner]   actors: human:<id>, bot:<slug>, group:<id>
    hub thread update <thread> [--title T] [--archive|--unarchive] [--audience ...] [--group G]
    hub thread link-slack <thread> [<channel>] [--history] [--off]
                                           a thread's Slack link (admins): with a channel, asks for it and Tico
                                           checks it; --history also brings the channel's earlier messages; --off
                                           takes it away; with the thread alone, lists its links
    hub question ask <bot> [<bot>...] "<question>" --wait 60
    hub question answer <message-id> "<text>" [--unknown "needs X"] [--on-behalf-of turn]
    hub note create <bot> "<text>" [--text-file f] [--defer 4h]  a deferred message: wakes nobody; the bot's next
                                           run reads it (with --defer, a run starts by then if none came)
    hub note list [--to me|X] [--from me|X] [--since 24h|ISO] [--waiting]   notes, newest first
    hub note delete <id>                   take back a note no run has carried yet
    hub task create --owner <bot|human> --title "..." [--body "..."|--body-file f] [--due D] [--parent ID]
                    [--goal ID] [--model M [--effort E] [--harness H]] [--dry-run] the checks a create would fail,
                    nothing written
    hub task model <id> [<model>] [--effort E] [--harness H] [--clear]
                                           the model this task's runs use; --clear puts it back on the bot's task model
                    [--defer [24h|2d|never]]  for a bot: a deferred task, no wake; its next run carries it, and
                                           one starts by the time given (24h when none; --next-run is the old name)
                                           blocked on a person? --owner <person> --parent <your task>
                                           for a person, the body's first paragraph is the ask (as --note below)
    hub task ask <id> "<question>"
    hub task update <id> --status waiting --on <person> --note "<plain ask>"
                                           ask a person on your task: one small subtask for them, yours stays blocked
    hub task question "<question>" --on <person> [--why "<why it matters>"]
                    [--option "A -> what happens" --option "B=<file> -> what happens"] [--multi --recommend A]
                                           one decision under your task, waiting on them; none for Yes / No.
                                           Every option says what happens (->); several picks (--multi) mark
                                           the recommended ones; no ids, paths or errors above --body
                    [--if-no-answer "Yes" --default-kind reversible|send|spend|delete] [--by D]
                                           what you do if they don't answer: a reversible step goes ahead
                                           after the team's days (3); asks expire after 14 days
    hub task comment <id> "<text>" [--steer | --queue | --defer [30m|4h|2d|never] | --goal]   on the record
                                           with your name. --steer goes into its bot's running turn, --queue right
                                           after it, --defer with its next turn on the task (at the latest 24h, or
                                           the time given), --goal sets its goal
    hub task comment-edit <id> <comment-id> "<text>"   change a comment you wrote; wakes nobody
    hub task comment-delete <id> <comment-id>          take back a comment you wrote
    hub task ask-resolve <id> <question-id> [--quote "<their words>"]
                                                       a question answered elsewhere, quoted; no --quote withdraws yours
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
                    [--on PERSON]          with --status waiting: the person it waits on (their Needs you);
                                           --note is then your ask to them, one or two plain sentences:
                                           "Approve the video tool trial on Thursday? If yes, I'll send the invite."
                    [--on bot:SLUG]        with --status waiting: an active bot owning a task this one is
                                           blocked by (hub task wait); --note is the reason shown
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
    hub page archive <id> [--restore]      hide a page from lists and search, or bring it back
    hub card list [--place P]              what is pinned at dashboard, team:<group>, bot:<slug>, task:<id> or
                                           person:me (My dashboard)
    hub card add <template> [--place P] [--team G] [--weeks N] [--count N] [--kpi ID] [--title T] [--size S]
                                           tasks_by_status, done_per_week, needs_you, spend, kpi, automation_runs,
                                           latest_pages, needs_attention, unread_threads, my_tasks, updates; a bot
                                           pins to its own sidebar or task
    hub card update <id> [--title T] [--team G] [--weeks N] [--count N] [--kpi ID]
    hub card remove <id>
    hub connections list [--cursor C] [--limit N]
                                           every connection you may see: status, scope, who reads it, its bots
    hub inbound audience <inbox-id> company|team [--group G]
                                           who reads an Email & Phone inbox (owner or admin)
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
    hub assistant learn "<line>" [--company]
                                           in a person's own chat: save what they taught you, in their own
                                           layer (--company: the Strategy Bot, about the company, owner's chat only)
    hub assistant learned                  what a person's chat bot has learned (What it has learned)
    hub replay show [--from ISO] [--to ISO] [--bot B|--person P|--team T]
                                           what happened in a window (default the last 24 hours), as you may see it
    hub goal list [--owner me|X] [--all]   what you are for: your goals in order, the chain above
                                           them, and your reports' goals; --all is every goal
    hub goal show <id>                     the goal, its KPIs (target, colour), tasks, check-ins, history
    hub goal read-back '[{"kind":"goal_create","owner":"company","title":"..."}]'
    hub goal save POST /api/v2/goals '<body_json>' --on-behalf-of turn
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
                               brand_draft|gtm_draft
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
    hub gtm show [--section S] [--version N] | summary | sources
                                           the go-to-market: motion, buyer, playbooks; sources of truth
    hub brand|gtm draft --section S --body JSON [--revision N] | publish [--note "..."]
                                           owner or admin; a bot proposes with --kind brand_draft|gtm_draft
    hub growth segment list | create --name ...|--segment-id ID [--status S --motion M --who-buys ... --deal-size ...
                             --why ... --owner ...] | update ID   who we sell to; serving/next are Us/Next on Strategy
    hub growth channel list [--retired] | show SLUG | add SLUG --name ... [--segment ID] | update SLUG | retire SLUG
    hub growth source list | add --truth-for ... --system ... --reach-kind ... | update ID | retire ID
    hub growth idea create --title ... [--hypothesis --prediction --bet --kpi --channel ... --segment ... --i --c --e
                           --source-kind --source-ref --follow-up-of --assignee]
    hub growth idea list [--status S] [--bet ID] [--channel SLUG] [--segment ID] [--kpi ID] [--q ...] | show ID
                         | update ID
    hub growth idea score ID --i N --c N --e N
    hub growth idea start ID --to D --change ... --stop-if ... [--from D] [--judged-kpi ID] [--min-events N]
    hub growth idea verdict ID --status won|lost|inconclusive|dropped|paused [--result --learning
                            --bet-effect --confidence]   a stop-rule hit or missed baseline is lost, before min events
    hub growth cohort list [--channel SLUG] [--month M] [--segment ID] | set --channel SLUG|unknown --month YYYY-MM
                           [--segment ID --spend N --bot-cost N --new N --d30 PCT --d90 PCT --d180 PCT --revenue N
                           --margin PCT --quality actual|estimate --source FIELD=TEXT]
    hub growth economics [--month M] [--segment ID] | week | evidence | working | kpis | settings [--set KEY=VALUE]
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
    hub approval read-back <id>...         the Assistant: the list it will approve, as its reply; the person's yes runs it
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
    hub audit [--object O] [--limit N] [--before B]
                                           who changed caps, spend, groups, automations and inboxes; the owner sees all
    hub automation inbox <inbox> [--send on|off]
                                           an inbox's send switch (email, slack:dm, slack:#chan); off = drafts only
    hub peer list                          this install's federation: key, Gateway, peerings, shared threads
    hub peer key | rotate-key              make this install's key, or a new one (both sides approve again)
    hub peer request <install>             ask another install to peer with this one; it approves, then you do
    hub peer approve|pause|resume|revoke <peering>
                                           approve it, the kill switch, resume it, or end it
    hub peer share|unshare <thread> <install>
                                           give an active peer a thread you own, or take an install out of one
    hub peer add-reader <thread> <actor>   let a person here read a shared copy of a thread (an admin adds them)
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
    hub update create "<- bullets>" [--kind daily|weekly]   post your own update when Tico asks
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
    hub bot update <slug> [--reports-to R] [--display-name N] [--description D] [--status S]
                                           apply a person's bot-settings request as them (BotOps)
    hub bot access <slug> [--see V] [--read V] [--write V]
                                           show or set who sees, reads, writes (V: everyone, or ben,group:legal,bot:x)
    hub bot owners <slug> [--add P ...] [--remove P ...]
                                           add or remove the humans who own a bot, as the requester (BotOps)
    hub bot mail <slug> [--send|--no-send] [--forward-to a@x,b@y] [--from-bot-yaml]
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
                                           draft a Tico bug report for the Tico team; a person approves it in Needs
                                           you (the version and install ID go with it unless --no-ids). Prints its id
    hub support send --approval <id>       send the report once a person approved it, filed as them; again is the
                                           same ticket, never a second one
    hub health-monitor findings            the Health Monitor: what the last hourly scan found
    hub health-monitor report <key> | --process process:<name> --cpu N --mb N
                                           report a finding as a Tico bug, one ticket per signature
    hub finance search [--kind entry|feed|bill|invoice|contact|rule|card] [--q words] [--status S] [--vendor V]
                    [--start D] [--end D] [--cursor C] [--limit N]   the books you may see; name a kind to page
    hub finance report <pnl|balance_sheet|cash_flow|budget_vs_actual|ap_aging|ar_aging|spend_by|close> [--start D]
                    [--end D] [--period month] [--compare prior] [--top-n N] [--by vendor] [--month YYYY-MM]
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
    hub stall-triage [--on|--off]          stalled task check: Decide reads a stalled task's last messages before its stall check
                                           stays silent (the owner or an admin; on by default)
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
notice on stderr and runs the new command (RENAMED below).

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
import json
import os
import re
import sys
from pathlib import Path

if __package__ in (None, ""):                          # run as a script: python clients/hubcli.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Mirrors of the server's vocabulary (backend/hubdb.py), so a typo is refused by the parser
# before a request is made. The server is the authority when they disagree.
TASK_STATUSES = ("open", "doing", "waiting", "review", "ready", "done", "closed", "declined")
TASK_UPDATE_STATUSES = tuple(status for status in TASK_STATUSES if status != "closed")
# task_cap and spend_limit: proposals (backend/proposals.py).
APPROVAL_KINDS = ("send", "spend", "publish", "merge", "support", "task_cap", "spend_limit")
GOAL_STATUSES = ("red", "yellow", "green", "done", "dropped")     # set by hand; gray is only ever automatic
# The Assistant, acting on a task the person asked it to in their own Assistant chat (backend/assistant.py `instruction`).
ASKED = ("Assistant: the person asked for exactly this in their own Assistant chat: `turn` (their message that started "
         "this run) or that message's id. Leave it out when they did not clearly ask, and propose instead")


class CliError(Exception):
    """Anything the arguments or the environment got wrong: exit 1."""


def task_update_status(value):
    if value == "closed":
        raise argparse.ArgumentTypeError("use `hub task close <id>` to close a task")
    return value


GO_LIVE_HELP = """When the computer does not have the bot's repository yet, go-live keeps the placement and answers
waiting_for_repository: the computer clones it, and Tico finishes going live when it reports the repository."""


def out(obj):
    print(json.dumps(obj, indent=2, default=str, sort_keys=False))
    return 0


def body_of(args):
    if getattr(args, "body_file", None):
        return Path(args.body_file).read_text()
    return args.body or ""


# ----------------------------------------------------------------------------- dry run
# The rules are the server's own functions (backend/hubdb.py) run client-side, so a dry run
# reads exactly as the real create would. They are imported only here: the module is pure
# stdlib, opens no database, and the rest of the CLI never touches backend/.
def words_outside_quotes(text):
    """What rule 7 counts: hubdb's own helper, so the number a dry run prints is the number the
    lint would refuse on (quoted `>` lines and URLs do not count)."""
    from backend import hubdb as H
    return len(H._outside_quotes(text).split())


def owner_from_registry(name):
    """The owner as an actor string, read from the registry files the cloud seeds its roster
    from; None when it is in neither. The CLI has no database to resolve against (§3) and
    must not open one."""
    from backend import hubdb as H, people as P
    from clients import registry as REG
    raw = str(name or "").strip()
    if H.actor_kind(raw) in ("bot", "human"):
        return raw
    try:
        emps = {e["name"] for e in REG.load_registry()[1] if e.get("name")}
        roster = P.load(REG.load_people())
    except Exception:               # a CLI that cannot read the registry still answers
        emps, roster = set(), None
    if raw in emps:
        return H.bot_actor(raw)
    if P.person(raw, roster):
        return H.human_actor(raw)
    return None


def task_problems(actor, owner, title, body, type=None):
    """(resolved owner, the problems the server's `task_create` would refuse on, the writing
    warnings it would accept with), writing none.

    The owner's kind comes from the registry files; the lint (`lint_human_item`) is hubdb's own
    function run here. Reach against live state, the
    duplicate check and whether a named type exists are the hub's to decide at the real create.
    A type other than General is a custom type, whose tasks neither lint shapes.
    """
    from backend import hubdb as H
    problems, warnings = [], []
    title, body = str(title or "").strip(), str(body or "")
    general = str(type or "").strip().lower() in ("", H.GENERAL_TYPE)
    target = owner_from_registry(owner)
    if not target:
        problems.append(f"{owner} is not in registry/employees.yaml or registry/people.yaml")
    if not title:
        problems.append("give it a title that says what you are asking for")
    elif H.is_human(target) and general and H.STYLE_LINT != "off":
        # writing problems: the create is accepted with these as warnings unless TICO_STYLE_LINT=refuse
        (problems if H.STYLE_LINT == "refuse" else warnings).extend(H.lint_human_item(body, title=title))
    if H.is_bot(actor) and H.is_human(target) and general and title:
        # the ask that opens a person's task is always refused when it is missing or unclear
        problems += list(filter(None, [H.person_ask_refusal(target, H.person_ask_of_body(body, title), "assign")]))
    if H.is_bot(actor) and general:
        # plain-English titles: a warning this week, a refusal once TICO_TITLE_LINT=refuse
        problems += [f"{p} (title lint, {H.TITLE_LINT})" for p in H.lint_title(title)]
    return target, problems, warnings


def cmd_task_dry_run(args, who):
    """`hub task create --dry-run`: print what a create would be refused for; write nothing.

    No database is opened and no request is made; the owner's kind comes from the registry
    files and the same lint runs here, so a bot on the cloud API can size a human item before
    it spends a turn on a refusal. Prints lines rather
    than JSON: `- <problem>` per problem and exit 1, or one `ok:` line.
    """
    from backend import hubdb as H
    if not os.environ.get("HUB_API_URL"):
        raise CliError("HUB_API_URL is not set: `hub` talks to the Tico API and runs inside a "
                       "bot turn, where the runner sets HUB_API_URL, HUB_TOKEN and HUB_BOT")
    if who:
        raise CliError("--human is unavailable with HUB_API_URL; remote identity is the token")
    slug = (os.environ.get("HUB_BOT") or os.environ.get("HUB_EMPLOYEE") or "").strip()
    actor = H.bot_actor(slug) if slug else None
    body = body_of(args)
    owner, problems, warnings = task_problems(actor, args.owner, args.title, body, getattr(args, "type", None))
    if problems:
        print("\n".join(f"- {p}" for p in problems + [f"{w} (warning)" for w in warnings]))
        return 1
    for w in warnings:
        print(f"- {w} (warning: the create is accepted)")
    print(f'ok: would create "{args.title.strip()}" for {owner} '
          f'({words_outside_quotes(body)} words outside quoted drafts)')
    return 0


# ----------------------------------------------------------------------------- parser
DEFER_FOR = re.compile(r"^(never|[1-9][0-9]{0,5}[mhd])$")


def defer_for(word):
    """A defer's deadline (backend/delivery.py): 30m, 4h, 2d or never. Empty is the default deadline."""
    if word and not DEFER_FOR.match(word):
        raise argparse.ArgumentTypeError("a defer is 30m, 4h, 2d or never")
    return word


def delivery_flags(s, *, steer=True, goal=False, steer_help=None):
    """How a send reaches its bot (backend/delivery.py): --steer, --queue or --defer [time], at most one. --defer sets
    `defer_for` ('' for the default deadline); the others set `delivery`."""
    ways = s.add_mutually_exclusive_group()
    if steer:
        ways.add_argument("--steer", dest="delivery", action="store_const", const="steer",
                          help=steer_help or "into the bot's running turn now (right after it if its harness can't steer)")
    ways.add_argument("--queue", dest="delivery", action="store_const", const="queue",
                      help="right after the bot's current turn there, or now if it is idle")
    ways.add_argument("--defer", dest="defer_for", nargs="?", const="", type=defer_for, metavar="30m|4h|2d|never",
                      help="wake nobody: it goes in with the bot's next turn there, at the latest after this time")
    if goal:
        ways.add_argument("--goal", dest="delivery", action="store_const", const="goal",
                          help="make the text the goal of the task's bot session (plain text, no files)")
    return ways


def delivery_of(args):
    """{delivery, defer_for} for a request body from delivery_flags' arguments; {} when none was given."""
    if getattr(args, "defer_for", None) is not None:
        return {"delivery": "defer", **({"defer_for": args.defer_for} if args.defer_for else {})}
    return {"delivery": args.delivery} if getattr(args, "delivery", None) else {}


def quiet_hours(word):
    """0 to 24, whole hours."""
    try:
        hours = int(word)
    except ValueError:
        hours = -1
    if not 0 <= hours <= 24:
        raise argparse.ArgumentTypeError("use a whole number of hours from 0 to 24")
    return hours


def on_off(word):
    """`on` or `off` as the boolean the tool takes."""
    if word not in ("on", "off"):
        raise argparse.ArgumentTypeError("use on or off")
    return word == "on"


def _finance_payload(text):
    """`hub finance act --payload`: a JSON object, or `-` to read it from stdin."""
    try:
        value = json.loads(sys.stdin.read() if text == "-" else text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--payload is not JSON ({exc})") from None
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("--payload must be a JSON object")
    return value


def thread_page_flags(s):
    """The paging flags of a thread list: a page's next_cursor reads on, and --limit sets the size."""
    s.add_argument("--cursor", help="the page after this cursor: a page's next_cursor")
    s.add_argument("--limit", type=int, help="items in the page (50 by default, 100 at most)")


def parser():
    p = argparse.ArgumentParser(prog="hub", description="the company hub: messages, tasks, status")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("whoami").set_defaults(fn="whoami")

    inbound = sub.add_parser('inbound', help='shared company email and SMS inboxes').add_subparsers(dest='sub')
    item = inbound.add_parser('inboxes'); item.set_defaults(fn='inbound inboxes')
    item.add_argument('--after'); item.add_argument('--limit', type=int)
    item = inbound.add_parser('create-email'); item.add_argument('name', nargs='?'); item.set_defaults(fn='inbound create-email')
    for action in ('list', 'search', 'cursor', 'wait'):
        item = inbound.add_parser(action); item.set_defaults(fn='inbound '+action)
        item.add_argument('--inbox-id', dest='inbox_ids', action='append')
        if action == 'search': item.add_argument('q')
        if action in ('list', 'search', 'wait'):
            item.add_argument('--kind', choices=('email','sms'))
            item.add_argument('--from', dest='sender')
            item.add_argument('--has-code', action='store_true', default=None)
            item.add_argument('--after', type=int, required=action=='wait')
            item.add_argument('--limit', type=int)
        if action == 'wait': item.add_argument('--timeout', type=int)
        if action in ('list', 'search'):
            item.add_argument('--before', type=int)
        if action == 'list':
            item.add_argument('--spam', action='store_true', default=None)
    item = inbound.add_parser('audience'); item.set_defaults(fn='inbound audience')
    item.add_argument('inbox_id'); item.add_argument('audience', choices=('company', 'team')); item.add_argument('--group')
    for action in ('read', 'code'):
        item = inbound.add_parser(action); item.set_defaults(fn='inbound '+action)
        item.add_argument('message_id')
        if action == 'read':
            item.add_argument('--offset', type=int); item.add_argument('--max-chars', type=int)


    connections = sub.add_parser('connections', help='every connection in one list').add_subparsers(dest='sub')
    item = connections.add_parser('list'); item.set_defaults(fn='connections list')
    item.add_argument('--cursor'); item.add_argument('--limit', type=int)

    mail = sub.add_parser('mail', help='physical mail the company received (scanned letters)').add_subparsers(dest='sub')
    item = mail.add_parser('list'); item.set_defaults(fn='mail list')
    item.add_argument('--status', choices=('new','filed','archived','failed'))
    item.add_argument('--kind', choices=('legal','government_tax','bill','check','financial','junk','other'))
    item.add_argument('--after'); item.add_argument('--limit', type=int)
    item = mail.add_parser('read'); item.set_defaults(fn='mail read'); item.add_argument('item_id')

    meeting = sub.add_parser("meeting",
                             help="search meetings, read transcripts, import a transcript from another tool").add_subparsers(dest="sub")
    granola = meeting.add_parser("granola", help="your Granola account; connect in Meetings in your browser").add_subparsers(dest="granola_action")
    for action in ("status", "sync"):
        granola.add_parser(action).set_defaults(fn="meeting granola " + action)
    s = meeting.add_parser("search")
    s.add_argument("q", nargs="?", default="")
    s.add_argument("--person", default="")
    s.add_argument("--since")
    s.add_argument("--until")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--offset", type=int, default=0)
    s.set_defaults(fn="meeting search")
    s = meeting.add_parser("read", help="read a transcript")
    s.add_argument("id")
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--limit", type=int, default=20000)
    s.set_defaults(fn="meeting read")
    s = meeting.add_parser("delete", help="delete a Meeting you may edit")
    s.add_argument("id")
    s.set_defaults(fn="meeting delete")
    meeting.add_parser("pending", help="your Pending queue").set_defaults(fn="meeting pending")
    s = meeting.add_parser("approve", help="share your Pending meetings")
    target = s.add_mutually_exclusive_group(required=True)
    target.add_argument("id", nargs="?")
    target.add_argument("--all", action="store_true")
    s.add_argument("--private", action="store_true", default=None)
    s.set_defaults(fn="meeting approve")
    for action in ("dismiss", "restore"):
        s = meeting.add_parser(action)
        s.add_argument("id")
        s.set_defaults(fn="meeting " + action)
    s = meeting.add_parser("import", help="file a transcript (text, WebVTT, SRT or JSON segments) as one of your meetings")
    s.add_argument("file", help="the transcript file; - reads standard input")
    s.add_argument("--title", help="default: the file's name")
    s.add_argument("--date", help="when it started: 2026-09-28 or 2026-09-28T16:00 (this Mac's time zone) or with an offset")
    s.add_argument("--participant", dest="participants", action="append", help="an email or a name; repeat for each")
    s.add_argument("--source", default="upload", help="where it came from: zoom, granola, otter, fireflies... (default upload)")
    s.add_argument("--external-id", help="that system's id for it: importing it again updates the meeting")
    s.add_argument("--format", choices=["auto", "text", "vtt", "srt", "json"], default="auto")
    s.add_argument("--notes-file", help="a Markdown file of notes or a summary")
    s.add_argument("--media-url", help="an https link to the recording, if there is one")
    s.add_argument("--send-to", help="a bot to hand the meeting to, as Send does")
    s.add_argument("--private", action="store_true", default=None)
    s.add_argument("--review", choices=["pending", "live"], help="share immediately with live; otherwise your review setting applies")
    s.set_defaults(fn="meeting import")

    message = sub.add_parser("message", help="send a message, read what waits for you, mark one read").add_subparsers(dest="sub")
    s = message.add_parser("send", help="send a message to a bot or a human")
    s.add_argument("to")
    s.add_argument("text")
    s.add_argument("--fyi", action="store_true", help="an fyi: it expects no reply, and takes no conversation or references")
    s.add_argument("--conversation")
    s.add_argument("--ref", action="append")
    ways = delivery_flags(s, steer=False)
    ways.add_argument("--steer", action="store_true",
                      help="into the bot's running turn now; with --ref task:<id> you requested, into its owner's "
                           "running turn on that task")
    s.add_argument("--visual", action="append", metavar="PATH",
                   help="a picture, SVG, HTML page, video or audio to show with it (a file in your checkout, or ID@VERSION "
                        "already in Tico); repeat for more")
    s.add_argument("--title", action="append", dest="visual_title", metavar="TITLE",
                   help="the caption for each --visual, in the same order")
    s.add_argument("--needs-action", dest="needs_action", action="store_true", help="a bot's message to a bot waits for that bot's next run; this wakes it now")
    s.set_defaults(fn="message send")
    message.add_parser("list", help="the messages and fyis waiting for you").set_defaults(fn="message list")
    s = message.add_parser("mark-read", help="mark a message from your inbox as read (an external agent's own delivery)")
    s.add_argument("message_id")
    s.set_defaults(fn="message mark-read")
    s = message.add_parser("redact", help="replace a secret (read from stdin) in a message with a mark")
    s.add_argument("message_id")
    s.add_argument("--label", help="what it was saved as")
    s.set_defaults(fn="message redact")

    s = sub.add_parser("conversation", help="what was said in a conversation").add_subparsers(dest="sub").add_parser("show")
    s.add_argument("conversation")
    s.add_argument("--before", help="the page before this message id: a page's next_before")
    s.add_argument("--since", help="only messages sent after this time (ISO 8601)")
    s.set_defaults(fn="conversation show")

    thread = sub.add_parser("thread", help="threads: list yours, read one, post, reply in a sub-thread, follow, search, catch up").add_subparsers(dest="sub")
    s = thread.add_parser("catchup", help="summarize what is new in a thread into your Assistant DM")
    s.add_argument("thread_id")
    s.add_argument("--thread-only", dest="scope", action="store_const", const="thread", default="subtree",
                   help="this thread only, not its sub-threads")
    s.set_defaults(fn="thread catchup")
    s = thread.add_parser("list", help="your threads: needs attention first, then unread, then recent; DMs apart")
    s.add_argument("--with", dest="with", metavar="ACTOR", help="only the threads shared with this person, bot or group")
    thread_page_flags(s)
    s.set_defaults(fn="thread list")
    s = thread.add_parser("show", help="one thread in tree order: each message, then the sub-threads under it")
    s.add_argument("thread")
    thread_page_flags(s)
    s.set_defaults(fn="thread show")
    s = thread.add_parser("home", help="a teammate's Home thread, made on first use (needs Read on them)")
    s.add_argument("actor", help="bot:<slug>, human:<id> or group:<id>")
    s.set_defaults(fn="thread home")
    s = thread.add_parser("dm-group", help="a group DM with several people or bots, with an optional first message")
    s.add_argument("members", nargs="+", metavar="ACTOR", help="human:<id> or bot:<slug>; one or more")
    s.add_argument("--body", help="the first message")
    s.set_defaults(fn="thread dm-group")
    s = thread.add_parser("dm", help="your DM with one person or bot: the open one, or a new one")
    s.add_argument("actor", help="human:<id> or bot:<slug>")
    s.set_defaults(fn="thread dm")
    s = thread.add_parser("create", help="start a thread: its audience decides who reads it")
    s.add_argument("title", nargs="?")
    s.add_argument("--audience", choices=["members", "team", "company", "personal"], help="members (private) by default")
    s.add_argument("--group", dest="audience_group_id", help="with --audience team: the group's id")
    s.add_argument("--member", dest="members", action="append", metavar="ACTOR",
                   help="a person, bot or group to add: human:<id>, bot:<slug> or group:<id>; repeat for more")
    s.add_argument("--body", help="the first message")
    s.set_defaults(fn="thread create")
    s = thread.add_parser("post", help="post a message to a thread")
    s.add_argument("thread")
    s.add_argument("text")
    s.add_argument("--kind", choices=["say", "ask"], help="ask: wants an answer from --to")
    s.add_argument("--to", help="the person or bot it is addressed to: human:<id> or bot:<slug>")
    s.add_argument("--ref", dest="refs", action="append", metavar="KIND:ID", help="a reference such as task:<id>; repeat for more")
    s.set_defaults(fn="thread post")
    s = thread.add_parser("reply", help="reply in a message's sub-thread: a side topic under that message")
    s.add_argument("thread")
    s.add_argument("message", help="the message the reply branches from")
    s.add_argument("text")
    s.add_argument("--ref", dest="refs", action="append", metavar="KIND:ID", help="a reference such as task:<id>; repeat for more")
    s.set_defaults(fn="thread reply")
    s = thread.add_parser("edit", help="edit a message you wrote: its text becomes a new revision")
    s.add_argument("message")
    s.add_argument("text")
    s.set_defaults(fn="thread edit")
    s = thread.add_parser("sub", help="open a message's sub-thread without posting; it is made if it does not exist")
    s.add_argument("message")
    s.set_defaults(fn="thread sub")
    s = thread.add_parser("react", help="add your reaction to a message, such as :+1:")
    s.add_argument("message_id")
    s.add_argument("emoji", help="an emoji, or its shortcode such as :+1:")
    s.set_defaults(fn="thread react")
    s = thread.add_parser("unreact", help="remove your reaction from a message")
    s.add_argument("message_id")
    s.add_argument("emoji", help="an emoji, or its shortcode such as :+1:")
    s.set_defaults(fn="thread unreact")
    s = thread.add_parser("open-earlier", help="open a bot's earlier messages to the people who read its Home (a manager of the bot)")
    s.add_argument("thread_id")
    s.set_defaults(fn="thread open-earlier")
    s = thread.add_parser("task", help="make a task from a message: private unless the thread is company-wide")
    s.add_argument("message_id")
    s.add_argument("--title", required=True)
    s.add_argument("--owner", required=True, help="a bot slug, a person id, or me")
    s.set_defaults(fn="thread task")
    s = thread.add_parser("follow", help="follow a thread: live, mentions, digest or muted")
    s.add_argument("thread")
    s.add_argument("--notify", choices=["live", "mentions", "digest", "muted"], help="live by default")
    s.set_defaults(fn="thread follow")
    s = thread.add_parser("unfollow", help="stop following a thread; you keep what you can read")
    s.add_argument("thread")
    s.set_defaults(fn="thread unfollow")
    s = thread.add_parser("read", help="mark a thread read to a message's seq, or all of it")
    s.add_argument("thread")
    s.add_argument("--seq", type=int, help="the seq of the last message you read")
    s.add_argument("--all", action="store_true", help="read the thread and every sub-thread under it")
    s.set_defaults(fn="thread read")
    s = thread.add_parser("search", help="search the threads you can read")
    s.add_argument("q")
    thread_page_flags(s)
    s.set_defaults(fn="thread search")
    members = thread.add_parser("members", help="who can read a thread: list, add, remove").add_subparsers(dest="subsub")
    s = members.add_parser("list", help="the people, bots and groups in a thread, with their roles")
    s.add_argument("thread")
    thread_page_flags(s)
    s.set_defaults(fn="thread members list")
    s = members.add_parser("add", help="add a person, bot or group so it can read the thread")
    s.add_argument("thread")
    s.add_argument("actor", help="human:<id>, bot:<slug> or group:<id>")
    s.add_argument("--owner", dest="role", action="store_const", const="owner",
                   help="as an owner: may remove members")
    s.set_defaults(fn="thread members add")
    s = members.add_parser("remove", help="remove a person, bot or group: they stop reading the thread (owner or admin)")
    s.add_argument("thread")
    s.add_argument("actor")
    s.set_defaults(fn="thread members remove")
    s = thread.add_parser("update", help="rename, archive or unarchive a thread, or change who may read it")
    s.add_argument("thread")
    s.add_argument("--title")
    s.add_argument("--archive", dest="archived", action="store_const", const=True)
    s.add_argument("--unarchive", dest="archived", action="store_const", const=False)
    s.add_argument("--audience", choices=["members", "team", "company"],
                   help="team or company makes it public; only its starter or an admin can")
    s.add_argument("--group", dest="audience_group_id", help="with --audience team: the group's id")
    s.set_defaults(fn="thread update")
    s = thread.add_parser("link-slack", help="link a thread to a Slack channel (admins), list its links, or --off to take them away")
    s.add_argument("thread", help="the thread's id")
    s.add_argument("channel", nargs="?", help="#name or id (C0123456789); left out, the thread's links are listed")
    s.add_argument("--history", action="store_true", help="also bring in the channel's earlier messages")
    s.add_argument("--off", action="store_true", help="take the thread's Slack link away")
    s.set_defaults(fn="thread link-slack")

    question = sub.add_parser("question", help="ask other bots a question and wait; answer one asked of you").add_subparsers(dest="sub")
    s = question.add_parser("ask")
    s.add_argument("words", nargs="*")
    s.add_argument("--wait", type=float, default=60)
    s.set_defaults(fn="question ask")
    s = question.add_parser("answer")
    s.add_argument("message_id")
    s.add_argument("text", nargs="?", default="")
    s.add_argument("--unknown")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="question answer")

    note = sub.add_parser("note", help="a deferred message for a bot: no run now, its next run reads it").add_subparsers(dest="sub")
    s = note.add_parser("create", help="leave a bot a deferred message: no run now, its next run reads it")
    s.add_argument("to")
    s.add_argument("text", nargs="?", default="")
    s.add_argument("--text-file", dest="text_file")
    s.add_argument("--defer", dest="defer_for", type=defer_for, metavar="30m|4h|2d|never",
                   help="start a run by this time if none carried it (never when omitted)")
    s.set_defaults(fn="note create")
    s = note.add_parser("list", help="deferred messages (notes), newest first: left for you and by you")
    s.add_argument("--to")
    s.add_argument("--from", dest="sender")
    s.add_argument("--since", help="an ISO time, or 24h / 3d")
    s.add_argument("--waiting", action="store_true", help="only notes no run has carried yet")
    s.add_argument("--limit", type=int, default=200)
    s.set_defaults(fn="note list")
    s = note.add_parser("delete", help="take back a note no run has carried yet")
    s.add_argument("id")
    s.set_defaults(fn="note delete")

    files = sub.add_parser("file", help="what you publish for people: reports, documents, imported objects").add_subparsers(dest="sub")
    s = files.add_parser("archive", help="archive a File with your rights")
    s.add_argument("id")
    s.set_defaults(fn="file archive")
    s = files.add_parser("list", help="the files on your page, newest activity first")
    s.add_argument("--bot")
    s.add_argument("--limit", type=int)
    s.add_argument("--cursor")
    s.set_defaults(fn="file list")
    s = files.add_parser("publish", help="upload a file from this checkout (reports/x.md); again adds a version")
    s.add_argument("path")
    s.add_argument("--title")
    s.add_argument("--task", help="the task it is for; defaults to the one you are working on")
    s.add_argument("--scope", choices=["task", "bot"])
    s.set_defaults(fn="file publish")
    s = files.add_parser("link", help="list a Google Doc, Notion page, Figma file or any https link")
    s.add_argument("url")
    s.add_argument("--title")
    s.add_argument("--task")
    s.add_argument("--scope", choices=["task", "bot"])
    s.set_defaults(fn="file link")
    s = files.add_parser("touch", help="you edited a linked document again: move it to the top")
    s.add_argument("target", help="a file id or its https link")
    s.set_defaults(fn="file touch")
    s = files.add_parser("import", help="copy an s3:// object with this computer's credentials into Tico")
    s.add_argument("uri")
    s.add_argument("--title")
    s.add_argument("--task")
    s.set_defaults(fn="file import")
    s = files.add_parser("search", help="every file you may read, by title and text")
    s.add_argument("q")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="file search")
    s = files.add_parser("read", help="one version of a file you may read, as text, in full (default: the current one)")
    s.add_argument("id")
    s.add_argument("--version", type=int)
    s.set_defaults(fn="file read")
    s = files.add_parser("delete", help="the owner deletes a file for good: its bytes leave storage")
    s.add_argument("id")
    s.set_defaults(fn="file delete")
    s = files.add_parser("approved", help="exit 0 when a person approved this exact version on a review question, "
                                          "1 when not (its JSON answer says \"approved\": false), 2 on bad input. "
                                          "A failed call exits 1 or 2 with an error answer: check the answer, not only "
                                          "the code")
    s.add_argument("target", help="a path in this checkout (its bytes now), or FILE[@N] (the current version without @N)")
    s.add_argument("--by", help="whose verdicts count: human (default), any, or bot:SLUG")
    s.set_defaults(fn="file approved")
    s = files.add_parser("review", help="approve or ask for changes on another bot's file version, on a task you read")
    s.add_argument("target", help="FILE[@N] (the current version without @N), or a path in this checkout (its bytes now)")
    verdict = s.add_mutually_exclusive_group(required=True)
    verdict.add_argument("--approve", dest="verdict", action="store_const", const="approve")
    verdict.add_argument("--changes", dest="verdict", action="store_const", const="changes")
    s.add_argument("--task", required=True, help="the task the review is for")
    s.add_argument("--note", help="what to change, or why it is approved")
    s.set_defaults(fn="file review")
    page = sub.add_parser("page", help="pages: HTML a bot keeps up to date, with Tico's updated line").add_subparsers(dest="sub")
    s = page.add_parser("publish", help="publish an .html file from this checkout; again adds a version")
    s.add_argument("path")
    s.add_argument("--title")
    s.add_argument("--cadence", choices=["none", "daily", "weekly", "monthly"])
    s.add_argument("--as-of", dest="as_of", help="the date the data is from, YYYY-MM-DD")
    s.add_argument("--task", help="the task it is for; it shows at the top of that task")
    s.add_argument("--replaces", help="the id of an older page this one takes the place of")
    s.add_argument("--scope", choices=["task", "bot"])
    s.set_defaults(fn="page publish")
    s = page.add_parser("list", help="pages you may read, newest first")
    s.add_argument("--bot")
    s.add_argument("--task")
    s.add_argument("--archived", action="store_true", default=None)
    s.add_argument("--limit", type=int)
    s.add_argument("--cursor")
    s.set_defaults(fn="page list")
    s = page.add_parser("show", help="one page's status")
    s.add_argument("id")
    s.set_defaults(fn="page show")
    s = page.add_parser("archive", help="hide a page from lists and search, or --restore it")
    s.add_argument("id")
    s.add_argument("--restore", action="store_true", default=None)
    s.set_defaults(fn="page archive")
    cards = sub.add_parser("card", help="Tico's built-in cards on the Dashboard, team pages, sidebars and tasks").add_subparsers(dest="sub")
    s = cards.add_parser("list", help="what is pinned at a place, with what each card shows you")
    s.add_argument("--place")
    s.set_defaults(fn="card list")
    for name, helptext in (("add", "add a built-in card, pinned at --place"), ("update", "change a card's heading or settings")):
        s = cards.add_parser(name, help=helptext)
        s.add_argument("template" if name == "add" else "card")
        s.add_argument("--title")
        s.add_argument("--team")
        s.add_argument("--weeks", type=int)
        s.add_argument("--count", type=int)
        s.add_argument("--kpi")
        if name == "add":
            s.add_argument("--place")
            s.add_argument("--size", choices=["small", "medium", "large"])
        s.set_defaults(fn="card " + name)
    s = cards.add_parser("remove", help="remove a card and its pins")
    s.add_argument("card")
    s.set_defaults(fn="card remove")
    pins = sub.add_parser("pin", help="pin a page or a card at a place").add_subparsers(dest="sub")
    s = pins.add_parser("add", help="pin page:<file id> or card:<id> at a place")
    s.add_argument("place")
    s.add_argument("item")
    s.add_argument("--size", choices=["small", "medium", "large"])
    s.add_argument("--position", type=int)
    s.set_defaults(fn="pin add")
    s = pins.add_parser("update", help="resize or move a pin")
    s.add_argument("pin")
    s.add_argument("--size", choices=["small", "medium", "large"])
    s.add_argument("--position", type=int)
    s.set_defaults(fn="pin update")
    s = pins.add_parser("remove", help="unpin")
    s.add_argument("pin")
    s.set_defaults(fn="pin remove")
    docs = sub.add_parser("doc", help="read and search team docs and external sources, write internal docs, ask the Librarian").add_subparsers(dest="sub")
    s = docs.add_parser("archive", help="archive an internal Doc with your rights")
    s.add_argument("ref")
    s.set_defaults(fn="doc archive")
    s = docs.add_parser("list", help="internal docs by path")
    s.add_argument("--prefix", help="only paths starting with this, e.g. sales/")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="doc list")
    s = docs.add_parser("read", help="one internal or indexed external doc in full")
    s.add_argument("ref", help="a doc id/path, external:<id>, or manual:<name>")
    s.add_argument("--revision", help="external commit returned by search")
    s.set_defaults(fn="doc read")
    s = docs.add_parser("search", help="search internal, indexed external and linked docs, then the Tico manual")
    s.add_argument("q")
    s.add_argument("--limit", type=int)
    s.add_argument("--manual", dest="collection", action="store_const", const="manual",
                   help="only the read-only Tico manual (how to do something in Tico)")
    s.add_argument("--team", dest="collection", action="store_const", const="team",
                   help="only team docs")
    s.add_argument("--market", action="store_true", default=None,
                   help="also the market's notes, entities and evidence")
    s.add_argument("--source-id", help="only this external source")
    s.set_defaults(fn="doc search")
    s = docs.add_parser("write", help="create or replace an internal doc at a path")
    s.add_argument("path")
    s.add_argument("--title")
    s.add_argument("--body-file", dest="body_file", help="the Markdown file; standard input when omitted")
    s.add_argument("--note", help="one line on what changed")
    s.set_defaults(fn="doc write")
    s = docs.add_parser("history", help="the versions of a doc")
    s.add_argument("ref", help="a doc id or a path")
    s.set_defaults(fn="doc history")
    s = docs.add_parser("link-list", help="the linked docs: where the company's other docs live")
    s.set_defaults(fn="doc link-list")
    chat = sub.add_parser("chat", help="bot chat goals and commands").add_subparsers(dest="sub")
    s = chat.add_parser("goal", help="read, set, edit, pause, resume or clear a chat goal")
    s.add_argument("conversation_id")
    s.add_argument("action", nargs="?", default="get", choices=["get", "set", "edit", "pause", "resume", "clear"])
    s.add_argument("objective", nargs="?")
    s.set_defaults(fn="chat goal")
    s = chat.add_parser("send", help="send a chat message or harness command")
    s.add_argument("to")
    s.add_argument("text")
    s.add_argument("--conversation", dest="conversation_id")
    s.add_argument("--command", action="store_true")
    delivery_flags(s)
    s.add_argument("--visual", action="append", metavar="PATH",
                   help="a picture, SVG, HTML page, video or audio to show with it (a file in your checkout, or ID@VERSION "
                        "already in Tico); repeat for more")
    s.add_argument("--title", action="append", dest="visual_title", metavar="TITLE",
                   help="the caption for each --visual, in the same order")
    s.set_defaults(fn="chat send")
    assistant = sub.add_parser("assistant", help="the Assistant's proposals for a person to confirm").add_subparsers(dest="sub")
    assistant.add_parser("read", help="read your private Assistant chat").set_defaults(fn="assistant read")
    s = assistant.add_parser("send", help="ask your private Assistant")
    s.add_argument("text")
    s.set_defaults(fn="assistant send")
    s = assistant.add_parser("propose", help="ask the person to confirm one side-effecting operation")
    s.add_argument("--summary", required=True)
    s.add_argument("--path", required=True)
    s.add_argument("--method", default="POST", choices=["POST", "PUT", "PATCH", "DELETE"])
    s.add_argument("--body", default="{}", help="the request's JSON body")
    s.set_defaults(fn="assistant propose")
    s = assistant.add_parser("learn", help="save one thing the person taught you (What it has learned)")
    s.add_argument("text", help="one short line, in their words")
    s.add_argument("--company", dest="layer", action="store_const", const="company", default="personal",
                   help="the Strategy Bot in the owner's chat: about the company, not how they like things")
    s.set_defaults(fn="assistant learn")
    assistant.add_parser("learned", help="what your chat bot has learned: your layer, and the company's for the owner") \
        .set_defaults(fn="assistant learned")
    # Tags keep the existing task --label key contract.
    tag = sub.add_parser("tag", help="tags and checklist templates").add_subparsers(dest="sub")
    s = tag.add_parser("list")
    s.add_argument("--templates", dest="is_template", action="store_const", const=True, default=None)
    s.add_argument("--instances", dest="is_template", action="store_const", const=False)
    s.set_defaults(fn="tag list")
    s = tag.add_parser("show")
    s.add_argument("id", help="tag key or id")
    s.add_argument("--offset", type=int)
    s.set_defaults(fn="tag show")
    s = tag.add_parser("create")
    s.add_argument("key")
    s.add_argument("--label")
    s.add_argument("--metadata", help="JSON object")
    s.add_argument("--markdown")
    s.add_argument("--markdown-file")
    s.add_argument("--template", dest="is_template", action="store_const", const=True, default=None)
    s.add_argument("--from-template", dest="template_id")
    s.add_argument("--owner")
    s.set_defaults(fn="tag create")
    s = tag.add_parser("update")
    s.add_argument("id", help="tag key or id")
    s.add_argument("--version", type=int, required=True)
    s.add_argument("--label")
    s.add_argument("--metadata", help="JSON object; replaces metadata")
    s.add_argument("--markdown")
    s.add_argument("--markdown-file")
    s.add_argument("--owner")
    s.set_defaults(fn="tag update")

    task = sub.add_parser("task").add_subparsers(dest="sub")
    s = task.add_parser("child", help="create a subtask")
    s.add_argument("parent")
    s.add_argument("--owner", help="who does it; a --check-model subtask is your own (`me`)")
    s.add_argument("--title", required=True)
    s.add_argument("--body", default="")
    s.add_argument("--check-model", metavar="RUNTIME[:MODEL]", nargs="?", const="auto",
                   help="run this subtask of yours on another model, in its own session (e.g. grok, codex:gpt-6.1-sol); "
                        "with no value (put the flag after the parent id), the company's check model, from a provider other "
                        "than the parent's authors when one can run it")
    s.add_argument("--role", dest="check_role", choices=("review", "check"),
                   help="with --check-model: what the other model is asked for (default check)")
    s.set_defaults(fn="task child create")
    s = task.add_parser("tree", help="show nested subtasks")
    s.add_argument("id")
    s.set_defaults(fn="task tree")
    worktree = task.add_parser("worktree", help="create or attach this task's worktree").add_subparsers(dest="worktree_sub")
    for operation, argument in (("add", "repo"), ("attach", "path"), ("setup", "repo")):
        s = worktree.add_parser(operation)
        s.add_argument(argument)
        s.add_argument("--task", help="task id; defaults to this run's task")
        s.set_defaults(fn="task worktree " + operation)
    s = task.add_parser("caps", help="the task caps: today's count per pair, the tasks closed before the "
                        "receiver acted, and the changes bots asked for")
    s.set_defaults(fn="task caps")
    cap = task.add_parser("cap", help="set a task cap: how many tasks sender may create for receiver a day").add_subparsers(dest="cap_sub")
    s = cap.add_parser("set", help="sender receiver --per-day N; 0 blocks the pair, and with no --per-day the cap is removed")
    s.add_argument("sender", help="bot:<slug> or person:<id>")
    s.add_argument("receiver", help="bot:<slug> or person:<id>, a manager above the sender")
    s.add_argument("--per-day", dest="per_day", type=int, help="tasks a day; 0 blocks the pair")
    s.set_defaults(fn="task cap set")
    s = task.add_parser("create")
    s.add_argument("--visibility", choices=["public", "private"])
    s.add_argument("--public-title", dest="public_title", help="public roadmap title (needed with --visibility public)")
    s.add_argument("--public-summary", dest="public_summary", help="public roadmap summary")
    s.add_argument("--private", action="store_true", default=None, help="only requester and assignee may read")
    s.add_argument("--from-private", dest="from_private", action="store_true",
                   help="from a run on your private task: a company-visible task for a bot, in your own words (no "
                        "12 words copied from the private work, no secret-shaped value, no file or link of it)")
    s.add_argument("--owner", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--body", default="")
    s.add_argument("--body-file", dest="body_file")
    s.add_argument("--due")
    s.add_argument("--parent", help="file it as a subtask of this task")
    s.add_argument("--label", action="append", help="a label (repeat, or comma-separate); a project is a label")
    s.add_argument("--change-path", dest="change_paths", action="append", help="changed path for automatic QA classification")
    s.add_argument("--qa", choices=["yes", "no"], help="explicit QA override")
    s.add_argument("--qa-not-checkable-live", action="store_true", default=None)
    s.add_argument("--blocker", nargs=3, metavar=("REPO", "STATUS", "ENDPOINT"),
                   help="what refused, for a task to DevOps or a person: e.g. ticoteam/tico 403 workflow_dispatch. "
                        "An open ask about the same three joins this task instead of a second ask")
    s.add_argument("--top", action="store_true", help="put it at the top of the owner's queue")
    s.add_argument("--link", action="append", help="a URL to attach (a pull request, an issue, a document)")
    s.add_argument("--goal", help="the goal this task serves (hub goal list); optional")
    ways = delivery_flags(s, steer=False)
    # The older words for --defer, kept: a deferred task with the default deadline.
    ways.add_argument("--next-run", dest="next_run", action="store_true", help="the older name for --defer")
    ways.add_argument("--quiet", dest="next_run", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="print the checks a create would fail and write nothing")
    s.add_argument("--request-id", dest="request_id",
                   help="the person's message this carries: handing BotOps something a person asked you for, their "
                        "message id or `turn` (BotOps acts with their rights, or puts it to them as a Confirm card)")
    s.add_argument("--type", help="task type id or name; defaults to General")
    s.add_argument("--step", help="step id or name in the type")
    s.add_argument("--number", type=int, help="movers: keep an imported ticket's number")
    s.add_argument("--model", help="the model this bot-owned task's runs use, from `hub bot model`'s list; leave out "
                                   "for the bot's task model")
    s.add_argument("--effort", help="the reasoning effort that model takes")
    s.add_argument("--harness", help="the harness that runs that model")
    s.set_defaults(fn="task create")
    types = task.add_parser("type", help="manage task types (movers only)").add_subparsers(dest="type_sub")
    bots_help = ("what every bot may do with the type's tasks: parties (only the bots on each task), "
                 "read (read and comment on all of them) or work (also change them)")
    steps_only_help = "its tasks change status only by moving to a step, never by closing, declining, reopening or finishing"
    s = types.add_parser("create")
    s.add_argument("--default-visibility", dest="default_visibility", choices=["public", "private"])
    s.add_argument("--name", required=True)
    s.add_argument("--steps-file", dest="steps_file")
    s.add_argument("--bots", choices=["parties", "read", "work"], help=bots_help)
    s.add_argument("--numbered", action="store_true", default=None, help="number each task on the type")
    s.add_argument("--steps-only", dest="steps_only", action="store_true", default=None, help=steps_only_help)
    s.set_defaults(fn="task type create")
    s = types.add_parser("update")
    s.add_argument("--default-visibility", dest="default_visibility", choices=["public", "private"])
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--steps-file", dest="steps_file")
    s.add_argument("--bots", choices=["parties", "read", "work"], help=bots_help)
    s.add_argument("--numbered", action=argparse.BooleanOptionalAction, default=None,
                   help="number each task created on or moved onto the type")
    s.add_argument("--steps-only", dest="steps_only", action=argparse.BooleanOptionalAction, default=None,
                   help=steps_only_help)
    s.set_defaults(fn="task type update")
    s = types.add_parser("delete")
    s.add_argument("id")
    s.set_defaults(fn="task type delete")
    s = task.add_parser("types", help="list or manage task types and steps")
    s.add_argument("id", nargs="?", help="type id or name")
    s.add_argument("--name", help="create a type, or rename the selected type")
    s.add_argument("--steps-file", dest="steps_file", help="JSON array of steps; keep ids when editing")
    s.add_argument("--delete", action="store_true", help="delete an unused type")
    s.set_defaults(fn="task types")
    s = task.add_parser("ask")
    s.add_argument("id")
    s.add_argument("text")
    s.set_defaults(fn="task ask")
    s = task.add_parser("question", help="put one decision to a person as its own task: a pick or Yes / No")
    s.add_argument("question")
    s.add_argument("--on", required=True, help="the person who decides")
    s.add_argument("--option", action="append",
                   help="\"LABEL -> WHAT HAPPENS\" or \"LABEL=FILE -> WHAT HAPPENS\" (an image shows with it); repeat, 2 to 6; "
                        "none for Yes / No. The what-happens line is required")
    s.add_argument("--why", help="one plain line on why it matters, shown under the question")
    s.add_argument("--multi", action="store_true", default=None,
                   help="several picks: the person ticks the ones they want; unticked ones come back as \"not this one\"")
    s.add_argument("--recommend", action="append", metavar="LABEL", help="an option you recommend (pre-ticked); repeat")
    s.add_argument("--title")
    s.add_argument("--body")
    s.add_argument("--parent")
    s.add_argument("--topic", help="a short key for what this decides (backup-model): your open question to them "
                                   "on it is updated instead of a second one opened")
    s.add_argument("--private", action="store_true", default=None, help="only you and the person may read it")
    s.add_argument("--if-no-answer", dest="if_no_answer",
                   help="what you will do if they don't answer: an option (\"Yes\") or one plain sentence")
    s.add_argument("--default-kind", dest="default_kind", choices=["reversible", "send", "spend", "delete"],
                   help="what that step is, required with --if-no-answer; only reversible (internal, undoable) goes "
                        "ahead on its own: a send, spending or a delete waits for an answer")
    s.add_argument("--by", help="ISO-8601 date-time: go ahead then, instead of after the team's days (default 3)")
    s.add_argument("--review", action="append", metavar="PATH|FILE[@N]",
                   help="a file version for them to review, shown on the card: a path here (published first, as "
                        "hub file publish does) or a file id; repeat. Without --option: Approve / Request changes")
    s.add_argument("--pick", action="store_true", default=None,
                   help="each --review file is its own option (2 to 5), plus Request changes")
    s.add_argument("--spend", metavar="LABEL=AMOUNT:WHAT",
                   help="picking LABEL also approves spending AMOUNT on WHAT: a spend approval you consume once")
    s.add_argument("--spend-account", dest="spend_account", help="the account --spend comes from (default gateway)")
    s.set_defaults(fn="task question")
    s = task.add_parser("update", description="Move your task; to reopen, set doing or open; the requester closes. "
                        "Putting it back to open requires a note. Automatic starts wait 60 minutes unless it gets "
                        "a comment, a new link or a blocker finishes.")
    s.add_argument("--visibility", choices=['public', 'private'])
    s.add_argument("--public-title", dest="public_title")
    s.add_argument("--public-summary", dest="public_summary")
    s.add_argument("--approval-id", dest="approval_id")
    s.add_argument("--size", type=float)
    visibility = s.add_mutually_exclusive_group()
    visibility.add_argument("--private", dest="private", action="store_true", default=None)
    visibility.add_argument("--company", dest="private", action="store_false", help="human requester publishes the task")
    s.add_argument("id")
    s.add_argument("--title", help="a new title, checked as a new task's title would be")
    s.add_argument("--status", choices=list(TASK_UPDATE_STATUSES), type=task_update_status)
    s.add_argument("--note")
    s.add_argument("--owner")
    s.add_argument("--due")
    s.add_argument("--on", dest="waiting_on",
                   help="with --status waiting: the person it waits on, so it is in their Needs you; '' clears it. "
                        "--note is then your ask to them: one or two plain sentences saying what you need and what "
                        "happens after, at most 40 words, with no ids, hashes, file paths or tool names (\"PR #1555\" is "
                        "fine). Or bot:<slug>, an active bot that owns the first open task this one is blocked by "
                        "(hub task wait <id> --on task:<its task>); --note is then the reason shown beside its name")
    s.add_argument("--goal", help='the goal this task serves; "" takes it off')
    s.add_argument("--brief", action="store_true", help="keep the note on the task; chat hears only the title and status")
    s.add_argument("--quiet", dest="brief", action="store_true", help=argparse.SUPPRESS)     # brief's older name
    s.add_argument("--type", help="task type id or name")
    s.add_argument("--step", help="step id or name; sets status; an empty string clears it")
    s.add_argument("--step-rank", dest="step_rank", type=float, help="its place within its step, lower first")
    s.add_argument("--number", type=int, help="movers: the number of a task that has none")
    s.add_argument("--role", action="append", help="who is on it by role, NAME=person,person (repeat for more roles; NAME= clears one)")
    s.add_argument("--verdict", choices=["approve", "changes", "pass", "fail"],
                   help="a review or QA subtask's verdict; the task that asked for it is woken with it")
    s.add_argument("--head", dest="verdict_head", help="the pull request head (commit sha) the verdict judged")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task update")
    s = task.add_parser("done", help="finish a task you own with its result; a review or QA subtask adds its --verdict "
                                     "and the --head it judged")
    s.add_argument("id")
    s.add_argument("--note", help="the result, in a few plain sentences")
    s.add_argument("--verdict", choices=["approve", "changes", "pass", "fail"])
    s.add_argument("--head", dest="verdict_head", help="the pull request head (commit sha) the verdict judged")
    s.add_argument("--brief", action="store_true", help="keep the note on the task; chat hears only the title and status")
    s.add_argument("--quiet", dest="brief", action="store_true", help=argparse.SUPPRESS)     # brief's older name
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task done")
    s = task.add_parser("comment", help="leave a comment on a task, on the record with your name")
    s.add_argument("--public", action="store_true", default=None)
    s.add_argument("--approval-id", dest="approval_id")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--attach", action="append", help="file to attach; repeat for more files")
    s.add_argument("--file", action="append", metavar="ID@VERSION",
                   help="a file already in Tico: one on this task, or (a person) a chat file you can open, as ID@1")
    s.add_argument("--visual", action="append", metavar="PATH",
                   help="a picture, SVG, HTML page, video or audio to show with it (a file in your checkout, or ID@VERSION "
                        "already in Tico); repeat for more")
    s.add_argument("--title", action="append", dest="visual_title", metavar="TITLE",
                   help="the caption for each --visual, in the same order")
    choices = s.add_mutually_exclusive_group()
    choices.add_argument("--ask", help="JSON file containing structured questions")
    choices.add_argument("--choices", help="comma-separated options, each \"LABEL -> WHAT HAPPENS\" (a question to a "
                                           "person needs the what-happens line)")
    delivery_flags(s, goal=True)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.add_argument("--needs-action", dest="needs_action", action="store_true", help="a bot's message to a bot waits for that bot's next run; this wakes it now")
    s.set_defaults(fn="task comment")
    s = task.add_parser("comment-moderate", help="hide, show again or delete a public comment on a task you manage")
    s.add_argument("id")
    s.add_argument("comment_id")
    s.add_argument("action", choices=["hide", "show", "delete"])
    s.set_defaults(fn="task comment-moderate")
    s = task.add_parser("requesters", help="a task's requesters: internal contacts and counts, never tokens")
    s.add_argument("id")
    s.add_argument("--limit", type=int)
    s.add_argument("--offset", type=int)
    s.set_defaults(fn="task requesters")
    s = task.add_parser("requester-save", help="add a requester to a task, or update one with --requester-id; nothing is sent")
    s.add_argument("id")
    s.add_argument("--requester-id", dest="requester_id", help="the requester to update; omit to add one")
    s.add_argument("--actor", help="a teammate (verified identity); omit for someone outside")
    for field in ("name", "email", "phone", "other", "organization", "note"):
        s.add_argument("--" + field)
    s.add_argument("--source", choices=["public_form", "inbox", "meeting", "manual"])
    s.add_argument("--important", action=argparse.BooleanOptionalAction, default=None)
    s.set_defaults(fn="task requester-save")
    s = task.add_parser("requester-remove", help="take a requester off a task")
    s.add_argument("id")
    s.add_argument("requester_id")
    s.set_defaults(fn="task requester-remove")
    s = task.add_parser("publication-preview", help="the exact payload a publish approval needs for public text, "
                                                    "a label change or a public reply")
    s.add_argument("id")
    s.add_argument("--visibility", choices=["public", "private"])
    s.add_argument("--public-title", dest="public_title")
    s.add_argument("--public-summary", dest="public_summary")
    s.add_argument("--label", dest="labels", action="append", help="the task's labels after the change; repeat for more")
    s.add_argument("--comment", help="the public reply's text")
    s.set_defaults(fn="task publication-preview")
    s = task.add_parser("comment-edit", help="change the text of a comment you wrote; it wakes nobody")
    s.add_argument("--approval-id", dest="approval_id")
    s.add_argument("id")
    s.add_argument("comment_id", help="the comment's id (its id in the task's comments)")
    s.add_argument("text")
    s.set_defaults(fn="task comment-edit")
    s = task.add_parser("comment-delete", help="take back a comment you wrote")
    s.add_argument("id")
    s.add_argument("comment_id", help="the comment's id (its id in the task's comments)")
    s.set_defaults(fn="task comment-delete")
    s = task.add_parser("ask-resolve", help="settle an open question the person answered elsewhere (--quote their "
                                            "words), or withdraw one you asked")
    s.add_argument("id")
    s.add_argument("comment_id", help="the question's id (its id in the task's comments)")
    s.add_argument("--quote", help="the person's answer in their own words")
    s.set_defaults(fn="task ask-resolve")
    s = task.add_parser("delete", help="delete a task made by mistake: its requester or a mover, never a bot")
    s.add_argument("id")
    s.set_defaults(fn="task delete")
    s = task.add_parser("deleted", help="deleted tasks you may restore, newest first")
    s.set_defaults(fn="task deleted")
    s = task.add_parser("restore", help="put a deleted task back: whoever deleted it, its requester or a mover")
    s.add_argument("id")
    s.set_defaults(fn="task restore")
    s = task.add_parser("link", help="attach a link: the pull request you opened, an issue, a document, a session")
    s.add_argument("id")
    s.add_argument("url", help="the URL; with --session also a resume command or a reference")
    s.add_argument("--title")
    s.add_argument("--session", action="store_true",
                   help="another session working this task, tracked only: task chat goes to the owning bot")
    s.add_argument("--wake-once", action=argparse.BooleanOptionalAction, default=None,
                   help="a pull request: wake this task once when its checks finish (pass or fail), it is approved or "
                        "gets changes requested, or a release ships it; never changes the task's status. "
                        "--no-wake-once disarms it")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task link")
    s = task.add_parser("relate", help="relate another task to this one: <id> <kind> <task>")
    s.add_argument("id")
    s.add_argument("task")
    s.add_argument("--kind", default="related", choices=["parent", "blocks", "blocked_by", "related", "duplicate_of", "follow_up"],
                   help="parent: task is its parent; blocks / blocked_by; related (default); duplicate_of; "
                        "follow_up: it was split off or followed up from task")
    s.add_argument("--remove", action="store_true", help="take it off instead")
    s.set_defaults(fn="task relate")
    s = task.add_parser("wait", help="make a task wait on another task, a pull request merging or a release being "
                                     "published; prints what was stored")
    s.add_argument("id")
    s.add_argument("--on", dest="wait_on", required=True,
                   help="task:<id> (blocked_by), pr:<url> or pr:<task> (until that pull request merges), "
                        "release:<tag> (until that release is published). The task it waits on keeps its own step.")
    s.add_argument("--note", help="what to check when the pr: or release: wait fires, in the wake; one or two sentences")
    s.add_argument("--remove", action="store_true", help="take the wait off instead")
    s.set_defaults(fn="task wait")
    s = task.add_parser("label", help="add or remove labels on a task")
    s.add_argument("id")
    s.add_argument("--add", action="append")
    s.add_argument("--remove", action="append")
    s.add_argument("--approval-id", dest="approval_id", help="an approved publish approval, for a public roadmap label")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task label")
    s = task.add_parser("close")
    s.add_argument("id")
    s.add_argument("--note")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task close")
    s = task.add_parser("bulk", help="one change to every task a filter selects (at most 200): step, status, owner, "
                                     "tags or close; --dry-run counts first. Each task changes as its own update would")
    s.add_argument("action", choices=["step", "status", "owner", "tags", "close"])
    s.add_argument("value", nargs="?", help="the step, status or owner to set (none for tags or close)")
    s.add_argument("--add", action="append", help="with tags: a tag to add; repeat for more")
    s.add_argument("--remove", action="append", help="with tags: a tag to take off; repeat for more")
    s.add_argument("--note", help="what changed, or why; goes with each task's change")
    s.add_argument("--dry-run", dest="dry_run", action="store_true", help="count what would change; change nothing")
    for name, word in (("owner", "owner (a bot slug, a human id or me)"), ("requester", "requester"),
                       ("label", "tag"), ("type", "type (id or name)"), ("step", "step (id or name)"),
                       ("updated-since", "changed after this ISO-8601 time"), ("member", "person or bot on it by role"),
                       ("role", "role, with --where-member")):
        s.add_argument("--where-" + name, dest="where_" + name.replace("-", "_"), help="only tasks with this " + word)
    s.add_argument("--where-status", dest="where_status", action="append", choices=list(TASK_STATUSES),
                   help="only tasks with this status; repeat for more")
    s.add_argument("--where-lane", dest="where_lane", choices=["company", "product"])
    s.add_argument("--where-number", dest="where_number", type=int, help="only this task number")
    s.add_argument("--where-snoozed", dest="where_snoozed", choices=["hide", "only", "all"])
    s.add_argument("--where-id", dest="where_ids", action="append", help="only this task (id or number); repeat for more")
    s.set_defaults(fn="task bulk")
    s = task.add_parser("attach", help="attach a file to a task; prints the link for your note")
    s.add_argument("id")
    s.add_argument("file")
    s.add_argument("--name", help="the name people see; defaults to the file's own")
    s.add_argument("--poster", help="a PNG or JPEG preview")
    s.add_argument("--note", help="short note on this version (at most 500 characters)")
    choices = s.add_mutually_exclusive_group()
    choices.add_argument("--ask", help="JSON file containing structured questions")
    choices.add_argument("--choices", help="comma-separated options, each \"LABEL -> WHAT HAPPENS\" (a question to a "
                                           "person needs the what-happens line)")
    s.set_defaults(fn="task attach")
    s = task.add_parser("answers", help="list the task's structured answers, oldest first")
    s.add_argument("id")
    s.set_defaults(fn="task answers")
    s = task.add_parser("list")
    s.add_argument("--owner")
    s.add_argument("--requester")
    s.add_argument("--status", action="append", choices=list(TASK_STATUSES))
    s.add_argument("--lane", choices=["company", "product"])
    s.add_argument("--label")
    s.add_argument("--type", help="only tasks of this type (id or name)")
    s.add_argument("--step", help="only tasks in this step (id or name)")
    s.add_argument("--sort", choices=["queue", "finished", "step"], help="step: in step order, then each one's place in it")
    s.add_argument("--number", type=int, help="only this task number")
    s.add_argument("--updated-since", help="only tasks changed after this ISO-8601 time with a timezone")
    s.add_argument("--member", help="only tasks this person or bot is on in some role (me, a human id, a bot slug)")
    s.add_argument("--role", help="with --member: only tasks they are on in this role")
    s.add_argument("--brief", action="store_true", help="leave out bodies and acceptance criteria")
    s.add_argument("--all", action="store_true", help="the board: every task and every bot you may see, as {tasks, bots}")
    s.add_argument("--stuck", action="store_true",
                   help="only open work untouched for --hours that waits on nobody (BotOps's sweep)")
    s.add_argument("--hours", type=int, default=24, help="with --stuck: untouched for at least this many hours")
    s.add_argument("--snoozed", choices=["only", "all"],
                   help="snoozed tasks are left out: only lists just them, all lists them with the rest")
    s.set_defaults(fn="task list")
    s = task.add_parser("show", help="one task with its history and conversation; several ids: their fields in one read")
    s.add_argument("id", nargs="+")
    s.set_defaults(fn="task show")
    s = task.add_parser("run", help="start a bot's task now, as the task")
    s.add_argument("id")
    s.set_defaults(fn="task run")
    s = task.add_parser("model", help="the model this task's runs use, with its effort and harness; --clear puts it "
                                      "back on the bot's task model")
    s.add_argument("id")
    s.add_argument("model", nargs="?", help="a model id or name; leave out to change only --effort or --harness")
    s.add_argument("--effort")
    s.add_argument("--harness")
    s.add_argument("--clear", action="store_true",
                   help="clear the task's own model: its runs use the bot's task model again")
    s.set_defaults(fn="task model")
    s = task.add_parser("snooze", help="set a task aside until a date: off the lists, Needs you and stall wakes "
                        "until then, when its owner bot is woken once")
    s.add_argument("id")
    s.add_argument("--until", required=True,
                   help="ISO-8601 date (the start of that day, Pacific) or date-time with a zone")
    s.add_argument("--note", help="what to check when the snooze ends, in the owner's wake: what you are waiting for and "
                                  "where to look (at most 500 characters)")
    s.set_defaults(fn="task snooze")
    # `defer` is its older name: a date makes it a snooze-like hold, not a delivery (backend/delivery.py).
    s = task.add_parser("later", aliases=["defer"],
                        help="a deferred task carried into this run that you will not do now: say when it is "
                        "picked up again (it gets one run of its own then); with none, your next run is handed it again")
    s.add_argument("id")
    when = s.add_mutually_exclusive_group(required=True)
    when.add_argument("--after", help="when this task is done or closed (an id or number)")
    when.add_argument("--at", help="at this time: ISO-8601 date-time with a zone, or a date (start of day, Pacific)")
    when.add_argument("--next-run", action="store_true", help="with your next run, whatever starts it")
    when.add_argument("--handled", action="store_true", help="this run is done with it for now (nothing to resume)")
    s.set_defaults(fn="task later")
    s = task.add_parser("unsnooze", help="bring a snoozed task back now; its owner bot is woken once")
    s.add_argument("id")
    s.set_defaults(fn="task unsnooze")
    s = task.add_parser("waive-qa", help="a person waives a Dev ticket's current In QA step for one exact pull "
                        "request head, with a reason; recorded as a waiver, never as a QA pass")
    s.add_argument("id")
    s.add_argument("--head", required=True, help="the pull request's current head: the full 40-character sha")
    s.add_argument("--reason", required=True, help="why QA is waived, in plain words")
    s.set_defaults(fn="task waive-qa")

    replay = sub.add_parser("replay", help="what happened in a window, as you may see it").add_subparsers(dest="sub")
    s = replay.add_parser("show", help="what happened in a window, in time order, as you may see it (JSON)")
    s.add_argument("--from", dest="from", help="start, ISO 8601 with a zone (default 24 hours before --to)")
    s.add_argument("--to", help="end (default now); at most 31 days after --from")
    scope = s.add_mutually_exclusive_group()
    scope.add_argument("--bot", help="only what involves this bot")
    scope.add_argument("--person", help="only what involves this person")
    scope.add_argument("--team", help="only what involves this team")
    s.set_defaults(fn="replay show")
    goal = sub.add_parser("goal", help="goals: list, read-back, create and edit",
                          description="Read back goal changes in the owner's Strategy Bot chat. "
                                      "Saves nothing without a plain yes; "
                                      "cite that yes with --on-behalf-of turn (or its message id) on each save.").add_subparsers(dest="sub")
    s = goal.add_parser("read-back", help="Tico writes the draft; saves nothing without a plain yes",
                        description="Post 1 to 20 changes as Tico's read-back. Saves nothing without a plain yes. "
                                    "Returns message_id, text and exact method/path/body_json steps. After yes, use goal save "
                                    "for each agreed step; replace {new} with the first save's goal id using --created-goal. "
                                    "On not_agreed read back again; never retry the save.")
    s.add_argument("changes", help="JSON array: kind goal_create, goal_update, measure_add, target_set, measure_remove or suggestion, "
                                  "and that change's fields")
    s.set_defaults(fn="goal read-back")
    s = goal.add_parser("save", help="save one exact read-back step after the owner's plain yes")
    s.add_argument("method", choices=["POST"])
    s.add_argument("path", help="the step's /api/v2 path")
    s.add_argument("body_json", help="the step's exact body_json string, unchanged")
    s.add_argument("--created-goal", dest="created_goal", help="first save's goal id, to replace {new} in later paths")
    s.add_argument("--on-behalf-of", required=True, help="turn or the message id of the owner's plain yes")
    s.set_defaults(fn="goal save")
    s = goal.add_parser("list", help="what you are for: your goals, the chain above them, your reports' goals")
    s.add_argument("--owner", help="someone else's: a bot slug or a person id")
    s.add_argument("--all", action="store_true", help="every live goal in the company")
    s.add_argument("--status", help="with --all: only these, comma-separated (red,yellow,green,gray,done,dropped)")
    s.set_defaults(fn="goal list")
    s = goal.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn="goal show")
    s = goal.add_parser("create", help="set a goal; a parent is optional, and it starts with no colour")
    s.add_argument("--owner", required=True, help="me, a bot slug, or a person id (company: a company goal, the owner only)")
    s.add_argument("--title", required=True)
    s.add_argument("--parent", help="the goal this one supports, if any; not needed")
    s.add_argument("--body", default="")
    s.add_argument("--body-file", dest="body_file")
    s.add_argument("--top", action="store_true", help="put it first in the owner's order")
    s.add_argument("--bet", dest="bet_id", help="the strategy bet it serves (hub strategy show lists them)")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help="turn or the message id of the owner's plain yes to a goal read-back")
    s.set_defaults(fn="goal create")
    s = goal.add_parser("status", help="set the colour by hand: red, yellow or green with one sentence, done or dropped "
                                     "when it ends; it sticks until a person hands it back (status auto)")
    s.add_argument("id")
    s.add_argument("status", choices=list(GOAL_STATUSES) + ["auto"])
    s.add_argument("note", nargs="?", default="")
    s.set_defaults(fn="goal status")
    s = goal.add_parser("update")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--body")
    s.add_argument("--body-file", dest="body_file")
    s.add_argument("--parent", help='the goal it supports; "" unlinks it')
    s.add_argument("--owner")
    s.add_argument("--rank", type=int)
    s.add_argument("--top", action="store_true")
    s.add_argument("--bet", dest="bet_id", help='the strategy bet it serves; "" unlinks it')
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help="turn or the message id of the owner's plain yes to a goal read-back")
    s.set_defaults(fn="goal update")
    s = goal.add_parser("refresh", help="the Strategy Bot's status pass: work automatic colours out again")
    s.add_argument("--goal", action="append", dest="goal_ids", help="only this goal; repeatable")
    s.set_defaults(fn="goal refresh")
    s = goal.add_parser("checkin", help="how the owner says the goal is going, in their words")
    s.add_argument("id")
    s.add_argument("body")
    s.add_argument("--signal", choices=["on_track", "at_risk", "off_track"])
    s.add_argument("--from", dest="from_actor", help="whose words these are, when you record them for someone")
    s.add_argument("--kpi", dest="kpi_id", help="the KPI that prompted it")
    s.set_defaults(fn="goal checkin")
    s = goal.add_parser("checkin-list", help="a goal's check-ins, newest first")
    s.add_argument("id")
    s.set_defaults(fn="goal checkin-list")
    s = goal.add_parser("needs-you", help="red KPIs on your goals, stale KPIs you own, proposals to confirm")
    s.set_defaults(fn="goal needs-you")
    s = goal.add_parser("company", help="the company's Vision and Description (read-only)")
    s.set_defaults(fn="goal company")
    s = goal.add_parser("strategy-bot", help="the Strategy Bot: its level, "
                                             "regular checks and today's Needs you count per person")
    s.set_defaults(fn="goal strategy-bot")
    s = goal.add_parser("strategy-bot-merge", help="the Strategy Bot at level 2+: "
                                                   "one item for several bots' same ask of a person")
    s.add_argument("person", help="the person they all wait on (a person id)")
    s.add_argument("title")
    s.add_argument("question", help="the one question that answers them all")
    s.add_argument("tasks", nargs="+", help="2 to 10 task ids waiting on that person")
    s.set_defaults(fn="goal strategy-bot-merge")
    s = goal.add_parser("strategy-bot-level3-offer", help="the Strategy Bot: "
                                                          "record the level-3 offer its Monday note made")
    s.set_defaults(fn="goal strategy-bot-level3-offer")
    s = goal.add_parser("strategy-bot-decide", help="the Strategy Bot at level 3: "
                                                    "one decision alone on bot tasks, with Undo")
    s.add_argument("reason", help="why, in one sentence the owner reads in the log")
    s.add_argument("changes", help='a JSON list of 1 to 10 changes: {"do": "create", "owner": "<bot>", "title": "..."}, '
                                   '{"do": "reorder"|"close", "task_id": "..."}, {"do": "reassign", "task_id": "...", '
                                   '"owner": "<bot>"}')
    s.add_argument("--goal-id", dest="goal_id", help="the goal this serves")
    s.set_defaults(fn="goal strategy-bot-decide")
    s = goal.add_parser("strategy-bot-log", help="the Strategy Bot log (the owner only): "
                                                 "what it decided alone and what it "
                                            "proposed instead")
    s.add_argument("--before", help="next_before from the previous page")
    s.set_defaults(fn="goal strategy-bot-log")
    def target_flags(s):
        s.add_argument("--baseline", type=float, help="improvement: where it starts (default the latest reading)")
        s.add_argument("--target", type=float, help="improvement: the value to reach; needs --deadline")
        s.add_argument("--deadline", help="improvement: when the target is due, YYYY-MM-DD")
        s.add_argument("--min", type=float, help="range: the lowest acceptable value")
        s.add_argument("--max", type=float, help="range: the highest acceptable value")

    def kpi_flags(s):
        s.add_argument("--definition", help="what exactly is counted, in a sentence")
        s.add_argument("--unit")
        s.add_argument("--direction", choices=["up", "down", "range"], help="which way is good")
        s.add_argument("--cadence", choices=["daily", "weekly", "monthly"], help="how often it is read")
        s.add_argument("--source-note", dest="source_note", help="where the number comes from")

    kpi = sub.add_parser("kpi", help="a measure on its own that goals link to, and its readings").add_subparsers(dest="sub")
    s = kpi.add_parser("list", help="KPIs with latest reading and colour")
    s.add_argument("--goal", dest="goal_id", help="only the KPIs this goal uses")
    s.add_argument("--owner", help="only this owner's: me, a bot slug or a person id")
    s.add_argument("--unlinked", action="store_true", help="only the KPIs no goal uses")
    s.add_argument("--bot", help="a bot slug: its five automatic KPIs")
    s.add_argument("--archived", dest="include_archived", action="store_true",
                    help="include archived KPIs for historical review")
    s.set_defaults(fn="kpi list")
    s = kpi.add_parser("show", help="definition and versions, the goals using it, every reading, check-ins")
    s.add_argument("id")
    s.add_argument("--effective", action="store_true", help="leave out readings a correction replaced")
    s.set_defaults(fn="kpi show")
    for action in ("archive", "restore"):
        s = kpi.add_parser(action, help=f"{action} a KPI while keeping its definitions, links and readings")
        s.add_argument("id")
        s.set_defaults(fn="kpi " + action)
    s = kpi.add_parser("create", help="make a KPI; with --goal it is linked, the target on the link")
    s.add_argument("name", help="what is counted, per what: 'booked demos per two weeks'")
    s.add_argument("--goal", dest="goal_id", help="link it to this goal")
    s.add_argument("--owner", help="me (default), company, a bot slug or a person id")
    kpi_flags(s)
    target_flags(s)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help="turn or the message id of the owner's plain yes to a goal read-back")
    s.set_defaults(fn="kpi create")
    s = kpi.add_parser("update", help="a change to what it measures is a new definition version")
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--owner")
    kpi_flags(s)
    s.add_argument("--parent", dest="parent_kpi_id", metavar="ID", help='the KPI it feeds in the growth tree; "" clears')
    s.add_argument("--role", dest="growth_role", choices=["north_star", "input", "guardrail", ""],
                   help="one North Star at a time: naming a new one moves the role")
    s.add_argument("--stage", choices=["reach", "acquire", "activate", "retain", "refer", ""])
    s.add_argument("--loop", metavar="NAME:STEP", help='its place in a growth loop, referral:2; "" clears')
    s.set_defaults(fn="kpi update")
    s = kpi.add_parser("link", help="link a goal to a KPI, or change the target on the link")
    s.add_argument("goal_id")
    s.add_argument("kpi_id")
    target_flags(s)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help="turn or the message id of the owner's plain yes to a goal read-back")
    s.set_defaults(fn="kpi link")
    s = kpi.add_parser("target", help="set or remove the target on a goal's measure")
    s.add_argument("goal_id")
    s.add_argument("kpi_id")
    target_flags(s)
    s.add_argument("--on-behalf-of", help="turn or the message id of the owner's plain yes")
    s.set_defaults(fn="kpi target")
    s = kpi.add_parser("unlink", help="take a KPI off a goal")
    s.add_argument("goal_id")
    s.add_argument("kpi_id")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help="turn or the message id of the owner's plain yes to a goal read-back")
    s.set_defaults(fn="kpi unlink")
    s = kpi.add_parser("log", help="a reading: a fact with its period; to correct one, supersede it")
    s.add_argument("kpi_id")
    s.add_argument("value", type=float)
    s.add_argument("note", nargs="?", default="")
    s.add_argument("--period-start", dest="period_start", help="start of the period it describes")
    s.add_argument("--period-end", dest="period_end", help="end of the period it describes; default now")
    s.add_argument("--at", help="the old name of --period-end")
    s.add_argument("--collected-at", dest="collected_at", help="when it was collected; default now")
    s.add_argument("--evidence", help="a link or a note that shows where the value came from")
    s.add_argument("--quality", choices=["measured", "estimate", "partial"])
    s.add_argument("--estimate", action="store_true", help="the same as --quality estimate")
    s.add_argument("--source", help="a connector or system name (posthog, close)")
    s.add_argument("--definition-version", dest="definition_version", type=int)
    s.add_argument("--supersedes", help="the id of the reading this one corrects")
    s.set_defaults(fn="kpi log")

    proposal = sub.add_parser("proposal", help="a change you may not make yourself, for the owner to confirm").add_subparsers(dest="sub")
    s = proposal.add_parser("create")
    s.add_argument("--kind", required=True, choices=["goal_wording", "goal_kpi", "kpi_definition", "kpi_target", "flag", "goal_why",
                                                     "recovery", "strategy_draft", "brand_draft", "gtm_draft"])
    s.add_argument("--goal", dest="goal_id")
    s.add_argument("--kpi", dest="kpi_id")
    s.add_argument("--payload", help="the proposed change, as JSON")
    s.add_argument("--payload-file", dest="payload_file")
    s.add_argument("--reason", default="")
    s.set_defaults(fn="proposal create")
    s = proposal.add_parser("list")
    s.add_argument("--status", choices=["pending", "confirmed", "rejected", "all"], default="pending")
    s.add_argument("--goal", dest="goal_id")
    s.add_argument("--kpi", dest="kpi_id")
    s.set_defaults(fn="proposal list")
    s = proposal.add_parser("decide", help="a person's own decision; a bot is refused")
    s.add_argument("id")
    s.add_argument("decision", choices=["confirm", "reject"])
    s.add_argument("--note", default="")
    s.add_argument("--option", type=int, help="a recovery: approve this option (0 is the first) instead of the pick")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help="turn or the message id of the owner's plain yes to a goal read-back")
    s.set_defaults(fn="proposal decide")

    rules = sub.add_parser("rules", help="org rules: what teammates follow (internal) and what someone outside requires "
                                         "(external), and their due dates").add_subparsers(dest="sub")
    s = rules.add_parser("list", help="the rules in force; --mine: your bot's rules and its settings as sentences")
    s.add_argument("--kind", choices=["internal", "external"])
    s.add_argument("--status", choices=["suggested", "active", "retired", "any"])
    s.add_argument("--who", help="a group, person or bot the rule's Who names")
    s.add_argument("--with", dest="with", help="a group, person or bot the rule's With names")
    s.add_argument("--source", help="a From, by name or id")
    s.add_argument("--bot", help="the rules that apply to this bot")
    s.add_argument("--mine", action="store_true", help="the rules that apply to your bot, and its settings")
    s.add_argument("--cursor")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="rules list")
    s = rules.add_parser("read", help="one rule, with its due dates and the quote it cites; with --from, add a document "
                                      "for Counsel to read (a file id or a link)")
    s.add_argument("id", help="a rule id, or with --from a file id or a link")
    s.add_argument("--from", dest="source", metavar="SOURCE", help="document: the From, by name or id")
    s.add_argument("--kind", choices=["vendor", "customer", "government", "other"], help="document: the kind, for a new From")
    s.add_argument("--note", help="document: what the document is for")
    s.set_defaults(fn="rules read")
    s = rules.add_parser("ask", help="a can-I question: the rules that apply, cited, and Counsel's answer on a task")
    s.add_argument("question")
    s.set_defaults(fn="rules ask")
    s = rules.add_parser("suggested", help="the suggestions waiting for a person to accept or skip")
    s.add_argument("--cursor")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="rules suggested")
    s = rules.add_parser("add", help="add a rule: a bot's is a suggestion; only owners and admins add Everyone")
    s.add_argument("--kind", required=True, choices=["internal", "external"])
    s.add_argument("--text", required=True, help="the rule in one sentence")
    s.add_argument("--description")
    s.add_argument("--who", dest="who_names", action="append", default=[], metavar="NAME",
                   help="a group, person or bot by id or name; repeatable")
    s.add_argument("--everyone", action="store_true")
    s.add_argument("--all-bots", dest="all_bots", action="store_true")
    s.add_argument("--with", dest="with_names", action="append", default=[], metavar="NAME",
                   help="internal: a group, person or bot the rule is about; repeatable")
    s.add_argument("--where", action="append", default=None, metavar="TERM",
                   help="where it applies: thread:ID, team:GROUP, kind:dm or has_outside_members; repeatable")
    s.add_argument("--from", dest="source_id", metavar="SOURCE", help="external: the From, by name or id")
    s.add_argument("--file", dest="file_id", metavar="FILE_ID", help="external: the document the rule rests on")
    s.add_argument("--section", help="external: the section of the document")
    s.add_argument("--quote", help="external: the words of the document the rule rests on (must be in the file)")
    s.add_argument("--version", dest="file_version", type=int, metavar="N",
                   help="external: the version of --file the quote is in (Counsel passes the one its task names)")
    s.add_argument("--if-broken", dest="if_broken", choices=["block", "escalate", "warn"])
    s.add_argument("--reviewer")
    s.set_defaults(fn="rules add")
    s = rules.add_parser("edit", help="change a rule, only the fields given, against the version you read")
    s.add_argument("id")
    s.add_argument("--version", type=int, required=True)
    s.add_argument("--text")
    s.add_argument("--description")
    s.add_argument("--who", dest="who_names", action="append", default=[], metavar="NAME")
    s.add_argument("--everyone", action="store_true")
    s.add_argument("--all-bots", dest="all_bots", action="store_true")
    s.add_argument("--with", dest="with_names", action="append", default=[], metavar="NAME")
    s.add_argument("--where", action="append", default=None, metavar="TERM",
                   help="where it applies (replaces the list): thread:ID, team:GROUP, kind:dm or has_outside_members")
    s.add_argument("--from", dest="source_id", metavar="SOURCE")
    s.add_argument("--if-broken", dest="if_broken", choices=["block", "escalate", "warn"])
    s.add_argument("--reviewer")
    s.set_defaults(fn="rules edit")
    s = rules.add_parser("accept", help="a person accepts a suggestion, optionally changing its Who")
    s.add_argument("id")
    s.add_argument("--version", type=int)
    s.add_argument("--who", dest="who_names", action="append", default=[], metavar="NAME")
    s.add_argument("--everyone", action="store_true")
    s.add_argument("--all-bots", dest="all_bots", action="store_true")
    s.set_defaults(fn="rules accept")
    s = rules.add_parser("skip", help="set a suggestion aside")
    s.add_argument("id")
    s.add_argument("--version", type=int)
    s.set_defaults(fn="rules skip")
    s = rules.add_parser("retire", help="retire a rule in force; its due dates come back")
    s.add_argument("id")
    s.add_argument("--version", type=int)
    s.set_defaults(fn="rules retire")
    due = rules.add_parser("due", help="due dates on rules: the next ones, add one, mark one satisfied")
    # The list's flags work on `due` itself and on `list`, which is the same command. SUPPRESS keeps a flag given on
    # one from being reset by the other's default.
    due.add_argument("--next", default=argparse.SUPPRESS, help="days ahead to list, such as 90d (default 90d)")
    due.add_argument("--status", default=argparse.SUPPRESS, choices=["overdue", "due_soon", "upcoming", "satisfied"])
    due.add_argument("--cursor", default=argparse.SUPPRESS)
    due.add_argument("--limit", type=int, default=argparse.SUPPRESS)
    due.set_defaults(fn="rules due")
    due_sub = due.add_subparsers(dest="subsub")
    s = due_sub.add_parser("list", help="the due dates of rules in force: overdue first, then those due within --next days")
    s.add_argument("--next", default=argparse.SUPPRESS)
    s.add_argument("--status", default=argparse.SUPPRESS, choices=["overdue", "due_soon", "upcoming", "satisfied"])
    s.add_argument("--cursor", default=argparse.SUPPRESS)
    s.add_argument("--limit", type=int, default=argparse.SUPPRESS)
    s.set_defaults(fn="rules due")
    s = due_sub.add_parser("add", help="a due date on a rule: first day, repeat, owner")
    s.add_argument("id", help="the rule's id")
    s.add_argument("--first", dest="first_due", required=True, metavar="YYYY-MM-DD")
    s.add_argument("--repeats", required=True, choices=["once", "weekly", "monthly", "quarterly", "yearly"])
    s.add_argument("--owner", required=True, help="a bot or person, by name or id")
    s.add_argument("--timezone")
    s.add_argument("--task-days", type=int, metavar="N", help="a routine opens the owner's task N days before each one")
    s.set_defaults(fn="rules due set")
    s = due_sub.add_parser("edit", help="change a due date; a new first day or repeat starts a new one, history kept")
    s.add_argument("id", help="the due date's id")
    s.add_argument("--first", dest="first_due", metavar="YYYY-MM-DD")
    s.add_argument("--repeats", choices=["once", "weekly", "monthly", "quarterly", "yearly"])
    s.add_argument("--owner", help="a bot or person, by name or id")
    s.add_argument("--timezone")
    routine = s.add_mutually_exclusive_group()
    routine.add_argument("--task-days", type=int, metavar="N", help="a routine opens the owner's task N days before")
    routine.add_argument("--no-routine", action="store_true", default=None, help="turn the routine off")
    s.set_defaults(fn="rules due edit")
    s = due_sub.add_parser("satisfied", help="mark the current occurrence satisfied")
    s.add_argument("id", help="a due date's id, or its rule's when it has one due date")
    s.add_argument("--on", dest="due_on", metavar="YYYY-MM-DD", help="which occurrence, when two are open")
    s.set_defaults(fn="rules due satisfied")
    s = rules.add_parser("check", help="check a draft against the rules that apply to you, as the write it will be")
    s.add_argument("text", help="the draft, whole")
    s.add_argument("--to", required=True, help="the teammate it goes to")
    s.add_argument("--as", dest="as_", default="message", choices=["message", "comment", "task", "assign"])
    s.set_defaults(fn="rules check")
    s = rules.add_parser("appeal", help="ask the reviewer to look at your held output")
    s.add_argument("id", help="the hold's id, from the held answer")
    s.add_argument("why", nargs="?", default="", help="why you think the rule does not apply")
    s.set_defaults(fn="rules appeal")
    strategy = sub.add_parser("strategy", help="the team's strategy: its bets, the four sections, the versions").add_subparsers(dest="sub")
    s = strategy.add_parser("show", help="the published strategy, its bets and their goals; the owner also gets the draft")
    s.set_defaults(fn="strategy show")
    s = strategy.add_parser("summary", help="the text every run's context carries")
    s.set_defaults(fn="strategy summary")
    s = strategy.add_parser("draft", help="owner only: change the draft; a bot proposes instead (proposal create --kind strategy_draft)")
    s.add_argument("--summary")
    s.add_argument("--vision")
    s.add_argument("--section", action="append", default=[], metavar="KEY=TEXT",
                   help="serve, sell, know, not, or an extra section's key; KEY= clears it; repeatable")
    s.add_argument("--revision", type=int, help="the draft's revision you read; default the current one")
    s.set_defaults(fn="strategy draft")
    s = strategy.add_parser("publish", help="owner only: the draft becomes the next version")
    s.add_argument("--note", default="")
    s.add_argument("--revision", type=int, help="the draft's revision you read; default the current one")
    s.set_defaults(fn="strategy publish")
    s = strategy.add_parser("versions", help="the published versions, newest first; with N, that version whole")
    s.add_argument("n", nargs="?", type=int)
    s.set_defaults(fn="strategy versions")
    s = strategy.add_parser("diff", help="what changed between two published versions")
    s.add_argument("--from", dest="from_n", type=int, required=True)
    s.add_argument("--to", dest="to_n", type=int, required=True)
    s.set_defaults(fn="strategy diff")
    s = strategy.add_parser("guides", help="deprecated: the Strategy Bot's drafting "
                                           "questions; the exercises are lenses")
    s.set_defaults(fn="strategy guides")
    bet = strategy.add_parser("bet", help="owner only: add, change or retire a bet").add_subparsers(dest="subsub")

    def bet_flags(s):
        s.add_argument("--why")
        s.add_argument("--rules-out", dest="rules_out")
        s.add_argument("--wrong-if-kpi", dest="wrong_if_kpi", metavar="ID", help="wrong if this KPI crosses the threshold")
        s.add_argument("--wrong-if-direction", dest="wrong_if_direction", choices=["above", "below"])
        s.add_argument("--wrong-if-threshold", dest="wrong_if_threshold", type=float, metavar="N")
        s.add_argument("--wrong-if-thesis", dest="wrong_if_thesis", metavar="MARKET_ID", help="wrong if this market entity or page changes")
        s.add_argument("--wrong-if-text", dest="wrong_if_text", metavar="TEXT", help="wrong if this turns out true (needs --check-by)")
        s.add_argument("--check-by", dest="check_by", metavar="DATE", help="YYYY-MM-DD: the day to look again")
        s.add_argument("--cite", action="append", dest="cites", metavar="MARKET_ID", help="a market id the bet rests on; repeatable")
        s.add_argument("--status", choices=["proposed", "active", "retired"])
        s.add_argument("--rank", type=int)
    s = bet.add_parser("add")
    s.add_argument("--statement", required=True, help="one sentence that could turn out wrong")
    bet_flags(s)
    s.set_defaults(fn="strategy bet add")
    s = bet.add_parser("update")
    s.add_argument("id")
    s.add_argument("--statement")
    bet_flags(s)
    s.set_defaults(fn="strategy bet update")
    s = bet.add_parser("retire", help="its goals keep their link")
    s.add_argument("id")
    s.set_defaults(fn="strategy bet retire")

    def json_flag(s):
        s.add_argument("--json", action="store_true", help="print the API response as JSON")
        return s

    brand_sections = ("name", "positioning", "voice", "tone", "words", "naming", "visual", "claims", "examples")
    gtm_sections = ("motion", "buyer", "playbooks")
    for kind, title, sections in (("brand", "the brand: voice, words, names, claims", brand_sections),
                                  ("gtm", "the go-to-market: motion, buyer, playbooks", gtm_sections)):
        page = sub.add_parser(kind, help=title).add_subparsers(dest="sub")
        s = json_flag(page.add_parser("show", help="the published page; owner and admins also get the draft"))
        s.add_argument("--section", choices=sections, help="only this section")
        s.add_argument("--version", type=int, metavar="N", help="a published version; default the latest")
        s.set_defaults(fn=f"{kind} show")
        s = json_flag(page.add_parser("summary", help="the text a growth-facing bot's turn carries"))
        s.set_defaults(fn=f"{kind} summary")
        if kind == "brand":
            s = json_flag(page.add_parser("check", help="check a draft against the published brand; - reads stdin"))
            s.add_argument("file", help="a file holding the draft, or - for stdin")
            s.set_defaults(fn="brand check")
        else:
            s = json_flag(page.add_parser("sources", help="the source of truth for each number, and conflicts"))
            s.add_argument("--retired", dest="include_retired", action="store_true", default=None)
            s.set_defaults(fn="gtm sources")
        s = json_flag(page.add_parser("draft", help=f"owner or admin: change one section of the draft; a bot proposes "
                                                    f"instead (proposal create --kind {kind}_draft)"))
        s.add_argument("--section", required=True, choices=sections)
        s.add_argument("--body", required=True, help="the section's value as JSON; null removes it")
        s.add_argument("--revision", type=int, help="the draft's revision you read; stale is refused")
        s.set_defaults(fn=f"{kind} draft")
        s = json_flag(page.add_parser("publish", help="owner or admin: the draft becomes the next version"))
        s.add_argument("--note", default="")
        s.set_defaults(fn=f"{kind} publish")

    growth = sub.add_parser("growth", help="growth: segments, channels, sources, ideas and tests, cohorts, unit economics").add_subparsers(dest="sub")
    segment = growth.add_parser("segment", help="who the company sells to").add_subparsers(dest="subsub")

    def segment_flags(s):
        s.add_argument("--status", choices=["serving", "testing", "next", "dropped"],
                       help="serving and next are Us and Next on the Strategy page; a bot adds testing ones")
        s.add_argument("--motion", choices=["self_serve", "sales_led", "partner", "marketplace", "local", "community",
                                            "ecommerce"])
        s.add_argument("--who-buys", dest="who_buys")
        s.add_argument("--deal-size", dest="deal_size", help="$40/month")
        s.add_argument("--why")
        s.add_argument("--owner", help='me, a bot slug or a person id; "" clears')
        s.add_argument("--sort", type=int)
    json_flag(segment.add_parser("list")).set_defaults(fn="growth segment list")
    s = json_flag(segment.add_parser("create", help="by name (found or made in the market graph) or market segment id"))
    s.add_argument("--name")
    s.add_argument("--segment-id", dest="segment_id", metavar="ID", help="a market segment: segment/...")
    segment_flags(s)
    s.set_defaults(fn="growth segment create")
    s = json_flag(segment.add_parser("update", help="owner or admin; a bot may change why, who buys and deal size"))
    s.add_argument("id", help="the segment's id or its market segment id")
    s.add_argument("--name")
    segment_flags(s)
    s.set_defaults(fn="growth segment update")

    channel = growth.add_parser("channel", help="the channels customers come from").add_subparsers(dest="subsub")

    def channel_flags(s):
        s.add_argument("--track", choices=["performance", "brand"])
        s.add_argument("--stage", choices=["idea", "testing", "scaling", "paused"])
        s.add_argument("--owner", help='me, a bot slug or a person id; "" clears')
        s.add_argument("--budget", dest="budget_month", type=float, metavar="N", help="spend per month")
        s.add_argument("--source", dest="source_id", metavar="ID", help="the source that holds its spend")
        s.add_argument("--why")
        s.add_argument("--guardrail-kpi", dest="guardrail_kpi_id", metavar="ID")
        s.add_argument("--guardrail-max", dest="guardrail_max", type=float, metavar="N")
        s.add_argument("--sort", type=int)
        s.add_argument("--segment", dest="segments", action="append", metavar="ID",
                       help='a segment it reaches; repeatable; "" clears')
    s = json_flag(channel.add_parser("list"))
    s.add_argument("--retired", dest="include_retired", action="store_true", default=None, help="also retired ones")
    s.set_defaults(fn="growth channel list")
    s = json_flag(channel.add_parser("show"))
    s.add_argument("slug")
    s.set_defaults(fn="growth channel show")
    s = json_flag(channel.add_parser("add", help="owner or admin"))
    s.add_argument("slug", help="lowercase, digits and dashes: google-search")
    s.add_argument("--name", required=True)
    channel_flags(s)
    s.set_defaults(fn="growth channel add")
    s = json_flag(channel.add_parser("update", help="owner or admin; the owning bot may change why, stage, budget"))
    s.add_argument("slug")
    s.add_argument("--name")
    channel_flags(s)
    s.set_defaults(fn="growth channel update")
    s = json_flag(channel.add_parser("retire", help="owner or admin"))
    s.add_argument("slug")
    s.set_defaults(fn="growth channel retire")

    source = growth.add_parser("source", help="which system holds the truth for each number").add_subparsers(dest="subsub")

    def source_flags(s, required):
        s.add_argument("--truth-for", dest="truth_for", required=required, help="the number: bookings, ad spend")
        s.add_argument("--system", required=required, help="Stripe, Google Ads")
        s.add_argument("--reach-kind", dest="reach_kind", required=required, choices=["mcp", "credential", "url", "person"])
        s.add_argument("--reach-ref", dest="reach_ref", help="the MCP server, credential, URL or person")
        s.add_argument("--may", help="what a bot may do there")
        s.add_argument("--owner", help="me, a bot slug or a person id")
    s = json_flag(source.add_parser("list"))
    s.add_argument("--retired", dest="include_retired", action="store_true", default=None)
    s.set_defaults(fn="growth source list")
    s = json_flag(source.add_parser("add", help="owner or admin"))
    source_flags(s, True)
    s.set_defaults(fn="growth source add")
    s = json_flag(source.add_parser("update", help="owner or admin"))
    s.add_argument("id")
    source_flags(s, False)
    s.set_defaults(fn="growth source update")
    s = json_flag(source.add_parser("retire", help="owner or admin"))
    s.add_argument("id")
    s.set_defaults(fn="growth source retire")

    idea = growth.add_parser("idea", help="growth ideas and their tests").add_subparsers(dest="subsub")
    idea_sources = ["strategy", "competitor", "listening", "peec", "lost_test", "person", "other"]

    def idea_flags(s):
        s.add_argument("--hypothesis", help="we believe X because Y")
        s.add_argument("--prediction", help="what will move, by how much, by when")
        s.add_argument("--bet", dest="bet_id", metavar="ID", help="the strategy bet it tests")
        s.add_argument("--kpi", dest="kpi_id", metavar="ID", help="the KPI it should move")
        s.add_argument("--channel", dest="channels", action="append", metavar="SLUG", help="repeatable")
        s.add_argument("--segment", dest="segments", action="append", metavar="ID",
                       help='a segment it targets; repeatable; "" clears')
        s.add_argument("--source-kind", dest="source_kind", choices=idea_sources)
        s.add_argument("--source-ref", dest="source_ref", help="a link or id for that source")
    s = json_flag(idea.add_parser("create", help="file an idea; anyone may"))
    s.add_argument("--title", required=True)
    idea_flags(s)
    s.add_argument("--i", dest="ice_i", type=int, metavar="1-10", help="impact")
    s.add_argument("--c", dest="ice_c", type=int, metavar="1-10", help="confidence")
    s.add_argument("--e", dest="ice_e", type=int, metavar="1-10", help="ease")
    s.add_argument("--follow-up-of", dest="follow_up_of", metavar="ID", help="the finished idea this follows")
    s.add_argument("--assignee", help="me, a bot slug or a person id; default you")
    s.add_argument("--body", help="more detail for the task")
    s.set_defaults(fn="growth idea create")
    s = json_flag(idea.add_parser("list", help="best ICE first"))
    s.add_argument("--status", choices=["idea", "testing", "won", "lost", "inconclusive", "dropped", "paused"])
    s.add_argument("--bet", dest="bet_id", metavar="ID")
    s.add_argument("--channel", metavar="SLUG")
    s.add_argument("--segment", metavar="ID")
    s.add_argument("--kpi", dest="kpi_id", metavar="ID")
    s.add_argument("--q", help="words in the title or hypothesis")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="growth idea list")
    s = json_flag(idea.add_parser("show"))
    s.add_argument("id")
    s.set_defaults(fn="growth idea show")
    s = json_flag(idea.add_parser("update", help="its assignee, creator or an admin"))
    s.add_argument("id")
    s.add_argument("--title")
    idea_flags(s)
    s.add_argument("--judged-kpi", dest="judged_kpi_id", metavar="ID")
    s.add_argument("--from", dest="test_from", metavar="YYYY-MM-DD")
    s.add_argument("--to", dest="test_to", metavar="YYYY-MM-DD")
    s.add_argument("--change")
    s.add_argument("--stop-if", dest="stop_if")
    s.add_argument("--min-events", dest="min_events", type=int)
    s.add_argument("--audience")
    s.add_argument("--external-ref", dest="external_ref")
    s.set_defaults(fn="growth idea update")
    s = json_flag(idea.add_parser("score", help="impact, confidence, ease: 1-10 each"))
    s.add_argument("id")
    s.add_argument("--i", type=int, required=True, metavar="1-10")
    s.add_argument("--c", type=int, required=True, metavar="1-10")
    s.add_argument("--e", type=int, required=True, metavar="1-10")
    s.set_defaults(fn="growth idea score")
    s = json_flag(idea.add_parser("start", help="start its test; its assignee or an admin"))
    s.add_argument("id")
    s.add_argument("--from", dest="test_from", metavar="YYYY-MM-DD", help="default today")
    s.add_argument("--to", dest="test_to", required=True, metavar="YYYY-MM-DD")
    s.add_argument("--change", required=True, help="what the test changes")
    s.add_argument("--stop-if", dest="stop_if", required=True, help="stop early if this happens")
    s.add_argument("--judged-kpi", dest="judged_kpi_id", metavar="ID", help="default the idea's KPI")
    s.add_argument("--min-events", dest="min_events", type=int, help="events needed before a won verdict")
    s.add_argument("--audience")
    s.set_defaults(fn="growth idea start")
    s = json_flag(idea.add_parser("verdict", help="a stop-rule hit or missed baseline is lost, before min events; "
                                                  "its assignee or an admin"))
    s.add_argument("id")
    s.add_argument("--status", required=True, choices=["won", "lost", "inconclusive", "dropped", "paused"])
    s.add_argument("--result", help="what happened, with the numbers")
    s.add_argument("--learning", help="what we now know")
    s.add_argument("--bet-effect", dest="bet_effect", choices=["supports", "weakens", "none"])
    s.add_argument("--confidence", choices=["low", "medium", "high"])
    s.set_defaults(fn="growth idea verdict")

    cohort = growth.add_parser("cohort", help="monthly numbers by channel").add_subparsers(dest="subsub")
    s = json_flag(cohort.add_parser("list"))
    s.add_argument("--channel", metavar="SLUG", help="a channel slug, or unknown")
    s.add_argument("--month", metavar="YYYY-MM")
    s.add_argument("--segment", metavar="ID")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="growth cohort list")
    s = json_flag(cohort.add_parser("set", help="one channel's month, only the fields given; any bot or an admin"))
    s.add_argument("--channel", required=True, metavar="SLUG", help="a channel slug, or unknown")
    s.add_argument("--month", required=True, metavar="YYYY-MM")
    s.add_argument("--segment", dest="segment_id", metavar="ID", help="one segment's share of the month")
    s.add_argument("--spend", type=float)
    s.add_argument("--bot-cost", dest="bot_cost", type=float)
    s.add_argument("--new", dest="new_customers", type=int, metavar="N", help="new customers")
    s.add_argument("--d30", dest="kept_d30", type=float, metavar="PCT", help="%% kept after 30 days")
    s.add_argument("--d90", dest="kept_d90", type=float, metavar="PCT")
    s.add_argument("--d180", dest="kept_d180", type=float, metavar="PCT")
    s.add_argument("--revenue", dest="revenue_month", type=float, metavar="N", help="per customer per month")
    s.add_argument("--margin", dest="margin_pct", type=float, metavar="PCT")
    s.add_argument("--quality", choices=["actual", "estimate"])
    s.add_argument("--source", action="append", metavar="FIELD=TEXT", help="where a number came from; repeatable")
    s.set_defaults(fn="growth cohort set")
    s = json_flag(growth.add_parser("economics", help="CAC, months kept, LTV and LTV:CAC by channel"))
    s.add_argument("--month", metavar="YYYY-MM")
    s.add_argument("--segment", metavar="ID", help="only that segment's cohorts")
    s.set_defaults(fn="growth economics")
    for word, text in (("week", "tests running and due, verdicts, guardrails, the North Star"),
                       ("evidence", "each bet with the verdicts for and against it"),
                       ("working", "open work under ideas and on channel-labelled tasks"),
                       ("kpis", "the KPI tree: North Star, inputs, guardrails")):
        json_flag(growth.add_parser(word, help=text)).set_defaults(fn="growth " + word)
    s = json_flag(growth.add_parser("settings", help="unit, revenue, margin, win threshold; --set: owner or admin"))
    s.add_argument("--set", action="append", metavar="KEY=VALUE", help="repeatable")
    s.set_defaults(fn="growth settings")

    lens = sub.add_parser("lens", help="thought exercises run against the strategy or the market").add_subparsers(dest="sub")
    s = lens.add_parser("list", help="the lenses, with their steps")
    s.add_argument("--market", action="store_true", help="only the market lenses")
    s.set_defaults(fn="lens list")
    s = lens.add_parser("run", help="run a lens; prints the run, waiting up to 60 seconds for its summary")
    s.add_argument("id")
    s.add_argument("--answers-file", dest="answers_file", help='JSON {"step key": answer}; a step left out is assumed')
    s.add_argument("--draft", action="store_true", help="owner only: run on the draft instead of the published version")
    s.add_argument("--no-wait", dest="no_wait", action="store_true", help="print the running run and return")
    s.set_defaults(fn="lens run")
    s = lens.add_parser("show", help="one run: answers, result and changes")
    s.add_argument("id")
    s.set_defaults(fn="lens show")
    s = lens.add_parser("runs", help="runs, newest first")
    s.add_argument("--lens", dest="lens")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="lens runs")

    market = sub.add_parser("market", help="the shared market graph: read it, report into it, curate it").add_subparsers(dest="sub")
    s = market.add_parser("show", help="one entity, its edges both ways, the evidence, the last ten events")
    s.add_argument("id")
    s.set_defaults(fn="market show")
    s = market.add_parser("find", help="names, aliases, summaries, and evidence")
    s.add_argument("text")
    s.set_defaults(fn="market find")
    s = market.add_parser("edges", help="graph rows; symmetric relations are returned from either end")
    s.add_argument("--from", dest="src")
    s.add_argument("--to", dest="dst")
    s.add_argument("--rel")
    s.add_argument("--as-of", dest="as_of")
    s.set_defaults(fn="market edges")
    s = market.add_parser("delta", help="what changed, grouped by entity")
    s.add_argument("--since", default="7d")
    s.set_defaults(fn="market delta")
    s = market.add_parser("ask", help="a question answered from the graph, with citations")
    s.add_argument("question")
    s.set_defaults(fn="market ask")
    s = market.add_parser("report", help="prose only; this does not change the graph")
    s.add_argument("--kind", required=True, choices=["new-entity", "edge", "property-change", "correction", "question", "other"])
    s.add_argument("--about", default="")
    s.add_argument("--claim", required=True)
    s.add_argument("--source", default="")
    s.add_argument("--quote", default="")
    s.add_argument("--confidence", default="medium", choices=["high", "medium", "low"])
    s.add_argument("--urgent", action="store_true")
    s.add_argument("--source-ref", dest="source_ref", help="where it came from, e.g. the intake item id")
    s.set_defaults(fn="market report")
    s = market.add_parser("resolve", help="close an insight: applied, merged, rejected, or needs-human")
    s.add_argument("id")
    s.add_argument("--status", required=True, choices=["applied", "merged", "rejected", "needs-human"])
    s.add_argument("--resolution", default="")
    s.add_argument("--event", action="append", dest="events", default=[])
    s.set_defaults(fn="market resolve")
    s = market.add_parser("apply", help="write evidence first, then the entity or edge, and mark the insight applied")
    s.add_argument("id")
    s.add_argument("--source", default="")
    s.add_argument("--source-kind", dest="source_kind", default="other")
    s.add_argument("--quote", default="")
    s.add_argument("--our-read", dest="our_read", default="")
    s.add_argument("--entity-type", dest="entity_type")
    s.add_argument("--entity-name", dest="entity_name")
    s.add_argument("--entity-id", dest="entity_id")
    s.add_argument("--summary")
    s.add_argument("--tier", choices=["core", "lookalike", "phrase-stealer", "secondary"], help="for a new company")
    s.add_argument("--new-id", dest="new_id", help="the id for a new entity when the default type/name-slug is wrong, e.g. company/self")
    s.add_argument("--edge-src", dest="edge_src")
    s.add_argument("--edge-rel", dest="edge_rel")
    s.add_argument("--edge-dst", dest="edge_dst")
    s.set_defaults(fn="market apply")
    s = market.add_parser("import", help="apply a verified batch: one JSON entity or edge per line, each with its evidence")
    s.add_argument("file", help="the JSONL file, at most 2000 non-blank lines (split larger ones); - reads stdin")
    s.add_argument("--dry-run", dest="preview", action="store_true", help="report what would change and keep nothing")
    s.add_argument("--note", default="", help="one sentence on where the batch came from")
    s.set_defaults(fn="market import")
    s = market.add_parser("sweep", help="one owner task for needs-human, listening tasks for unverified stale entities")
    s.add_argument("--today")
    s.add_argument("--unverified", action="append", default=[], help="entity-id=what to look for")
    s.set_defaults(fn="market sweep")
    s = market.add_parser("refresh", help="rewrite the weekly delta from market events; only a Monday changes it")
    s.add_argument("--today")
    s.set_defaults(fn="market refresh")
    s = market.add_parser("page", help="read one market page (overview, coverage-universe, ...), or rewrite it from a Markdown file")
    s.add_argument("name", help="overview, structure-and-size, coverage-universe, people-who-matter, channels, "
                                "regulation-and-catalysts, theses, open-questions or customer-themes")
    s.add_argument("--body-file", dest="body_file", help="the whole page in Markdown; - reads stdin; omit to read the page")
    s.set_defaults(fn="market page")
    watch = market.add_parser("watch", help="watch a competitor's public page; the server reads it daily and files an insight on a change").add_subparsers(dest="subsub")
    s = watch.add_parser("add", help="watch one page of an entity")
    s.add_argument("entity_id")
    s.add_argument("url")
    s.add_argument("--kind", default="other", choices=["pricing", "careers", "changelog", "docs", "home", "other"])
    s.set_defaults(fn="market watch add")
    s = watch.add_parser("list", help="the watched pages and when each was last read")
    s.add_argument("--entity", dest="entity_id")
    s.set_defaults(fn="market watch list")
    s = watch.add_parser("stop", help="stop watching a page (the row is kept)")
    s.add_argument("id")
    s.set_defaults(fn="market watch stop")
    s = watch.add_parser("check", help="read the watched pages now (the longest unchecked first, at most 50)")
    s.set_defaults(fn="market watch check")
    briefing = market.add_parser("briefing", help="the weekly market briefing").add_subparsers(dest="subsub")
    s = briefing.add_parser("post", help="post the week's briefing; the same week again replaces it")
    s.add_argument("--week", required=True, help="ISO week, like 2026-W44")
    s.add_argument("--title", required=True)
    s.add_argument("--body-file", dest="body_file", required=True, help="the briefing in Markdown; - reads stdin")
    s.add_argument("--items-file", dest="items_file", help="JSON list of {text, kind: change|signal|theme|size, entity_ids}")
    s.set_defaults(fn="market briefing post")
    s = briefing.add_parser("latest", help="the newest briefing, its items and the bets it touches")
    s.set_defaults(fn="market briefing latest")
    s = market.add_parser("define", help="Librarian or owner: define the market (what it is, what is in and out, how to count)")
    s.add_argument("--description")
    s.add_argument("--root-name", dest="root_name", help="the root segment's name, the first time")
    s.add_argument("--include", action="append", dest="includes", metavar="TEXT", help="what is in; repeatable, replaces the list")
    s.add_argument("--exclude", action="append", dest="excludes", metavar="TEXT", help="what is out; repeatable, replaces the list")
    s.add_argument("--rule", action="append", dest="counting_rules", metavar="TEXT", help="a counting rule; repeatable, replaces the list")
    s.add_argument("--year", type=int)
    s.add_argument("--geography")
    s.add_argument("--param", action="append", dest="params", metavar="NAME=NUMBER", help="a shared parameter formulas read as $NAME; repeatable")
    s.set_defaults(fn="market define")
    s = market.add_parser("segment", help="the market's segments: add one, or print the tree").add_subparsers(dest="subsub")
    s2 = s.add_parser("add", help="Librarian or owner: a segment under the root, or under --parent")
    s2.add_argument("name")
    s2.add_argument("--parent", dest="parent_id", metavar="SEGMENT_ID")
    s2.add_argument("--summary")
    s2.set_defaults(fn="market segment add")
    s2 = s.add_parser("tree", help="the segments, indented, with their sizes")
    s2.set_defaults(fn="market segment tree")
    s = market.add_parser("size", help="a segment's yearly spend: add an estimate, accept or reject one, list them").add_subparsers(dest="subsub")
    s2 = s.add_parser("add", help="Librarian or owner: file an estimate, proposed unless --accept")
    s2.add_argument("--segment", dest="segment_id", required=True, metavar="SEGMENT_ID")
    s2.add_argument("--method", required=True, choices=["top_down", "bottom_up", "player_sum", "owner"])
    s2.add_argument("--spend", dest="spend_usd", type=float, metavar="USD", help="omit when --formula gives it")
    s2.add_argument("--low", dest="low_usd", type=float, required=True, metavar="USD")
    s2.add_argument("--high", dest="high_usd", type=float, required=True, metavar="USD")
    s2.add_argument("--formula", help="arithmetic over numbers, --param names and shared $names")
    s2.add_argument("--param", action="append", dest="params", metavar="NAME=NUMBER")
    s2.add_argument("--arithmetic", help="the working, in words")
    s2.add_argument("--growth", dest="growth_pct", type=float, metavar="PERCENT")
    s2.add_argument("--year", type=int)
    s2.add_argument("--dimension", choices=["offering", "buyer"])
    s2.add_argument("--basis", choices=["product", "platform"])
    s2.add_argument("--confidence", choices=["high", "medium", "low"])
    s2.add_argument("--overlaps")
    s2.add_argument("--spread", help="analyst headline figures, as text")
    s2.add_argument("--evidence", action="append", dest="evidence_ids", metavar="EVIDENCE_ID")
    s2.add_argument("--evidence-url", dest="evidence_url")
    s2.add_argument("--quote")
    s2.add_argument("--source-kind", dest="source_kind")
    s2.add_argument("--our-read", dest="our_read")
    s2.add_argument("--accept", action="store_true", default=None)
    s2.set_defaults(fn="market size add")
    for word, help_text in (("accept", "the estimate becomes the segment's size"), ("reject", "the estimate is out")):
        s2 = s.add_parser(word, help=help_text)
        s2.add_argument("id")
        s2.set_defaults(fn="market size decide", decision=word)
    s2 = s.add_parser("params", help="owner only: change an estimate's parameter; files a new accepted owner estimate")
    s2.add_argument("id")
    s2.add_argument("--param", action="append", dest="params", required=True, metavar="NAME=NUMBER")
    s2.set_defaults(fn="market size decide", decision="params")
    s2 = s.add_parser("list", help="the estimates, optionally one segment's")
    s2.add_argument("--segment", metavar="SEGMENT_ID")
    s2.set_defaults(fn="market size list")
    s = market.add_parser("figure", help="a player's revenue, ARR, customers or seats in a segment").add_subparsers(dest="subsub")
    s2 = s.add_parser("add", help="Librarian or owner: one dated figure with its evidence")
    s2.add_argument("--entity", dest="entity_id", required=True, metavar="ENTITY_ID")
    s2.add_argument("--segment", dest="segment_id", required=True, metavar="SEGMENT_ID")
    s2.add_argument("--metric", required=True, choices=["revenue", "arr", "customers", "seats"])
    s2.add_argument("--value", type=float)
    s2.add_argument("--text", help="when there is no number")
    s2.add_argument("--kind", required=True, choices=["reported", "estimated", "claimed"])
    s2.add_argument("--as-of", dest="as_of", required=True, metavar="YYYY-MM[-DD]")
    s2.add_argument("--evidence", dest="evidence_id", metavar="EVIDENCE_ID")
    s2.add_argument("--evidence-url", dest="evidence_url")
    s2.add_argument("--quote")
    s2.add_argument("--source-kind", dest="source_kind")
    s2.add_argument("--our-read", dest="our_read")
    s2.set_defaults(fn="market figure add")
    s = market.add_parser("sources", help="where to look when sizing the market: the catalog and the team's additions")
    s.add_argument("--add-file", dest="add_file", help="a YAML file of sources to add (a list, or one)")
    s.set_defaults(fn="market sources")
    s = market.add_parser("spend", help="the market by spend: the tree with sizes (JSON has every estimate)")
    s.add_argument("--dimension", choices=["offering", "buyer"])
    s.add_argument("--basis", choices=["product", "platform"])
    s.add_argument("--year", type=int)
    s.set_defaults(fn="market spend")
    s = market.add_parser("research", help="owner only: ask the Librarian to define and size the market")
    s.add_argument("description")
    s.set_defaults(fn="market research")

    listen = sub.add_parser("listening", help="Listening's saved posts: save a sweep, decide where each post goes, trace a post").add_subparsers(dest="sub")
    s = listen.add_parser("save", help="one sweep of one query and the posts it saw, from a JSON file")
    s.add_argument("--file", required=True, help="JSON: {source, query, status, note, pages_read, items: [...]}; - for stdin")
    s.set_defaults(fn="listening save")
    s = listen.add_parser("decide", help="put the listening-item decisions to new posts and route them to the message bots")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--item", action="append", dest="item_ids", default=[])
    s.set_defaults(fn="listening decide")
    s = listen.add_parser("show", help="a post, its run, its decisions, and where it went")
    s.add_argument("id")
    s.set_defaults(fn="listening show")
    s = listen.add_parser("runs", help="sweeps, newest first, with ok/blocked/rate_limited/error")
    s.add_argument("--since")
    s.add_argument("--source")
    s.set_defaults(fn="listening runs")
    s = listen.add_parser("stats", help="coverage by source and precision by message bot")
    s.add_argument("--since")
    s.set_defaults(fn="listening stats")
    item = listen.add_parser("item", help="posts Listening routed to you: list them, then accept or reject each").add_subparsers(dest="subsub")
    s = item.add_parser("list", help="your posts, oldest first")
    s.add_argument("--destination")
    s.add_argument("--status", default="new", choices=["new", "accepted", "rejected", "duplicate"])
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(fn="listening item list")
    s = item.add_parser("resolve", help="accepted --ref <your record>, duplicate --ref <existing>, or rejected --reason")
    s.add_argument("id")
    s.add_argument("--status", required=True, choices=["accepted", "rejected", "duplicate"])
    s.add_argument("--ref", dest="receiver_ref", default="")
    s.add_argument("--reason", default="")
    s.set_defaults(fn="listening item resolve")

    def mcp_flags(s):
        s.add_argument("--mcp-url", dest="mcp_url", help="the tool is a remote MCP server at this https address (http only for localhost)")
        s.add_argument("--transport", choices=["http", "sse"], help="the MCP server's transport (default http)")
        s.add_argument("--header", dest="headers", action="append", metavar="'NAME: VALUE'",
                       help='an MCP header; the value may use ${VAR_NAME} for the tool\'s --env variable, never the value itself; repeatable')

    tools = sub.add_parser("tool", help="what the team uses: each tool, what a bot uses, its model, repository and declared tools (docs/creating-bots.md)").add_subparsers(dest="sub")
    s = tools.add_parser("list", help="the team's tools; with --bot, that bot's tools with their status")
    s.add_argument("--bot", help="a bot's slug, or me: that bot's own tools instead of the team's")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.set_defaults(fn="tool list")
    s = tools.add_parser("show", help="one tool: the page, its queries and the learnings")
    s.add_argument("service")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.set_defaults(fn="tool show")
    s = tools.add_parser("query-search", help="search a tool's query catalog")
    s.add_argument("service")
    s.add_argument("term", nargs="?", default="", help="matched against id, title, description, tags and SQL")
    s.add_argument("--id", dest="query_id", help="print one query's SQL and params")
    s.add_argument("--json", action="store_true", help="print the matching queries as JSON")
    s.set_defaults(fn="tool query-search")
    s = tools.add_parser("learn", help='add a learning: hub tool learn <service> "<text>"')
    s.add_argument("service")
    s.add_argument("text")
    s.set_defaults(fn="tool learn")
    s = tools.add_parser("report", help="report the external profile's complete current tool list")
    s.add_argument("tools", help="JSON array of tool entries")
    s.set_defaults(fn="tool report")
    s = tools.add_parser("add", help="register a tool: BotOps adds it to bot.yaml; never a credential value")
    s.add_argument("bot")
    s.add_argument("service", help="a short name such as posthog or google-calendar")
    s.add_argument("--can", required=True, help="read, draft, post, act, use, send or write; comma separated")
    s.add_argument("--identity", help="who it acts as, in words for a person")
    s.add_argument("--scope", action="append", metavar="KEY=VALUE", help="database=warehouse, channels=#a,#b, project=123; repeatable")
    s.add_argument("--env", help="the variable's NAME, such as POSTHOG_KEY; the owner installs the value")
    s.add_argument("--note")
    mcp_flags(s)
    s.add_argument("--title-prefix", dest="title_prefix", help="prefix for the generated BotOps task")
    s.add_argument("--dry-run", dest="dry_run", action="store_true", help="validate without opening a task")
    s.set_defaults(fn="tool add")
    s = tools.add_parser("update", help="change a declared tool's can, scope or note in place; never remove and add again")
    s.add_argument("id", help="the tool id from `hub tool list --bot`")
    s.add_argument("--bot", required=True, help="the bot's slug")
    s.add_argument("--can", help="the full list from now on: read, draft, post, act, use, send or write; comma separated")
    s.add_argument("--scope", action="append", metavar="KEY=VALUE", help="set a key (database=warehouse, channels=#a,#b); KEY= takes it off; repeatable")
    s.add_argument("--note", help='the new note; "" clears it')
    mcp_flags(s)
    s.add_argument("--title-prefix", dest="title_prefix", help="prefix for the generated BotOps task")
    s.set_defaults(fn="tool update")
    s = tools.add_parser("remove", help="ask BotOps to remove a tool, or withdraw a pending request")
    s.add_argument("bot")
    s.add_argument("id", help="the tool id from `hub tool list --bot`")
    s.set_defaults(fn="tool remove")

    auto = sub.add_parser("automation", help="automations: a trigger and steps").add_subparsers(dest="sub")
    s = auto.add_parser("list")
    s.add_argument("--owner", help="person:<id>, bot:<slug> or group:<id>")
    s.add_argument("--limit", type=int)
    s.add_argument("--cursor", type=int, help="`next` from the page before")
    s.set_defaults(fn="automation list")
    s = auto.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn="automation get")
    s = auto.add_parser("create", help="save a new automation from a JSON flow")
    s.add_argument("--file", required=True, help="a JSON object {name, owner, trigger, steps, ...}; - reads stdin")
    s.set_defaults(fn="automation create")
    s = auto.add_parser("update", help="change one; a new trigger, steps or batch is a new version")
    s.add_argument("id")
    s.add_argument("--file", help="a JSON object with the fields to change; - reads stdin")
    s.add_argument("--name")
    s.add_argument("--summary", help="a few words on the change")
    switch = s.add_mutually_exclusive_group()
    switch.add_argument("--enable", action="store_true")
    switch.add_argument("--disable", action="store_true")
    s.set_defaults(fn="automation update")
    s = auto.add_parser("delete")
    s.add_argument("id")
    s.set_defaults(fn="automation delete")
    s = auto.add_parser("run", help="run it now and print the run")
    s.add_argument("id")
    s.add_argument("--input", help="a JSON object standing in for the trigger's output")
    s.set_defaults(fn="automation run")
    s = auto.add_parser("runs")
    s.add_argument("id")
    s.add_argument("--limit", type=int)
    s.add_argument("--before", help="`next` from the page before")
    s.set_defaults(fn="automation runs")
    s = auto.add_parser("held", help="end a run-guard hold: release queues its held runs, drop skips them")
    s.add_argument("id")
    s.add_argument("action", choices=["release", "drop"])
    s.set_defaults(fn="automation held")
    s = auto.add_parser("recent-runs", help="runs across every automation you may see, newest first")
    s.add_argument("--range", choices=["5m", "1h", "24h", "7d"])
    s.add_argument("--status", choices=["done", "failed", "waiting", "running", "held"])
    s.add_argument("--trigger", help="message, task_received, schedule, webhook, listening, event, due or system")
    s.add_argument("--owner", help="person:<id>, bot:<slug> or group:<id>")
    s.add_argument("--limit", type=int)
    s.add_argument("--before", help="`next` from the page before")
    s.set_defaults(fn="automation recent-runs")
    s = auto.add_parser("stats", help="runs, failures and cost per automation over a range")
    s.add_argument("--range", choices=["5m", "1h", "24h", "7d"])
    s.set_defaults(fn="automation stats")
    s = auto.add_parser("versions")
    s.add_argument("id")
    s.add_argument("--pending", action="store_true", help="proposals only")
    s.set_defaults(fn="automation versions")
    s = auto.add_parser("restore")
    s.add_argument("id")
    s.add_argument("n", type=int)
    s.set_defaults(fn="automation restore")
    s = auto.add_parser("discard", help="drop a pending version (a proposal)")
    s.add_argument("id")
    s.add_argument("n", type=int)
    s.set_defaults(fn="automation discard")
    s = auto.add_parser("question-sets", help="the question sets a Decide step may name")
    s.set_defaults(fn="automation question-sets")
    s = auto.add_parser("guide", help="how BotOps builds automations, with the flow format")
    s.set_defaults(fn="automation guide")
    s = auto.add_parser("inbox", help="read or set an inbox's send switch")
    s.add_argument("inbox", help="an email address, slack:dm or slack:#<channel>")
    s.add_argument("--send", choices=["on", "off"], help="leave out to read it")
    s.set_defaults(fn="automation inbox")
    s = sub.add_parser("audit", help="who changed caps, spend, groups, automations and inboxes, newest first (the owner sees all)")
    s.add_argument("--object", help="one object, such as automation:<id> or inbox:<address>")
    s.add_argument("--limit", type=int)
    s.add_argument("--before", help="`next` from the page before")
    s.set_defaults(fn="audit")

    s = sub.add_parser("event-types", help="every event Tico names, with a one-line description and an example payload")
    s.add_argument("--group", help="only this group: task, approval, needs_you, bot, release, meeting, strategy, market, "
                                   "inbound, health, webhook, tico, custom")
    s.set_defaults(fn="event-types")
    peer = sub.add_parser("peer", help="federation: shared threads with other Tico installs, through Tico HQ").add_subparsers(dest="sub")
    s = peer.add_parser("list", help="this install's key, Gateway, peerings and shared threads")
    s.set_defaults(fn="peer list")
    s = peer.add_parser("key", help="make this install's federation key (does nothing when it has one)")
    s.set_defaults(fn="peer key")
    s = peer.add_parser("rotate-key", help="a new key; every peering needs approving again on both sides")
    s.set_defaults(fn="peer rotate-key")
    s = peer.add_parser("request", help="ask another install to peer with this one; it approves, then you do")
    s.add_argument("install", help="the other install's id: gi_ and 24 hex digits")
    s.set_defaults(fn="peer request")
    for verb, text in (("approve", "approve a peering, having checked the other install's key"),
                       ("pause", "the kill switch: nothing crosses a paused peering"),
                       ("resume", "let a paused peering carry again; what was missed is caught up"),
                       ("revoke", "end a peering; a new request starts over")):
        s = peer.add_parser(verb, help=text)
        s.add_argument("peering", help="the peering's id from `hub peer list`")
        s.set_defaults(fn="peer " + verb)
    s = peer.add_parser("share", help="give an active peer a thread you own: it gets the thread from now on")
    s.add_argument("thread", help="the thread's id")
    s.add_argument("install", help="the other install's id")
    s.set_defaults(fn="peer share")
    s = peer.add_parser("unshare", help="take an install out of a thread you own; it gets the removal and nothing after")
    s.add_argument("thread", help="the thread's id")
    s.add_argument("install", help="the other install's id")
    s.set_defaults(fn="peer unshare")
    s = peer.add_parser("add-reader", help="let a person on this install read a shared copy of a thread")
    s.add_argument("thread", help="the thread's id on this install (a shared copy)")
    s.add_argument("actor", help="the person, as human:<name>")
    s.set_defaults(fn="peer add-reader")
    trigger = sub.add_parser("trigger", help="webhooks that start automations (Settings > Webhooks)").add_subparsers(dest="sub")
    s = trigger.add_parser("list")
    s.set_defaults(fn="trigger list")
    s = trigger.add_parser("create", help="make a webhook trigger; the address with its secret is shown once")
    s.add_argument("name")
    s.add_argument("--id", help="lowercase letters, digits and dashes; made from the name by default")
    s.set_defaults(fn="trigger create")
    s = trigger.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn="trigger show")
    s = trigger.add_parser("update", help="change a trigger's name or settings")
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--preset", choices=["none", "hmac", "github", "standard", "stripe", "slack"], help="how calls are verified (needs a signing secret)")
    s.add_argument("--type-from", dest="type_from", help="a header name or a JSON pointer (/type); empty for the preset's default")
    s.add_argument("--hourly", type=int, help="calls an hour, 1-600 (60 by default)")
    s.add_argument("--keep-days", dest="keep_days", type=int, help="days calls stay readable and can run again, 1-30 (7 by default)")
    s.add_argument("--unheard", choices=["log", "note"], help="note: tell the maker, at most hourly, when no automation or rule acted")
    s.set_defaults(fn="trigger update")
    s = trigger.add_parser("rotate", help="a new secret; the old address stops working")
    s.add_argument("id")
    s.set_defaults(fn="trigger rotate")
    s = trigger.add_parser("sign", help="verify each call with a preset; the signing secret is shown once")
    s.add_argument("id")
    s.add_argument("--preset", choices=["hmac", "github", "standard", "stripe", "slack"], help="the current one, else hmac")
    s.add_argument("--secret", help="the sender's own secret (16+ characters); a new one is made when left out")
    s.add_argument("--off", action="store_true", help="stop checking signatures")
    s.set_defaults(fn="trigger sign")
    s = trigger.add_parser("delete")
    s.add_argument("id")
    s.set_defaults(fn="trigger delete")
    s = trigger.add_parser("deliveries", help="the latest calls to a trigger")
    s.add_argument("id")
    s.set_defaults(fn="trigger deliveries")
    s = trigger.add_parser("delivery", help="one stored call: redacted headers and the body (long bodies in parts)")
    s.add_argument("id")
    s.add_argument("delivery")
    s.add_argument("--part", type=int, help="which 20,000-character part of a long body (from 1)")
    s.set_defaults(fn="trigger delivery")
    s = trigger.add_parser("replay", help="run a stored call again as a new occurrence")
    s.add_argument("id")
    s.add_argument("delivery")
    s.set_defaults(fn="trigger replay")
    s = trigger.add_parser("recover", help="run again, under the current secret and preset, every call refused in the last "
                                           "hours whose body is kept; a sender's later retry of one is a duplicate")
    s.add_argument("id")
    s.add_argument("--since", type=int, default=24, help="hours back, 1-720 (24 by default)")
    s.set_defaults(fn="trigger recover")
    relay = trigger.add_parser("relay", help="a second address through Tico Gateway, so nothing is lost while this "
                                             "server is off").add_subparsers(dest="subsub")
    for word, text in (("create", "make the address; its secret is shown once"),
                       ("rotate", "replace the secret; the old address stops at once"),
                       ("delete", "remove the address; the trigger and its own address stay")):
        s = relay.add_parser(word, help=text)
        s.add_argument("trigger", help="the trigger id from `hub trigger list`")
        s.set_defaults(fn="trigger relay " + word)
    relay_top = sub.add_parser("relay", help="webhooks through Tico Gateway").add_subparsers(dest="sub")
    s = relay_top.add_parser("status", help="the Gateway key, the addresses, the last pull and what waits at the Gateway")
    s.set_defaults(fn="relay status")
    s = relay_top.add_parser("set", help="whether a delivery to a trigger's Gateway address starts a sleeping server")
    s.add_argument("trigger", help="the trigger id from `hub trigger list`")
    s.add_argument("--wake", type=on_off, metavar="on|off", required=True, help="start the server on a delivery (on) or not (off)")
    s.set_defaults(fn="relay set")
    wake_url = relay_top.add_parser("wake-url", help="an address Tico Gateway knocks on to start a server you run when a "
                                                     "webhook waits for it").add_subparsers(dest="subsub")
    wake_url.add_parser("show", help="the address, who set it and when").set_defaults(fn="relay wake-url show")
    wake_url.add_parser("off", help="stop knocking").set_defaults(fn="relay wake-url off")
    s = wake_url.add_parser("set", help="set the address; the signing secret is shown once")
    s.add_argument("url", help="an https address you run")
    s.set_defaults(fn="relay wake-url set")
    call = sub.add_parser("call", help="Tico Meet: audio and screen share in a thread, with a transcript posted to it"
                          ).add_subparsers(dest="sub")
    s = call.add_parser("list", help="the calls in a thread, newest first")
    s.add_argument("thread_id")
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--before", help="the next_before of the page you read, to read the next page")
    s.set_defaults(fn="call list")
    s = call.add_parser("start", help="start a call in a thread you can write to; you become its connector")
    s.add_argument("thread_id")
    s.add_argument("--title", default="")
    s.add_argument("--speak", dest="speak_replies", action="store_true",
                   help="bots speak their replies on this call (off by default; spoken replies are not sent yet)")
    s.set_defaults(fn="call start")
    for action, help_text in (("show", "a call: its state, people, bots and how it ended"),
                              ("join", "join a call: the URL and a token good for 5 minutes"),
                              ("token", "a fresh token while you are joined"),
                              ("leave", "leave a call you joined"),
                              ("end", "end a call you started; its transcript is posted to the thread"),
                              ("retry", "ask again for action items the Gateway could not make")):
        s = call.add_parser(action, help=help_text)
        s.add_argument("call_id")
        s.set_defaults(fn="call " + action)
    bot = call.add_parser("bot", help="bots that listen to a call and reply in its thread").add_subparsers(dest="subsub")
    s = bot.add_parser("add", help="let bots listen to a call you joined (up to 10)")
    s.add_argument("call_id")
    s.add_argument("bots", nargs="+")
    s.set_defaults(fn="call bot add")
    s = bot.add_parser("remove", help="take a bot out of a call")
    s.add_argument("call_id")
    s.add_argument("bot")
    s.set_defaults(fn="call bot remove")
    sleep = sub.add_parser("sleep", help="Sleep when idle: a hosted server stops after quiet hours").add_subparsers(dest="sub")
    s = sleep.add_parser("status", help="hosted or not, the hours, what keeps the server up and Tico's last answer")
    s.set_defaults(fn="sleep status")
    s = sleep.add_parser("set", help="turn it on (1-24 quiet hours) or off (0); an owner or an admin")
    s.add_argument("--hours", type=quiet_hours, metavar="N", help="quiet hours before it sleeps; 0 turns it off")
    s.add_argument("--runners", type=on_off, metavar="on|off", help="stop the bot computers with the server (default on)")
    s.set_defaults(fn="sleep set")

    notice = sub.add_parser("notice", help="notices from Tico Team").add_subparsers(dest="sub")
    s = notice.add_parser("list", help="the notices Tico Team sent, newest first")
    s.set_defaults(fn="notice list")

    catalogue = sub.add_parser("settings", help="find, read and change a control in Settings by its words "
                               "(docs/permissions.md)").add_subparsers(dest="sub")
    s = catalogue.add_parser("find", help="settings whose name, place or key hold these words")
    s.add_argument("words")
    s.set_defaults(fn="settings find")
    for leaf, text in (("show", "one setting: its value, who may change it and its card"),
                       ("set", "change one setting as yourself, or BotOps or the Assistant as the person who asked")):
        s = catalogue.add_parser(leaf, help=text)
        s.add_argument("key", help="the key `hub settings find` gives, e.g. team.rules.admin_sql")
        if leaf == "set":
            s.add_argument("value", help="on/off, a number, text, a comma-separated list or JSON")
            s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
        target = s.add_mutually_exclusive_group()
        target.add_argument("--bot", help="the bot, for a setting set per bot")
        target.add_argument("--computer", help="the computer, for a setting set per computer")
        target.add_argument("--id", help="the one thing it sets: a trigger, webhook, rule, routine, repository...")
        target.add_argument("--person", help="the person's id, for a setting set per person")
        s.set_defaults(fn="settings " + leaf)
    release = sub.add_parser("release", help="the team's releases: ask for one now, or set the daily hour").add_subparsers(dest="sub")
    s = release.add_parser("now", help="ask the Release Manager to release what is at Pending Release now (hotfix, "
                                       "midday release); one task listing the tickets")
    s.add_argument("--note", help="why now; goes in the task; a person's note asks even with nothing at Pending Release")
    s.set_defaults(fn="release now")
    s = release.add_parser("schedule", help="show the daily release, or change it")
    on = s.add_mutually_exclusive_group()
    on.add_argument("--on", dest="on", action="store_true", default=None, help="turn the daily release on")
    on.add_argument("--off", dest="on", action="store_false", help="turn the daily release off")
    s.add_argument("--time", dest="times", action="append", metavar="HH:MM",
                   help="a release time in company time; give it twice for two (replaces the times)")
    s.add_argument("--manager", dest="bot", help="the Release Manager's bot slug; empty finds it by starter template")
    s.add_argument("--writer", help="the Technical Writer's bot slug; empty finds it by starter template")
    s.set_defaults(fn="release schedule")

    appr = sub.add_parser("approval").add_subparsers(dest="sub")
    s = appr.add_parser("request",
                        help="ask for a yes; if the owner already said send, mail send --approve <their message id>")
    s.add_argument("--kind", required=True, choices=list(APPROVAL_KINDS))
    s.add_argument("--payload-file", dest="payload_file")
    s.add_argument("--payload")
    s.add_argument("--task")
    s.add_argument("--supersedes", metavar="APPROVAL_ID",
                   help="your older undecided approval this replaces; it is withdrawn")
    s.set_defaults(fn="approval request")
    s = appr.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn="approval show")
    s = appr.add_parser("read-back", help="the Assistant: list in the person's chat the approvals it will approve")
    s.add_argument("ids", nargs="+")
    s.set_defaults(fn="approval read-back")
    s = appr.add_parser("approve", help="approve an approval addressed to you")
    s.add_argument("id")
    s.add_argument("--note", default="")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="approval approve")
    s = appr.add_parser("decline", help="decline an approval addressed to you, with a reason")
    s.add_argument("id")
    s.add_argument("--note", default="")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="approval decline")

    webhook = sub.add_parser("webhook", help="outgoing webhooks: Tico posts events to an address you choose "
                                              "(Settings > Webhooks)").add_subparsers(dest="sub")
    s = webhook.add_parser("list", help="the endpoints you may see, with 7-day delivered and failed counts")
    s.add_argument("--limit", type=int)
    s.add_argument("--offset", type=int)
    s.set_defaults(fn="webhook list")
    s = webhook.add_parser("create", help="make an endpoint; the signing secret is printed once. A bot files an approval instead")
    s.add_argument("--name", required=True)
    s.add_argument("--url", required=True, help="an https address")
    s.add_argument("--events", nargs="+", help="event names, groups (task.*) or * (default all)")
    s.add_argument("--rate-per-hour", dest="rate_per_hour", type=int, help="most sends an hour (default 600)")
    s.add_argument("--allow-http", dest="allow_http", action="store_true", default=None,
                   help="owner only: plain http to localhost, for development")
    s.add_argument("--departments", nargs="+", help="only events about work in these departments (default all)")
    s.add_argument("--bots", nargs="+", help="only events that involve these bots (default all)")
    s.add_argument("--task-types", dest="task_types", nargs="+", help="only events about tasks of these types (default all)")
    s.add_argument("--header", action="append", help='a header to send, "Name: value" (up to 10; every use replaces the list)')
    s.add_argument("--secret-header", dest="secret_header", action="append",
                   help='like --header, the value is sealed and never shown; "Name" alone keeps the value it has')
    s.set_defaults(fn="webhook create")
    s = webhook.add_parser("show", help="one endpoint and its last attempts")
    s.add_argument("id")
    s.set_defaults(fn="webhook show")
    s = webhook.add_parser("update", help="change an endpoint")
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--url")
    s.add_argument("--events", nargs="+")
    s.add_argument("--rate-per-hour", dest="rate_per_hour", type=int)
    s.add_argument("--allow-http", dest="allow_http", action=argparse.BooleanOptionalAction, default=None)
    s.add_argument("--expected-revision", dest="expected_revision", type=int, help="the revision you read; needed with --url")
    s.add_argument("--credentials", choices=("keep", "clear"),
                   help="a new address for another destination: keep sends the saved secret headers there, clear removes them")
    s.add_argument("--departments", nargs="+", help="only events about work in these departments (default all)")
    s.add_argument("--bots", nargs="+", help="only events that involve these bots (default all)")
    s.add_argument("--task-types", dest="task_types", nargs="+", help="only events about tasks of these types (default all)")
    s.add_argument("--header", action="append", help='a header to send, "Name: value" (up to 10; every use replaces the list)')
    s.add_argument("--secret-header", dest="secret_header", action="append",
                   help='like --header, the value is sealed and never shown; "Name" alone keeps the value it has')
    s.add_argument("--no-filter", dest="no_filter", action="store_true", help="send every event the endpoint listens for")
    s.add_argument("--no-headers", dest="no_headers", action="store_true", help="remove every custom header")
    s.set_defaults(fn="webhook update")
    s = webhook.add_parser("rotate", help="a new signing secret, printed once; the old one signs for 24 more hours")
    s.add_argument("id")
    s.set_defaults(fn="webhook rotate")
    s = webhook.add_parser("pause", help="stop sending; queued sends wait")
    s.add_argument("id")
    s.add_argument("--reason")
    s.set_defaults(fn="webhook pause")
    s = webhook.add_parser("resume", help="send again")
    s.add_argument("id")
    s.set_defaults(fn="webhook resume")
    s = webhook.add_parser("delete", help="delete the endpoint and cancel its queued sends")
    s.add_argument("id")
    s.set_defaults(fn="webhook delete")
    s = webhook.add_parser("send", help="send a custom event (custom.<name>) to one endpoint")
    s.add_argument("id")
    s.add_argument("--type", required=True, help="custom.<name>")
    s.add_argument("--data", help="JSON, or @file with the JSON")
    s.add_argument("--subject")
    s.set_defaults(fn="webhook send")
    s = webhook.add_parser("sends", help="an endpoint's send log; --send ID shows one send with its attempts")
    s.add_argument("id")
    s.add_argument("--send")
    s.add_argument("--state", choices=["ready", "sending", "delivered", "failed", "cancelled", "withheld"])
    s.add_argument("--limit", type=int)
    s.add_argument("--offset", type=int)
    s.set_defaults(fn="webhook sends")
    s = webhook.add_parser("retry", help="send a failed, cancelled or delivered send again now")
    s.add_argument("id")
    s.add_argument("send")
    s.set_defaults(fn="webhook retry")
    s = webhook.add_parser("recover", help="send again every send that gave up since a date (same ids and bodies)")
    s.add_argument("id")
    s.add_argument("--since", required=True, help="a date like 2026-10-20 or a time")
    s.set_defaults(fn="webhook recover")
    s = webhook.add_parser("replay", help="send the events since a date that the endpoint should have had and has no send for")
    s.add_argument("id")
    s.add_argument("--since", required=True, help="a date like 2026-10-20 or a time")
    s.add_argument("--after", help="with --since: continue where a round that said `more` stopped (its next_since and next_after)")
    s.set_defaults(fn="webhook replay")
    s = webhook.add_parser("enable", help="turn an endpoint back on; after Tico turned it off for failing, it offers to send again what it held back")
    s.add_argument("id")
    s.set_defaults(fn="webhook enable")
    s = webhook.add_parser("example", help="send an event type's example to one endpoint")
    s.add_argument("id")
    s.add_argument("--type", help="an event name (default custom.example)")
    s.set_defaults(fn="webhook example")

    s = sub.add_parser("pipeline", help="a task type's steps (count, age, owners, whom they wait on, open PRs), each bot's "
                       "running, queued, held and dead jobs, failed runs by cause, and the engineering manager's exceptions")
    s.add_argument("--type", help="task type id or name; default the engineering board")
    s.add_argument("--owner")
    s.add_argument("--label")
    s.add_argument("--updated-since", help="ISO date-time or a window like 7d")
    s.add_argument("--snoozed", choices=["all", "hide", "only"], help="default all")
    s.add_argument("--bot", help="queues and failures for this bot only")
    s.add_argument("--since", help="failure window: 24h (default), up to 7d, or an ISO date-time")
    s.set_defaults(fn="pipeline")
    s = sub.add_parser("brief", help="alerts, who needs the person, what bots said since --since")
    s.add_argument("--since", help="an ISO date-time; default the last 12 hours")
    s.set_defaults(fn="brief")
    mcp = sub.add_parser("mcp", help="how outside assistants' tool calls perform").add_subparsers(dest="sub")
    s = mcp.add_parser("stats", help="per-tool timing and answer size of assistants' calls")
    s.add_argument("--days", type=int)
    s.add_argument("--via", help="one assistant's token label, e.g. grok-bot")
    s.set_defaults(fn="mcp stats")
    bt = sub.add_parser("needs-you", help="a person's walk through what needs them: respond, commit "
                                          "(backend/batch.py)").add_subparsers(dest="sub")
    s = bt.add_parser("start", help="start the batch of the bot that most needs you, or resume the one in progress")
    s.add_argument("--bot", help="only this bot's items (a slug)")
    s.add_argument("--all", action="store_true", help="every item from every bot in one batch")
    s.add_argument("--fresh", action="store_true", help="drop the batch in progress and build a new list")
    s.set_defaults(fn="needs-you start")
    s = bt.add_parser("next", help="the next item; at the end, the summary to read back")
    s.add_argument("batch")
    s.set_defaults(fn="needs-you next")
    s = bt.add_parser("respond", help="record a response to the current item; nothing applies until commit")
    s.add_argument("batch")
    s.add_argument("kind", choices=["decide", "needs_info", "instruct", "rule", "skip", "later"])
    s.add_argument("--text", default="")
    s.add_argument("--decision", choices=["approve", "decline", "done", "close", "answer"])
    s.add_argument("--item", type=int, help="another item's number")
    s.add_argument("--until", help="with later: an ISO date-time")
    s.set_defaults(fn="needs-you respond")
    s = bt.add_parser("commit", help="apply every recorded response")
    s.add_argument("batch")
    s.set_defaults(fn="needs-you commit")
    s = bt.add_parser("abandon", help="drop the batch without applying anything")
    s.add_argument("batch")
    s.set_defaults(fn="needs-you abandon")

    runs = sub.add_parser("run", help="a bot's runs").add_subparsers(dest="sub")
    s = runs.add_parser("list", help="a bot's recent runs")
    s.add_argument("bot")
    s.add_argument("--since")
    s.set_defaults(fn="run list")
    s = runs.add_parser("export", help="a run's captured provider stream (owners and engineering bots)")
    s.add_argument("attempt", help="the attempt id, from hub run list")
    s.add_argument("--fixture", action="store_true",
                   help="write input.json, <provider>_transcript.ndjson and output.json to a folder")
    s.add_argument("--out", help="with --fixture: the parent folder (default: the current one)")
    s.set_defaults(fn="run export")

    team = sub.add_parser("team", help="team chart and icon").add_subparsers(dest="sub")
    s = team.add_parser("icon", help="set the public team logo (owner; PNG/JPEG/WebP up to 1 MB)")
    s.add_argument("file")
    s.set_defaults(fn="team icon")
    s = team.add_parser(
        "show", help="the team chart: humans, Slack, what they own, the bots with reports_to and group")
    s.add_argument("--person", help="that human and everyone under them")
    s.add_argument("--team", help="a group id, like engineering or sales: that group and the groups in it")
    s.set_defaults(fn="team show")
    s = team.add_parser("budget", help="read or set the team's default runs per hour and per day for bots (owner and admins)")
    for window in ("hour", "day"):
        s.add_argument("--" + window, type=int, help=f"runs allowed per {window} for bots with no budget of their own; 0 for no limit")
    s.set_defaults(fn="team budget")
    seat = sub.add_parser("seat", help="Recruiting seats: roles on the chart with no bot yet").add_subparsers(dest="sub")
    seat.add_parser("list", help="the Recruiting seats").set_defaults(fn="seat list")
    s = seat.add_parser("create", help="add a seat under you or someone below you (a person)")
    s.add_argument("title")
    s.add_argument("--description", help="its job description")
    s.add_argument("--reports-to", dest="reports_to", help="a bot's slug or human:<id>; you by default")
    s.add_argument("--team", help="a group id")
    s.set_defaults(fn="seat create")
    s = seat.add_parser("update", help="change a seat's title, job description, reporting line or group (a person)")
    s.add_argument("seat")
    s.add_argument("--title")
    s.add_argument("--description")
    s.add_argument("--reports-to", dest="reports_to")
    s.add_argument("--team")
    s.set_defaults(fn="seat update")
    s = seat.add_parser("close", help="take a seat off the chart (a person)")
    s.add_argument("seat")
    s.set_defaults(fn="seat close")
    org = sub.add_parser("org", help="the order of the team chart").add_subparsers(dest="sub")
    s = org.add_parser("order", help="a manager's direct reports in the chart's order; --set puts them in an order")
    s.add_argument("manager", help="a bot's slug or a human's id (human:<id> when one could be either)")
    s.add_argument("--set", type=lambda text: [x.strip() for x in text.split(",") if x.strip()], metavar="A,B,C",
                   help="its reports in the new order, comma-separated; the ones left out keep their places")
    s.set_defaults(fn="org order")
    s = org.add_parser("reports-to", help="change who a teammate reports to, as the person who asked")
    s.add_argument("teammate", help="bot:<slug> or human:<id>")
    s.add_argument("reports_to", metavar="manager", help="a bot's slug or human:<id>")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="org reports-to")
    org.add_parser("models", help="the published starter org chart models that recommendations come from").set_defaults(fn="org models")
    health = sub.add_parser("health", help="what is wrong, and where").add_subparsers(dest="sub")
    s = health.add_parser("check", help="what is wrong with the bots, most urgent first, each with its fix")
    s.set_defaults(fn="health check")
    s = health.add_parser("metrics", help="the server's flight recorder: requests, CPU, loop lag, SQL, database size")
    s.add_argument("--minutes", type=int)
    s.add_argument("--section", help="requests, slow, process, sql, db or events; comma-separated")
    s.set_defaults(fn="health metrics")
    changelog = sub.add_parser("changelog", help="shipped product changes and your own read state").add_subparsers(dest="sub")
    s = changelog.add_parser("list", help="what changed; --unread catches up since your last look")
    s.add_argument("--kind", choices=["product", "activity", "all"], default="product")
    s.add_argument("--unread", action="store_true")
    s.add_argument("--since-version", dest="since_version")
    s.add_argument("--q", help="search change titles and bullets")
    s.add_argument("--offset", type=int)
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="changelog list")
    s = changelog.add_parser("mark-read", help="acknowledge only the changes you have reviewed")
    s.add_argument("ids", nargs="+")
    s.set_defaults(fn="changelog mark-read")

    upd = sub.add_parser("update", help="create, read and reply to the bots' daily and weekly updates").add_subparsers(dest="sub")
    s = upd.add_parser("create", help="post your update when Tico asks for it: 1-5 plain-English bullets, "
                                       "or a week in review's slides")
    s.add_argument("body", nargs="?", help="a daily: one to five lines, each starting with '- '")
    s.add_argument("--slides-file", help="a week in review: JSON {goal, kpis, done, focus, blockers}")
    s.add_argument("--kind", choices=("daily", "weekly"))
    s.add_argument("--day", help="YYYY-MM-DD, only when Tico asks you to redo a past day")
    s.set_defaults(fn="update create")
    s = upd.add_parser("redo", help="ask the bots again for a past day's update in the current shape (owner)")
    s.add_argument("--day", required=True)
    s.add_argument("--kind", choices=("daily", "weekly"))
    s.add_argument("--bot", dest="bots", action="append", help="only this bot (repeatable)")
    s.add_argument("--with-managers", dest="managers", action="store_true", help="also the bots above them")
    s.set_defaults(fn="update redo")
    s = upd.add_parser("list", help="updates from bots and people, newest first")
    s.add_argument("--kind", choices=("daily", "weekly"))
    s.add_argument("--bot", help="one author: a bot slug or human:<id>")
    s.add_argument("--scope", choices=("all", "direct"), help="direct: only your direct reports")
    s.add_argument("--unread", action="store_true")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="update list")
    s = upd.add_parser("show")
    s.add_argument("update")
    s.set_defaults(fn="update show")
    s = upd.add_parser("mark-read", help="mark updates read (--unread to mark them unread)")
    s.add_argument("ids", nargs="*")
    s.add_argument("--all", action="store_true")
    s.add_argument("--unread", dest="read", action="store_false")
    s.set_defaults(fn="update mark-read")
    s = upd.add_parser("reply")
    s.add_argument("update")
    s.add_argument("text")
    s.set_defaults(fn="update reply")
    s = upd.add_parser("settings", help="turn a bot's daily or weekly update on or off")
    s.add_argument("bot")
    s.add_argument("--daily", choices=("on", "off"))
    s.add_argument("--weekly", choices=("on", "off"))
    s.set_defaults(fn="update settings")
    grok = sub.add_parser("grokbot", help="your Grok Bots in Tico (docs/external-agent-sync.md)").add_subparsers(dest="sub")
    s = grok.add_parser("sync", help="sync Grok Bots from a JSON file: {\"bots\": [...], \"source\": ...}")
    s.add_argument("--file", required=True, help="the JSON body hub_grokbot_sync takes")
    s.set_defaults(fn="grokbot sync")
    ext = sub.add_parser("external", help="your bots on another platform in Tico (docs/external-agent-sync.md)").add_subparsers(dest="sub")
    s = ext.add_parser("sync", help="sync Grok Bots or Dots from a JSON file: {\"bots\": [...], \"source\": ...}")
    s.add_argument("--provider", required=True, choices=("grokbot", "dots", "muse"))
    s.add_argument("--file", required=True, help="the JSON body hub_external_sync takes")
    s.set_defaults(fn="external sync")

    calendar = sub.add_parser("calendar", help="read or schedule company appointments").add_subparsers(dest="sub")
    s = calendar.add_parser("list", help="appointments on a company calendar")
    s.add_argument("--calendar", default="", help="roster email; default the company owner's")
    s.set_defaults(fn="calendar list")
    s = calendar.add_parser("schedule", help="queue one calendar appointment")
    s.add_argument("--calendar", default="", help="roster email; default the company owner's")
    s.add_argument("--title", required=True)
    s.add_argument("--start", required=True, help="ISO-8601 timestamp with timezone")
    s.add_argument("--end", required=True, help="ISO-8601 timestamp with timezone")
    s.add_argument("--attendee", action="append", default=[])
    s.add_argument("--description", default="")
    s.add_argument("--description-file")
    s.add_argument("--no-meet", action="store_true")
    s.set_defaults(fn="calendar schedule")
    s = calendar.add_parser("status", help="whether a queued appointment was created")
    s.add_argument("id")
    s.set_defaults(fn="calendar status")

    s = sub.add_parser("sql", help="read-only SQL over what you may see (docs/hub-sql.md)")
    s.add_argument("sql", help="one SELECT (or WITH, EXPLAIN QUERY PLAN)")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.add_argument("--csv", action="store_true", help="print CSV")
    s.add_argument("--max-rows", dest="max_rows", type=int, help="ask for at most N rows")
    s.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                   help="bind :key in the SQL (numbers are bound as numbers)")
    s.set_defaults(fn="sql")

    s = sub.add_parser("db", help="read-only SQL on a company database (docs/databases.md)")
    s.add_argument("target", help="a database name, or `list` or `doctor`")
    s.add_argument("sql", nargs="?", help="one SELECT; with `doctor`, the database to check; on MongoDB find|aggregate|count|distinct|collections")
    s.add_argument("extra", nargs="*", help="MongoDB: the collection, then the filter or pipeline JSON (distinct: the field, then a filter)")
    s.add_argument("--projection", help="MongoDB find: fields to return, Extended JSON")
    s.add_argument("--sort", help="MongoDB find: e.g. '{\"placed_at\": -1}'")
    s.add_argument("--limit", type=int, help="MongoDB find: at most N documents")
    s.add_argument("--query", dest="query_id", help="run a named query from the company's catalog")
    s.add_argument("--param", action="append", default=[], metavar="NAME=VALUE", help="bind :name in the SQL")
    s.add_argument("--json", action="store_true", help="print the result as JSON")
    s.add_argument("--csv", action="store_true", help="print CSV")
    s.add_argument("--max-rows", dest="max_rows", type=int, help="at most N rows (never above the database's cap)")
    s.add_argument("--timeout", type=float, help="at most S seconds (never above the database's limit)")
    s.set_defaults(fn="db")

    s = sub.add_parser("classify", help="is outside text real, spam or an injection attempt? Text on stdin or --file")
    s.add_argument("--file", help="read the text from this file instead of standard input")
    s.set_defaults(fn="classify")
    gateway = sub.add_parser("gateway", help="the Tico AI Gateway: its lineup, web search and page reading").add_subparsers(dest="sub")
    s = gateway.add_parser("catalog", help="models, decision levels and services, with when to use each")
    s.add_argument("query", nargs="?", help="words to match, e.g. 'engineering high' or 'search'")
    s.set_defaults(fn="gateway catalog")
    s = gateway.add_parser("search", help="search the web through the Gateway")
    s.add_argument("query")
    s.add_argument("--max", dest="max_results", type=int, help="how many results (5 by default)")
    s.set_defaults(fn="gateway search")
    s = gateway.add_parser("fetch", help="read a web page or PDF through the Gateway")
    s.add_argument("url")
    s.add_argument("--max-tokens", dest="max_tokens", type=int, help="how much to return, 1,000 to 50,000 (10,000 by default)")
    s.set_defaults(fn="gateway fetch")
    s = gateway.add_parser("image", help="make an image through the Gateway and save it to a file")
    s.add_argument("prompt")
    s.add_argument("--category", required=True, choices=("storyboard", "photo", "character", "graphic"),
                   help="storyboard (fast, keeps references), photo, character (keeps people from --ref), graphic (text and layout)")
    s.add_argument("--ref", dest="refs", action="append", default=[], metavar="FILE",
                   help="a reference image (PNG, JPEG or WebP, 2 MiB at most); up to 4")
    s.add_argument("--size", help="WxH, for example 1920x1080 (the nearest aspect ratio is asked for)")
    s.add_argument("--count", type=int, help="how many images, 1 to 4 (1 by default)")
    s.add_argument("--out", required=True, metavar="PATH",
                   help="the file for the first image; more are saved as name-2.png, name-3.png...")
    s.set_defaults(fn="gateway image")
    decision = sub.add_parser("decision", help="the decision model: typed questions about a state").add_subparsers(dest="sub")
    s = decision.add_parser("ask", help="ask the decision model typed questions about a JSON state (questions/README.md)")
    s.add_argument("--set", dest="question_set", help="a question set: questions/<name>.json in the Tico checkout")
    s.add_argument("--questions-file", dest="questions_file",
                   help="instead of --set: a JSON file holding the questions map (or an object with `questions`)")
    s.add_argument("--state-file", dest="state_file", help="the JSON state the questions are about; `-` reads stdin")
    s.add_argument("--option", action="append", default=[], metavar="QUESTION=FILE",
                   help="complete a dynamic choice with a JSON map of option -> description from FILE")
    s.add_argument("--label", help="how the call is grouped in the audit; the set's id@version by default")
    s.add_argument("--list", action="store_true", help="print the question sets this checkout has")
    s.set_defaults(fn="decision ask")
    s = decision.add_parser("outcome", help="record what you did with a decision answer, once per call (its ref)")
    s.add_argument("--ref", required=True, help="the `ref` the decision answer returned")
    s.add_argument("--verdict", required=True, help="the answer you acted on, in a few words")
    s.add_argument("--action", required=True, help="what you did with it; say `overrode` when you went against it")
    s.add_argument("--label", help="the call's label; the one it was made with by default")
    s.set_defaults(fn="decision outcome")
    s = decision.add_parser("readout", help="per question set: the last days' calls, cost, Jev or fallback and answer "
                                            "spread, and the month's spend against the cap")
    s.add_argument("--days", type=int, help="how many days back from now (1-31, 7 by default)")
    s.set_defaults(fn="decision readout")

    # A new bot: BotOps builds the chosen template in the workspace (`hub bot create`) or only registers it (--record-only).
    # The Librarian (docs/librarian.md): ask a question, and read a linked doc on this computer.
    s = docs.add_parser("ask", help="ask the Librarian about the team's docs and wait for the answer")
    s.add_argument("question")
    s.add_argument("--wait", type=float, default=120, help="seconds to wait for the answer (default 120)")
    s.set_defaults(fn="doc ask")
    s = docs.add_parser("ask-status", help="collect the same Librarian answer without sending another question")
    s.add_argument("conversation_id")
    s.add_argument("message_id")
    s.add_argument("--wait", type=float, default=0)
    s.set_defaults(fn="doc ask-status")
    s = docs.add_parser("fetch", help="read a public link as text; runs on this computer, never on the server")
    s.add_argument("url")
    s.add_argument("--max-chars", dest="max_chars", type=int, default=30000)
    s.set_defaults(fn="doc fetch")
    sub.add_parser("template", help="the bot templates this company can pick from").add_subparsers(dest="sub").add_parser(
        "list", help="the bot templates this company can pick from").set_defaults(fn="template list")
    bot = sub.add_parser("bot", help="build a bot from a template (BotOps), change it, place it, its status").add_subparsers(dest="sub")
    s = bot.add_parser("create", help="build a bot from a template in the workspace and register it; --record-only only registers")
    s.add_argument("slug", nargs="?", help="leave out: Tico picks one from --name, else the template's name")
    s.add_argument("--template", help="a template from `hub template list`")
    s.add_argument("--record-only", dest="record_only", action="store_true",
                   help="only register the bot with Tico (planned), as the person who asked; build nothing")
    s.add_argument("--name", help="what people call this bot; the template card's name by default")
    s.add_argument("--description")
    s.add_argument("--reports-to", help="with --record-only: a bot slug, or human:<id>; the requester by default")
    s.add_argument("--model", help="with --record-only: `hermes` or `openclaw` for a bot run by a Hermes or OpenClaw profile; the company's default otherwise")
    s.set_defaults(fn="bot create")
    s = bot.add_parser("update", help="change a bot's settings with your rights, or the requester's (BotOps)")
    s.add_argument("slug")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID",
                   help="the person's message to BotOps asking for this change; by default the one that started this run")
    s.add_argument("--reports-to", help="a bot slug, or human:<id>")
    s.add_argument("--display-name")
    s.add_argument("--description")
    s.add_argument("--repo", help="its GitHub repository: <org>/bot-<slug>")
    s.add_argument("--status", choices=("active", "paused", "planned"))
    s.add_argument("--session", choices=("bot", "task"), help="one provider session per bot or per task")
    s.add_argument("--subtask-session", choices=("parent", "own"),
                   help="parent (default): a subtask continues its parent task's session; own: its own session")
    s.add_argument("--task-goal", choices=("on", "off"), help="on: work each task it owns as a goal; off (default): not")
    s.add_argument("--review-model", choices=("check", "own"),
                   help="check: every subtask it is handed under another's task runs on the company's check model, "
                        "for a reviewer bot; own (default): its own model")
    s.add_argument("--capture-stream", choices=("on", "off"),
                   help="on (owner only): keep each run's raw provider stream a week for hub run export; off (default): not")
    s.add_argument("--subscribe-updates", metavar="templates,tico|none",
                   help="Subscribe to updates, as deferred messages for its next run: templates (its template's changes), "
                        "tico (Tico releases), both, or none")
    s.add_argument("--shared", action=argparse.BooleanOptionalAction, default=None, help="allow branches")
    s.set_defaults(fn="bot update")
    s = bot.add_parser("check", help="what preflight would still refuse about that repository")
    s.add_argument("slug")
    s.set_defaults(fn="bot check")
    s = bot.add_parser("access", help="show or set who may see, read and write to a bot")
    s.add_argument("slug")
    for level in ("see", "read", "write"):
        s.add_argument("--" + level, help="everyone, or a comma list: human ids, group:<id>, bot:<slug>")
    s.set_defaults(fn="bot access")
    s = bot.add_parser("owners", help="add or remove the humans who own a bot")
    s.add_argument("slug")
    s.add_argument("--add", nargs="+", default=[], metavar="HUMAN")
    s.add_argument("--remove", nargs="+", default=[], metavar="HUMAN")
    s.set_defaults(fn="bot owners")
    s = bot.add_parser("mail", help="show or set a bot's mail sending without approval (a person only)")
    s.add_argument("slug")
    s.add_argument("--send", dest="outbound_send", action=argparse.BooleanOptionalAction, default=None,
                   help="--send: send without a per-message approval to the recipients docs/mail.md lists; --no-send: off")
    s.add_argument("--forward-to", dest="forward_to", help="comma list of forward addresses; an empty string clears it")
    s.add_argument("--from-bot-yaml", dest="from_request", action="store_true",
                   help="take what the bot's bot.yaml asks, as its computer reported it")
    s.set_defaults(fn="bot mail")
    s = bot.add_parser("setup-done", help="mark a bot's onboarding complete, as a person above it on the org chart")
    s.add_argument("slug", nargs="?", help="the bot; the one running this command by default")
    s.set_defaults(fn="bot setup-done")
    s = bot.add_parser("ready", help="an onboarding bot says it is ready, with what it learned")
    s.add_argument("--summary", required=True, help="a few lines on what you learned and what you will do first")
    s.set_defaults(fn="bot ready")
    s = bot.add_parser("place", help="put a bot on a computer, as the person who asked (BotOps)")
    s.add_argument("bot")
    s.add_argument("--computer", help="a computer's label or id; the best one that takes it by default")
    s.set_defaults(fn="bot place")
    s = bot.add_parser("go-live", help="place it if needed, turn it on and start its setup (BotOps)",
                       formatter_class=argparse.RawDescriptionHelpFormatter, epilog=GO_LIVE_HELP)
    s.add_argument("bot")
    s.add_argument("--computer")
    s.add_argument("--no-setup", dest="no_setup", action="store_true", help="do not start its setup chat")
    s.set_defaults(fn="bot go-live")
    s = bot.add_parser("restore", help="bring an archived bot back, as the person who asked (BotOps)")
    s.add_argument("bot")
    s.set_defaults(fn="bot restore")
    s = bot.add_parser("archive", help="archive a bot you manage")
    s.add_argument("bot")
    s.add_argument("--successor")
    s.set_defaults(fn="bot archive")
    s = bot.add_parser("keep-tasks-going", help="read or set automatic task starts: N, off, or unlimited")
    s.add_argument("bot")
    s.add_argument("limit", nargs="?", type=lambda v: None if v == 'unlimited' else 0 if v == 'off' else int(v), default=argparse.SUPPRESS)
    s.set_defaults(fn="bot keep-tasks-going")
    s = bot.add_parser("turns-at-once",
                       help="read or set the most turns the bot runs at once across all computers: N or unlimited")
    s.add_argument("bot")
    s.add_argument("limit", nargs="?", type=lambda v: None if v == 'unlimited' else int(v), default=argparse.SUPPRESS)
    s.set_defaults(fn="bot turns-at-once")
    s = bot.add_parser("browser-exit",
                       help="read or set the computer a bot browses the web through: a computer id, or none for its own")
    s.add_argument("bot")
    s.add_argument("computer", nargs="?", type=lambda v: None if v == "none" else v, default=argparse.SUPPRESS)
    s.set_defaults(fn="bot browser-exit")
    s = bot.add_parser("budget", help="read or set a bot's runs per hour and per day, or release it from a hold")
    s.add_argument("bot")
    s.add_argument("action", nargs="?", choices=["release"], help="release the bot from a hold instead of changing a budget")
    for window in ("hour", "day"):
        s.add_argument("--" + window, type=lambda v: v if v == "default" else int(v),
                       help=f"runs allowed per {window}: a number, 0 for no limit, or default to follow the team")
    s.set_defaults(fn="bot budget")
    s = bot.add_parser("profile", help="read or set a bot's profile: what wakes it and which tasks it takes")
    s.add_argument("bot")
    s.add_argument("profile", nargs="?", choices=["doer", "reviewer", "lead", "manager", "responder", "monitor", "custom"],
                   help="the profile to set; leave out to read the bot's and the choices")
    s.add_argument("--batch-seconds", dest="batch_seconds", type=int, help="how long news waits to be batched (seconds)")
    s.add_argument("--sweep-seconds", dest="sweep_seconds", type=int, help="least seconds between two runs of the same sweep")
    s.add_argument("--digest-hour", dest="digest_hour", type=int, help="the hour (0-23, team time) of a lead's daily digest")
    s.add_argument("--pulls", choices=["all", "review", "none"], help="custom: which open tasks it starts by itself")
    s.add_argument("--sweeps", type=lambda v: {"on": True, "off": False}[v], metavar="on|off",
                   help="custom: whether stall checks and automations may wake it")
    s.add_argument("--takes", choices=["all", "review", "watcher", "none"], help="custom: which tasks it may be given")
    s.add_argument("--confirm-drop-rules", dest="confirm_drop_rules", action="store_true", default=None,
                   help="leaving custom deletes the wake rules it copied into the rules table")
    s.set_defaults(fn="bot profile")
    s = bot.add_parser("release",
                       help="release a bot Tico stopped (failed runs, too many runs, or a run it could not start); "
                            "its queue starts again")
    s.add_argument("bot")
    s.set_defaults(fn="bot release")
    s = bot.add_parser("model", help="list the models, or change this bot's (BotOps)")
    s.add_argument("bot")
    s.add_argument("model", nargs="?", help="a model id or name; leave out to list")
    s.add_argument("--effort")
    s.add_argument("--on-behalf-of", help="the open task a person asked you for, when something else started this run")
    s.set_defaults(fn="bot model")
    s = bot.add_parser("task-model", help="the model the bot's tasks run on, with its effort and harness; --clear puts "
                                          "them back on the bot's own model")
    s.add_argument("bot")
    s.add_argument("model", nargs="?", help="a model id or name; leave out to change only --effort or --harness")
    s.add_argument("--effort")
    s.add_argument("--harness")
    s.add_argument("--clear", action="store_true", help="the bot's tasks run on its own model again")
    s.add_argument("--on-behalf-of", help="the open task a person asked you for, when something else started this run")
    s.set_defaults(fn="bot task-model")
    for verb in ("pause", "resume"):
        s = bot.add_parser(verb, help=f"{verb} a bot (BotOps)")
        s.add_argument("bot")
        s.set_defaults(fn="bot " + verb)
    s = bot.add_parser("scope", help="limit a bot's claims to one task and its subtasks, or lift that (BotOps)")
    s.add_argument("bot")
    s.add_argument("task", nargs="?", help="the task it may still work on")
    s.add_argument("--release", action="store_true", help="lift the limit; its other work becomes claimable again")
    s.set_defaults(fn="bot scope")
    s = bot.add_parser("branch", help="make your branch of a bot that allows branches")
    s.add_argument("bot")
    s.add_argument("--computer", help="your computer's label or id")
    s.set_defaults(fn="bot branch")
    s = bot.add_parser("copy", help="copy a bot into a new one the requester owns (BotOps)")
    s.add_argument("bot", help="the bot to copy")
    s.add_argument("--slug", help="the copy's slug; <bot>-copy by default")
    s.add_argument("--name", help="what people call the copy")
    s.add_argument("--with-memory", dest="with_memory", action="store_true", help="also copy the original's memory, notes and state")
    s.add_argument("--computer", help="a computer's label or id to put the copy on")
    s.set_defaults(fn="bot copy")
    s = bot.add_parser("update-from-original", help="bring a copy's instructions up to date with the bot it was copied from (BotOps)")
    s.add_argument("bot", help="the copy")
    s.add_argument("--resolved", action="store_true", help="conflicts settled by hand and committed: record the copy as up to date")
    s.set_defaults(fn="bot update-from-original")
    s = bot.add_parser("suggest-to-original", help="suggest a copy's instruction changes to the bot it was copied from (BotOps)")
    s.add_argument("bot", help="the copy")
    s.add_argument("--paths", nargs="+", metavar="PATH", help="only these files or folders, like AGENT.md or skills/triage")
    s.add_argument("--title")
    s.set_defaults(fn="bot suggest-to-original")
    s = bot.add_parser("repos", help="read or set a bot’s repository access")
    s.add_argument("bot")
    modes = s.add_mutually_exclusive_group()
    modes.add_argument("--all", action="store_true")
    modes.add_argument("--own", action="store_true")
    modes.add_argument("--chosen", nargs="*", metavar="OWNER/REPO[:read|:write[+actions|-actions][+edit|-edit]]",
                       help="+actions lets the bot run workflows there, +edit edit workflow files (Owner or admin)")
    s.set_defaults(fn="bot repos")
    repos = sub.add_parser("repo", help="team repositories").add_subparsers(dest="sub")
    s = repos.add_parser("list")
    s.set_defaults(fn="repo list")
    for verb in ("tick", "untick"):
        s = repos.add_parser(verb)
        s.add_argument("full_name")
        s.set_defaults(fn="repo " + verb)
    s = repos.add_parser("review", help="submit a GitHub review at an exact PR head; shared App falls back to COMMENT")
    s.add_argument("repository", metavar="OWNER/REPO")
    s.add_argument("number", type=int)
    s.add_argument("--head", required=True, help="reviewed full 40-character commit SHA")
    s.add_argument("--verdict", required=True, choices=("approve", "changes"))
    text = s.add_mutually_exclusive_group(required=True)
    text.add_argument("--body")
    text.add_argument("--body-file", help="review text file; VERDICT is appended automatically")
    s.add_argument("--operation-id", help="stable retry key; defaults to a hash of the review")
    s.set_defaults(fn="repo review")
    s = repos.add_parser("product-create", help="preview, then confirm an exact private empty product repository "
                         "(the Owner, or a bot with Create product repositories)")
    s.add_argument("name", help="exact GitHub repository name, without an organization")
    s.add_argument("--confirm", metavar="ORG/NAME", help="the exact preview target, instead of typing it at a terminal")
    s.set_defaults(fn="repo product-create")
    s = bot.add_parser("repo-create", help="owner or BotOps: create <org>/bot-<slug> from a template, or empty")
    s.add_argument("slug")
    s.add_argument("--template", default="ticoteam/botops", metavar="OWNER/REPO")
    s.add_argument("--empty", action="store_true", help="an empty private repository, for a bot whose history is on a computer")
    s.set_defaults(fn="bot repo-create")
    s = bot.add_parser("recent", help="the bots you have been working with lately and where each stands")
    s.add_argument("--days", type=int, help="how far back (default 7)")
    s.add_argument("--limit", type=int, help="at most this many bots (default 10)")
    s.set_defaults(fn="bot recent")
    st = bot.add_parser("status", help="a bot's live status: set yours, list every bot's, read the history").add_subparsers(dest="subsub")
    s = st.add_parser("set", help="one factual line about what you are doing now")
    s.add_argument("focus")
    s.add_argument("--state")
    s.add_argument("--task")
    s.add_argument("--bot")
    s.set_defaults(fn="bot status set")
    s = st.add_parser("list", help="every bot you may see with its live status")
    s.add_argument("--team")
    s.set_defaults(fn="bot status list")
    s = st.add_parser("history", help="a bot's status history")
    s.add_argument("bot")
    s.add_argument("--since")
    s.set_defaults(fn="bot status history")
    skill = sub.add_parser("skill", help="a bot's skills: copy one to other bots").add_subparsers(dest="sub")
    s = skill.add_parser("copy", help="copy skills/<skill>/ from one bot into others, a commit in each (BotOps)")
    s.add_argument("skill", help="the skill's folder name under skills/")
    s.add_argument("--from", dest="bot", required=True, metavar="BOT", help="the bot that has the skill")
    s.add_argument("--to", nargs="+", required=True, metavar="BOT", help="the bots to copy it to")
    s.add_argument("--replace", action="store_true", help="replace a different skill of the same name")
    s.set_defaults(fn="skill copy")
    agent = sub.add_parser("agent", help="external agents: connect a Hermes profile to a bot").add_subparsers(dest="sub")
    pair = agent.add_parser("pair", help="approve or decline the code a Hermes profile printed when it asked to pair").add_subparsers(dest="subsub")
    s = pair.add_parser("show", help="preview the profile, computer and expiry without changing a credential")
    s.add_argument("code")
    s.set_defaults(fn="agent pair show")
    s = pair.add_parser("approve", help="connect the profile that printed this code to a bot (BotOps, or its owner or an admin)")
    s.add_argument("code", help="like K7QM-4F2P")
    s.add_argument("--bot", required=True, help="the bot's slug; it must use the hermes harness")
    s.set_defaults(fn="agent pair approve")
    s = pair.add_parser("decline", help="turn down the profile that printed this code")
    s.add_argument("code")
    s.set_defaults(fn="agent pair decline")
    humans = sub.add_parser("human", help="the humans on the team: add one, list who is on it").add_subparsers(dest="sub")
    s = humans.add_parser("add", help="add a human to the roster and the sign-in list")
    s.add_argument("email")
    s.add_argument("--name")
    s.add_argument("--title")
    s.add_argument("--reports-to")
    s.set_defaults(fn="human add")
    humans.add_parser("list", help="the humans on the roster").set_defaults(fn="human list")
    s = humans.add_parser("access", help="show or set who may see, read and write to a human")
    s.add_argument("human")
    for level in ("see", "read", "write"):
        s.add_argument("--" + level, help="everyone, or a comma list: human ids, group:<id>, bot:<slug>")
    s.set_defaults(fn="human access")
    groups = sub.add_parser("group", help="the groups: sub-teams that nest, holding humans and bots").add_subparsers(dest="sub")
    groups.add_parser("list", help="the groups with their humans and bots").set_defaults(fn="group list")

    s = groups.add_parser("update", help="create (no group given), rename, move, lead or fill a group (an owner or an admin)")
    s.add_argument("group", nargs="?", help="the group's id; leave out to create one from --name")
    s.add_argument("--name")
    s.add_argument("--parent", help="the group it is in; an empty one is the top")
    s.add_argument("--add-human", dest="add_humans", action="append", metavar="HUMAN", help="put a human in it")
    s.add_argument("--add-bot", dest="add_bots", action="append", metavar="BOT", help="put a bot in it")
    s.add_argument("--remove-human", dest="remove_humans", action="append", metavar="HUMAN")
    s.add_argument("--remove-bot", dest="remove_bots", action="append", metavar="BOT")
    s.add_argument("--lead", help="bot:<slug> or person:<id>; an empty one clears it")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="group update")
    s = groups.add_parser("delete", help="delete a group; its teammates and groups move up to its parent")
    s.add_argument("group")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="group delete")
    s = groups.add_parser("access", help="show or set who may see, read and write to a group")
    s.add_argument("group")
    for level in ("see", "read", "write"):
        s.add_argument("--" + level, help="everyone, or a comma list: human ids, group:<id>, bot:<slug>")
    s.set_defaults(fn="group access")
    s = sub.add_parser("api", help="BotOps: any v2 route, as the person who asked")
    s.add_argument("method", type=str.upper, choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    s.add_argument("path", help="/api/v2/... or the part after it")
    s.add_argument("body", nargs="?", help="a JSON body for a write; - reads it from standard input")
    s.add_argument("--on-behalf-of", help="the open task a person asked you for, when something else started this run")
    s.set_defaults(fn="api")
    comp = sub.add_parser("computer", help="the computers a bot may go on").add_subparsers(dest="sub")
    s = comp.add_parser("list", help="the computers a bot may go on, one short row each; --computer for one in full")
    s.add_argument("--computer", help="one computer's label or id: its full detail, large sections capped")
    s.add_argument("--section", choices=["assigned", "repositories", "bots", "runtimes", "harnesses", "recent_errors"],
                   help="with --computer: one page of this section only")
    s.add_argument("--offset", type=int, help="with --section: the first item to return")
    s.set_defaults(fn="computer list")
    s = comp.add_parser("performance", help="a computer's CPU, memory and disk over time, overloads and what ran then")
    s.add_argument("computer", help="the computer's label or id")
    s.add_argument("--hours", type=int, default=24)
    s.set_defaults(fn="computer performance")
    s = comp.add_parser("turns-at-once",
                        help="set how many turns a computer runs at once: N, or automatic for its own capacity")
    s.add_argument("computer", help="the computer's id")
    s.add_argument("limit", type=lambda v: None if v == "automatic" else int(v), help="N (1 to 256), or automatic")
    s.set_defaults(fn="computer turns-at-once")
    s = comp.add_parser("browser-exit", help="read or set whether a computer shares its internet with bots: on or off")
    s.add_argument("computer", help="the computer's id")
    s.add_argument("sharing", nargs="?", choices=["on", "off"], default=argparse.SUPPRESS)
    s.add_argument("--bots", type=lambda v: [b.strip() for b in v.split(",") if b.strip()],
                   help="comma-separated bot slugs it shares with")
    s.add_argument("--cap-gb", dest="daily_cap_gb", type=float, help="the daily cap in GB (default 5)")
    s.add_argument("--usage", action="store_true", default=None, help="also show the last 7 days of what went through it")
    s.set_defaults(fn="computer browser-exit")
    cred = sub.add_parser("credential", help="ask for a credential in the chat, store one, list them").add_subparsers(dest="sub")
    s = cred.add_parser("request", help="open a card in the chat for the person to type the credential into")
    s.add_argument("env", help="the variable's name, like JIRA_BASIC_AUTH")
    s.add_argument("--for-bot", dest="for_bot")
    s.add_argument("--label", help="what it is: 'your Jira login'")
    s.add_argument("--format", help="its shape: you@company.com:API token")
    s.add_argument("--help-url", dest="help_url", help="an https page where they make one")
    s.add_argument("--kind", choices=["api_key", "token", "password", "connection"])
    s.set_defaults(fn="credential request")
    s = cred.add_parser("set", help="store a credential a person gave you, for one bot; the value is read from stdin")
    s.add_argument("env")
    s.add_argument("--for-bot", dest="for_bot", required=True)
    s.add_argument("--name")
    s.add_argument("--kind", choices=["api_key", "token", "password", "connection"])
    s.add_argument("--username")
    s.add_argument("--no-redact", dest="no_redact", action="store_true", help="leave the pasted words in the chat")
    s.set_defaults(fn="credential set")
    cred.add_parser("list", help="names, variables and which bots have each; never a value").set_defaults(fn="credential list")
    s = cred.add_parser("grant", help="give a bot a stored credential")
    s.add_argument("credential", help="its name in Credentials (or its variable's name)")
    s.add_argument("--to", dest="to_bot", required=True, metavar="BOT")
    s.set_defaults(fn="credential grant")
    s = cred.add_parser("revoke", help="take a stored credential away from a bot")
    s.add_argument("credential")
    s.add_argument("--from", dest="from_bot", required=True, metavar="BOT")
    s.set_defaults(fn="credential revoke")
    s = cred.add_parser("delete", help="erase a Credential and all its grants (credential administrators)")
    s.add_argument("credential")
    s.set_defaults(fn="credential delete")
    s = cred.add_parser("import", help="move one variable from a bot's own secrets file into Credentials, granted to that bot")
    s.add_argument("env", metavar="VAR", help="the variable's name, like JIRA_BASIC_AUTH")
    s.add_argument("--from-bot", dest="from_bot", required=True, metavar="BOT")
    s.add_argument("--name", help="what to call it in Credentials; the variable's name by default")
    s.add_argument("--kind", choices=["api_key", "token", "password", "connection"])
    s.add_argument("--wait", type=int, help="seconds to wait for the computer to answer (30; at most 60)")
    s.set_defaults(fn="credential import")
    slack = sub.add_parser("slack", help="the Slack channels bots may read and post in").add_subparsers(dest="sub")
    chan = slack.add_parser("channel", help="list, add or remove a channel").add_subparsers(dest="subsub")
    chan.add_parser("list", help="the listed channels, their readers, and whether bots may post").set_defaults(fn="slack channel list")
    s = chan.add_parser("add", help="list a channel, or add readers to one already listed (an owner or an admin)")
    s.add_argument("channel", help="#name or id (C0123456789)")
    s.add_argument("--reader", dest="readers", action="append", metavar="BOT", help="a bot that reads it; repeat for several")
    s.add_argument("--post", dest="post", action="store_true", default=None, help="bots may post there (the default for a new channel)")
    s.add_argument("--no-post", dest="post", action="store_false", help="bots may only read it")
    s.add_argument("--note", help="what the channel is for")
    s.add_argument("--digest-hours", dest="digest_hours", type=float, help="readers get it at most once in this many hours")
    s.add_argument("--name", help="its name, when you give the id")
    s.set_defaults(fn="slack channel add")
    s = chan.add_parser("remove", help="take a channel off the list, or one reader off it")
    s.add_argument("channel")
    s.add_argument("--reader", help="only this bot stops reading it")
    s.set_defaults(fn="slack channel remove")
    chan.add_parser("import", help="store the channels of the old registry/slack-channels.yaml").set_defaults(fn="slack channel import")
    support = sub.add_parser("support", help="tell the Tico team about a gap or a fault").add_subparsers(dest="sub")
    s = support.add_parser("file", help="a Confirm card shows the message; nothing is sent until the person confirms")
    s.add_argument("message")
    s.set_defaults(fn="support file")
    s = support.add_parser("report", help="draft a Tico bug report; a person must approve it before it is sent")
    text = s.add_mutually_exclusive_group(required=True)
    text.add_argument("--message", help="the whole report: what failed, since when, the exact error, what you tried")
    text.add_argument("--file", help="a file holding the report (UTF-8)")
    s.add_argument("--no-ids", dest="no_ids", action="store_true", help="send only the message: not the version and install ID")
    s.set_defaults(fn="support report")
    s = support.add_parser("send", help="send a report a person approved; filed as the person who approved it")
    s.add_argument("--approval", required=True, help="the approval id `support report` printed")
    s.set_defaults(fn="support send")
    monitor = sub.add_parser("health-monitor", help="the Health Monitor's findings and reports (docs/health-monitor.md)").add_subparsers(dest="sub")
    monitor.add_parser("findings", help="what the last hourly scan found, and each signature's report").set_defaults(fn="health-monitor findings")
    s = monitor.add_parser("report", help="report a finding as a Tico bug: one ticket per signature, with the redacted diagnostics")
    s.add_argument("key", nargs="?", help="the finding's key or signature, from its task")
    s.add_argument("--process", help="instead of a key: what the watcher saw busy, process:<name> or container:<name>")
    s.add_argument("--cpu", type=float, default=0, help="with --process: its CPU percent")
    s.add_argument("--mb", type=float, default=0, help="with --process: its memory in MB")
    s.set_defaults(fn="health-monitor report")
    fin = sub.add_parser("finance", help="the books, as you: search, reports, metrics, actions, your work queue "
                         "(docs/finance-tools.md)").add_subparsers(dest="sub")
    s = fin.add_parser("search", help="entries, bank and card rows, bills, invoices, contacts and rules you may see")
    s.add_argument("--kind", choices=["entry", "feed", "bill", "invoice", "contact", "rule", "card"])
    s.add_argument("--q")
    for flag in ("--status", "--contact-id", "--vendor", "--account-id", "--money-account-id", "--start", "--end",
                 "--cursor"):
        s.add_argument(flag)
    s.add_argument("--aging", choices=["current", "1_30", "31_60", "61_90", "over_90"])
    s.add_argument("--contact-kind", choices=["vendor", "customer", "both"])
    s.add_argument("--rule-kind", choices=["gate", "posting"])
    s.add_argument("--active", type=lambda v: v.lower() in ("1", "true", "yes", "on"), help="contacts: true or false")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="finance search")
    s = fin.add_parser("report", help="one report cut to your view; rows past --top-n fold into Other")
    s.add_argument("kind", choices=["pnl", "balance_sheet", "cash_flow", "budget_vs_actual", "ap_aging", "ar_aging",
                                    "spend_by", "close"])
    s.add_argument("--start"); s.add_argument("--end")
    s.add_argument("--period", choices=["day", "week", "month", "quarter", "year"])
    s.add_argument("--basis", choices=["cash", "accrual"])
    s.add_argument("--compare", choices=["prior"])
    s.add_argument("--top-n", type=int)
    s.add_argument("--by", choices=["department", "team", "teammate", "bot", "person", "vendor", "account"])
    s.add_argument("--month", help="close: the month, YYYY-MM")
    s.set_defaults(fn="finance report")
    s = fin.add_parser("metric", help="one number you may see, with when it was worked out")
    s.add_argument("name", choices=["cash", "burn", "runway_months", "revenue", "spend", "budget_remaining",
                                    "budget_pct_used", "ap_due", "ar_overdue", "uncategorized_count",
                                    "unreconciled_days"])
    for flag in ("--budget-id", "--start", "--end", "--account-id", "--team", "--teammate"):
        s.add_argument(flag)
    s.add_argument("--days", type=int)
    s.set_defaults(fn="finance metric")
    s = fin.add_parser("act", help="ask for one Finance action (help lists kinds)")
    s.add_argument("kind")
    s.add_argument("--payload", type=_finance_payload, help="the kind's fields as JSON, or - to read stdin")
    s.add_argument("--dry-run", action="store_true", default=None)
    s.add_argument("--operation-id", help="the action's key (default: HUB_OPERATION_ID)")
    s.set_defaults(fn="finance act")
    s = fin.add_parser("action", help="an action's status and result")
    s.add_argument("action_id")
    s.set_defaults(fn="finance action")
    s = fin.add_parser("inbox", help="your Finance work queue")
    s.add_argument("--kind", choices=["uncategorized", "bill_to_enter", "unmatched_receipt", "missing_receipt", "bill_due",
                                      "invoice_overdue", "budget_near_limit", "sync_failed", "period_ready"],
                   help="only this kind, paged on with --cursor past its first 200")
    s.add_argument("--cursor"); s.add_argument("--limit", type=int)
    s.set_defaults(fn="finance inbox")
    fin.add_parser("card-fill", help="fill your card into a checkout (not available yet)").set_defaults(fn="finance card-fill")
    manager = sub.add_parser("task-manager", help="the Task Manager's hourly department check (docs/task-manager.md)").add_subparsers(dest="sub")
    manager.add_parser("status", help="bot, report switch and schedule, last run, each department's last check").set_defaults(fn="task-manager status")
    manager.add_parser("on", help="switch the hourly report on").set_defaults(fn="task-manager on")
    manager.add_parser("off", help="switch the hourly report off").set_defaults(fn="task-manager off")
    manager.add_parser("run", help="run the department check now").set_defaults(fn="task-manager run")
    s = manager.add_parser("schedule", help="pick when the report runs")
    s.add_argument("preset", choices=["business", "hourly", "4h", "daily"])
    s.add_argument("--at", help="HH:MM for the daily preset")
    s.set_defaults(fn="task-manager schedule")
    s = manager.add_parser("bot", help="turn the Task Manager bot itself on or off")
    s.add_argument("state", choices=["on", "off"])
    s.set_defaults(fn="task-manager bot")
    s = sub.add_parser("stall-triage", help="stalled task check: Decide reads a stalled task before its stall check stays silent (docs/tasks.md)")
    on = s.add_mutually_exclusive_group()
    on.add_argument("--on", dest="on", action="store_true", default=None, help="switch it on")
    on.add_argument("--off", dest="on", action="store_false", help="switch it off; leave both out to read the switch")
    on.add_argument("--no-on", dest="on", action="store_false", help=argparse.SUPPRESS)      # the old spelling
    s.set_defaults(fn="stall-triage")
    roadmap = sub.add_parser("roadmap", help="public roadmaps: ranking and company settings (docs/roadmaps.md)").add_subparsers(dest="sub")
    s = roadmap.add_parser("tasks", help="roadmap tasks ranked by requests, organization or size")
    s.add_argument("--sort", choices=["requester_count", "external_requesters", "important", "organization", "size"])
    s.add_argument("--direction", choices=["asc", "desc"])
    s.add_argument("--limit", type=int)
    s.add_argument("--offset", type=int)
    s.set_defaults(fn="roadmap tasks")
    s = roadmap.add_parser("settings", help="read the company's roadmap settings; any flag changes those named (owners and admins)")
    s.add_argument("--label", dest="labels", action="append", help="a label shown publicly; repeat for more, '' for none")
    s.add_argument("--cors-origin", dest="cors_origins", action="append", help="an exact website origin; repeat for more, '' for none")
    s.add_argument("--action-limit", dest="action_limit", type=int, help="visitor writes per address per day")
    s.add_argument("--suggestion-limit", dest="suggestion_limit", type=int, help="visitor suggestions waiting at once")
    s.add_argument("--bot-public-approval", dest="bot_public_approval", action=argparse.BooleanOptionalAction, default=None,
                   help="bots need an approval to publish")
    who = s.add_mutually_exclusive_group()
    who.add_argument("--suggestions-bot", dest="suggestions_bot", metavar="SLUG", help="the bot that receives visitor suggestions")
    who.add_argument("--automatic", action="store_true", help="suggestions go to the Automatic bot (Product Manager, then Head of Product)")
    s.set_defaults(fn="roadmap settings")
    keys = sub.add_parser("service-key", help="keys another system uses to file tasks, or to update this install (owner and admins)").add_subparsers(dest="sub")
    s = keys.add_parser("create", help="make a key; it is shown this once")
    s.add_argument("--label", required=True, help="the system that holds it; every task it files says so")
    s.add_argument("--scope", choices=["tasks", "update"], default="tasks",
                   help="tasks (default): files tasks; update (owner only): checks for, starts and follows an update of this install")
    s.set_defaults(fn="service-key create")
    keys.add_parser("list", help="every service key, never its secret").set_defaults(fn="service-key list")
    s = keys.add_parser("revoke", help="stop a key at once")
    s.add_argument("id")
    s.set_defaults(fn="service-key revoke")
    return p


RUNNER_CONFIG = Path.home() / ".config" / "tico" / "runner.json"
# Read-only commands a person or a script on the Mac may run outside a turn with the runner credential.
# The commands that work in this computer's workspace of bot repositories.
WORKSPACE_COMMANDS = ("bot check", "bot copy", "bot update-from-original", "bot suggest-to-original", "skill copy")
READ_OUTSIDE_A_TURN = ("sql", "tool list", "tool show", "tool query-search")


# ----------------------------------------------------------------------------- the last release's spellings
def _goal_auto(rest):                   # hub goal auto <id>  ->  hub goal status <id> auto
    return rest[:1] + ["auto"] + rest[1:]


def _context_search(rest):              # --source all|docs|market  ->  --market (docs are always searched)
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


# (old words, new words, words added at the end, rewrite of what follows). The old spellings are not in the parser, so
# they are not in `hub --help`; `rename_argv` maps a command line onto the new one and says so on stderr. They are removed
# one release after the rename. The longest old spelling that matches wins.
RENAMED = [
    (("say",), ("message", "send"), (), None),
    (("notice",), ("message", "send"), ("--fyi",), None),
    (("inbox",), ("message", "list"), (), None),
    (("ack",), ("message", "mark-read"), (), None),
    (("history",), ("conversation", "show"), (), None),
    (("ask",), ("question", "ask"), (), None),
    (("answer",), ("question", "answer"), (), None),
    (("notes",), ("note", "list"), (), None),
    (("unnote",), ("note", "delete"), (), None),
    (("board",), ("task", "list"), ("--all",), None),
    (("task", "stuck"), ("task", "list"), ("--stuck",), None),
    (("goals",), ("goal", "list"), (), None),
    (("goal", "auto"), ("goal", "status"), (), _goal_auto),
    (("goal", "checkins"), ("goal", "checkin-list"), (), None),
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
]
RENAMED.sort(key=lambda rule: -len(rule[0]))
NOTICE_COMMANDS = ("list",)                             # the subcommands `hub notice` has now


def parse(p, argv):
    """`p.parse_args(argv)`, except that a `--json` no command on the line takes is ignored, so a bot that adds it out of
    habit gets the command's normal answer instead of a usage error. One parse: a value read from standard input is read
    once, a `--json` after `--` stays a value, and a command with its own `--json` (`hub tool list`) still gets it."""
    args, extra = p.parse_known_args(argv)
    left = [a for a in extra if a != "--json"]
    if left:
        p.error("unrecognized arguments: " + " ".join(left))
    return args


def rename_argv(argv):
    """`argv` for the new spelling of an old command line, and the notice to print (or None when nothing was renamed)."""
    argv = list(argv)
    if argv[:1] == ["note"] and len(argv) > 1 and argv[1] not in ("create", "list", "delete") and not argv[1].startswith("-"):
        return ["note", "create"] + argv[1:], '"hub note <bot> <text>" is now "hub note create <bot> <text>"'
    if argv[:1] == ["notice"] and len(argv) > 1 and (argv[1] in NOTICE_COMMANDS or argv[1].startswith("-")):
        return argv, None                              # `hub notice list` is new; only `hub notice <text>` is the old send
    if argv[:2] in (["org", "order"], ["org", "reports-to"]):   # `hub org` alone is the old `hub team show`
        return argv, None
    for old, new, tail, rewrite in RENAMED:
        if tuple(argv[:len(old)]) == old:
            rest = argv[len(old):]
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
            return argv[:i] + argv[i + 2:], who
        if a.startswith("--human="):
            return argv[:i] + argv[i + 1:], a.split("=", 1)[1]
    return argv, who


def main(argv):
    argv, who = split_human(argv)
    argv, renamed = rename_argv(argv)
    if renamed:
        print(f"hub: {renamed}; the old name stops working in the next release", file=sys.stderr)
    p = parser()
    try:
        args = parse(p, argv)
    except SystemExit as e:                 # argparse already said what was wrong, or printed help
        return 0 if not e.code else 1
    if not getattr(args, "fn", None):
        p.print_help(sys.stderr)
        return 1
    try:
        if args.fn == "task create" and args.dry_run:     # never reaches remotecli: nothing to send
            return cmd_task_dry_run(args, who)
        if args.fn == "decision ask" and args.list:    # the sets are files in this checkout, no API needed
            from clients import judge
            print(json.dumps(judge.list_sets(), indent=2))
            return 0
        if args.fn == "doc fetch":                 # runs here, beside the bot: no hub, no credential
            from clients import doc_fetch
            try:
                print(json.dumps(doc_fetch.fetch(args.url, args.max_chars), indent=2))
                return 0
            except doc_fetch.FetchError as e:
                print(json.dumps({"error": e.code, "detail": e.message}, indent=2))
                return 1
        if not os.environ.get("HUB_API_URL"):
            local = args.fn in READ_OUTSIDE_A_TURN and not (args.fn == "tool list" and args.bot)
            credential = runner_credential(os.environ.get("HUB_RUNNER_CONFIG")) if local else None
            if not credential:
                raise CliError("HUB_API_URL is not set: `hub` talks to the Tico API and runs inside a "
                               "bot turn, where the runner sets HUB_API_URL, HUB_TOKEN and HUB_BOT"
                               + (f"; outside a turn `hub {args.fn}` needs this Mac's runner credential "
                                  "(scripts/setup-runner.sh)" if local else ""))
            os.environ["HUB_API_URL"], os.environ["HUB_TOKEN"] = credential
        if (args.fn in WORKSPACE_COMMANDS or args.fn == "bot create" and not args.record_only) and not os.environ.get("HUB_WORKSPACE"):
            raise CliError("HUB_WORKSPACE is not set: bot repositories live in the workspace this "
                           "machine was enrolled with, which the runner gives every turn")
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
