#!/usr/bin/env python3
"""`hub`: what a bot calls inside a turn to talk to Tico.

Thin on purpose (docs/history/hub-v2.md §5): parse the arguments, hand them to `clients/remotecli.py`,
which makes one HTTP call against the Tico API and prints JSON on stdout. Every rule lives on
the server (`backend/hubdb.py`), never here. A command is its tool's name (clients/hubtools.py) with
`hub_` dropped: `hub message send` is `hub_message_send`, `hub bot go-live` is `hub_bot_go_live`.

    hub whoami
    hub message send <bot|human> "<text>" [--fyi] [--conversation ID] [--ref task:ID ...]
                                           --fyi: expects no reply, takes no conversation or references
    hub message list                       what waits for you
    hub message mark-read <message-id>     an external agent's own delivery
    hub message redact <message-id> [--label L]   take a secret (read from stdin) out of a message
    hub conversation show <conversation-id> [--before MSG] [--since TIME]
                                           what was said in a conversation: the newest 200, oldest
                                           first; has_more says there is an older page, --before
                                           its next_before reads it. The run's prompt names it
    hub question ask <bot> [<bot>...] "<question>" --wait 60
    hub question answer <message-id> "<text>" [--unknown "needs X"]
    hub note create <bot> "<text>" [--text-file f]  a quiet note: wakes nobody; the bot's next run reads it
    hub note list [--to me|X] [--from me|X] [--since 24h|ISO] [--waiting]   notes, newest first
    hub note delete <id>                   take back a note no run has carried yet
    hub task create --owner <bot|human> --title "..." [--body "..."|--body-file f] [--due D] [--parent ID]
                    [--goal ID] [--dry-run] the checks a create would fail, nothing written
                    [--next-run]           for a bot: no wake; its next run carries the task
    hub task ask <id> "<question>"
    hub task comment <id> "<text>"         on the record with your name
    hub task comment-edit <id> <comment-id> "<text>"   change a comment you wrote; wakes nobody
    hub task comment-delete <id> <comment-id>          take back a comment you wrote
    hub task delete <id>                               delete a task made by mistake, to the trash (a person only)
    hub task deleted                                   deleted tasks you may restore
    hub task restore <id>                              put a deleted task back
    hub task update <id> --status doing|waiting|done|declined [--note "..."] [--goal ID|--goal ""]
                    [--title "..."]        rename it: checked as a new task's title would be
                    [--on PERSON]          with --status waiting: the person it waits on (their Needs you)
    hub task close <id> [--note "..."]
    hub task attach <id> <file> [--name "..."]
                                           store a deliverable with the task; prints the link
    hub task list [--owner me|X] [--requester me] [--status open|doing|waiting|done]
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
    hub doc list [--prefix sales/]         the company's internal docs by path
    hub doc read <id|path|manual:name>     one doc in full, with its version; manual:<name> is a Tico manual page
    hub doc search "<words>" [--manual] [--market]
                                           internal and linked docs, best first, then the Tico manual (--manual:
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
    hub assistant propose --summary "..." --path /api/v2/... [--method POST] [--body '{...}']
                                           Assistant only: ask the person to confirm a side effect (an
                                           approval, anything outside the company, spend, settings,
                                           archive/delete, activating a bot); it runs only on their click
    hub goal list [--owner me|X] [--all]   what you are for: your goals in order, the chain above
                                           them, and your reports' goals; --all is every goal
    hub goal show <id>                     the goal, its KPIs (target, colour), tasks, check-ins, history
    hub goal create --owner me|X --title "..." [--parent ID] [--body "..."|--body-file f] [--top]
                                           set a goal; a parent is optional. With one, whoever owns the parent gives its first colour
    hub goal status <id> red|yellow|green|done|dropped "<one sentence>"
                                           set the colour by hand: it sticks (with your name) until handed back
    hub goal status <id> auto              let the Goal Manager set the colour again (ends a colour set by hand)
    hub goal refresh [--goal ID ...]       the Goal Manager's status pass: work automatic colours out again
    hub goal checkin <id> "<words>" [--signal on_track|at_risk|off_track] [--from X] [--kpi ID]
                                           how the owner says it is going, in their words (colours a goal with no KPI)
    hub goal checkin-list <id>             a goal's check-ins, newest first
    hub goal needs-you                     red KPIs on your goals, stale KPIs you own, proposals to confirm
    hub goal update <id> [--title ...] [--body ...|--body-file f] [--parent ID|--parent ""] [--owner X]
                    [--rank N|--top]
    hub kpi list [--goal ID] [--owner me|X] [--unlinked] [--bot SLUG] [--archived]
                                           KPIs with latest reading and colour; --bot: that bot's five automatic KPIs
    hub kpi show <id> [--effective]        definition and versions, the goals using it, every reading, check-ins
    hub kpi archive <id> | restore <id>    hide from active lists, or restore; history stays
    hub kpi create "<name>" [--goal ID] [--definition "..."] [--unit %] [--direction up|down|range]
                [--cadence daily|weekly|monthly] [--owner me|X|company] [--source-note "..."] [target flags]
                                           a KPI of its own; with --goal it is linked, the target on the link
    hub kpi update <id> [--name ...] [--definition ...] [--unit ...] [--direction ...] [--cadence ...]
                    [--source-note ...] [--owner X]
                                           a change to what it measures is a new definition version
    hub kpi link <goal-id> <kpi-id> [target flags]   link a goal to a KPI, or change the target on the link
    hub kpi unlink <goal-id> <kpi-id>      take a KPI off a goal
        target flags: --baseline N --target N --deadline YYYY-MM-DD (improve; a deadline is required)
                      or --min N --max N (a range to stay in); none: just linked
    hub kpi log <kpi-id> <value> ["<note>"] [--period-start D] [--period-end D|--at D] [--collected-at T]
                [--evidence "url or note"] [--quality measured|estimate|partial|--estimate] [--source posthog]
                [--definition-version N] [--supersedes READING-ID]
                                           a reading: a fact with its period; to correct one, supersede it
    hub proposal create --kind goal_wording|goal_kpi|kpi_definition|kpi_target|flag [--goal ID] [--kpi ID]
                        (--payload '{json}'|--payload-file f.json) [--reason "..."]
                                           a change you may not make yourself, for the owner to confirm
    hub proposal list [--status pending|confirmed|rejected|all] [--goal ID] [--kpi ID]
    hub proposal decide <id> confirm|reject [--note "..."]   a person's own decision; a bot is refused
    hub approval request --kind send|spend|publish|merge --payload-file f.json [--task ID]
                                           if the owner already said send in Tico, skip this and
                                           `mail send --approve <their-message-id>`
    hub approval show <id>
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
    hub routine list [--bot X]             the routines a bot runs on a schedule (yours by default)
    hub routine set <key> --title "..." (--cron "0 7 * * 1-5" | --on meeting.ready)
                    [--text "..."|--text-file f] [--timezone Z] [--bot X] [--disabled]
                                           create or update one by its key; Tico's row is the routine
    hub routine update <id|key> [--title ...] [--text ...|--text-file f] [--cron ...|--on ...]
                    [--timezone Z] [--enable|--disable] [--bot X]
                                           --enable / --disable turn it on or off; another bot's, as the requester (BotOps)
    hub routine delete <id>
    hub bot status set "<focus>" [--state waiting_human|blocked|...] [--task ID] [--bot X]
    hub bot status list [--team marketing]
    hub bot status history <bot> [--since 7d]
    hub bot recent [--days N] [--limit N]  the bots you have been working with lately and where each stands
    hub run list <bot> [--since 24h]       a bot's recent runs
    hub team show [--person ID] [--team NAME]
                                           humans and bots: who they are, Slack, what they own; each bot
                                           with its reports_to, group and template (the bots you may see)
    hub health check                       what is wrong with the bots, most urgent first, each with its fix
                                           (the Assistant gets the live snapshot)
    hub update list [--kind daily|weekly] [--bot X] [--unread] [--limit N]
                                           the bots' updates, newest first
    hub update show <id>                   one update with its thread
    hub update create "<- bullets>" [--kind daily|weekly]   post your own update when Tico asks
    hub update create --slides-file week.json [--day D]   a week in review: {goal, kpis, done, focus, blockers}
    hub update redo --day D [--kind weekly|daily] [--bot B ...]   ask the bots to redo a past day (owner)
    hub update mark-read [ids...] [--all] [--unread]
    hub update reply <id> "<text>"
    hub update settings <bot> [--daily on|off] [--weekly on|off]
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
    hub template list                      the bot templates this company can pick from
    hub bot create <slug> --template T [--name "Display"]
                                           set a chosen bot up in the workspace (BotOps only); it also registers
                                           the bot with Tico as the requester when a person's message started the run
    hub bot create <slug> --record-only [--name N] [--description D] [--reports-to R] [--template T]
                                           only register a new bot with Tico (planned), as the requester (BotOps)
    hub bot check <slug>                   what preflight would still refuse about that repository
    hub bot update <slug> [--reports-to R] [--display-name N] [--description D] [--status S]
                                           apply a person's bot-settings request as them (BotOps)
    hub bot access <slug> [--see V] [--read V] [--write V]
                                           show or set who sees, reads, writes (V: everyone, or ben,group:legal,bot:x)
    hub bot owners <slug> [--add P ...] [--remove P ...]
                                           add or remove the humans who own a bot, as the requester (BotOps)
    hub bot setup-done [slug]              a starter bot marks its setup done once its setup is done
    hub bot place <bot> [--computer <label|id>]
                                           put a bot on a computer: the one named, or the best one that takes it
    hub bot go-live <bot> [--computer C] [--no-setup]
                                           place it if needed, turn it on, start its setup
    hub bot model <bot> [<model>] [--effort E]
                                           list the models, or change the bot's
    hub bot pause|resume <bot>             stop or restart a bot (resume places one that has no computer)
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
    hub group list                         the groups (they nest), with their humans and bots
    hub group update [<group>] [--name N] [--parent G|''] [--add-human H ...] [--add-bot B ...]
                     [--remove-human H ...] [--remove-bot B ...]
                                           an owner or admin, or BotOps as the person who asked: with no <group>,
                                           create one from --name; else rename, move or fill it. A teammate is in
                                           one group at a time
    hub api GET|POST|PUT|PATCH|DELETE <path> ['{json}']
                                           BotOps: any v2 route, as the person who asked; a Confirm card for what
                                           always needs their click. Never a secret in the body
    hub computer list                      the computers a bot may go on, and what runs on each
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
    hub service-key create --label "Billing backend"
                                           the owner or an admin: a key another system uses to file, update and close
                                           tasks (POST /api/v2/inbound/tasks, docs/service-keys.md); shown once
    hub service-key create --label "Release Manager" --scope update
                                           the owner: a key a release bot on another install uses to check for,
                                           start and follow an update of this one (/api/v2/system/update), and nothing else
    hub service-key list | revoke <id>     every service key, never its secret; stop one at once
    hub grokbot sync --file f.json         sync your Grok Bots into Tico

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
import sys
from pathlib import Path

if __package__ in (None, ""):                          # run as a script: python clients/hubcli.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Mirrors of the server's vocabulary (backend/hubdb.py), so a typo is refused by the parser
# before a request is made. The server is the authority when they disagree.
TASK_STATUSES = ("open", "doing", "waiting", "review", "ready", "done", "closed", "declined")
APPROVAL_KINDS = ("send", "spend", "publish", "merge")
GOAL_STATUSES = ("red", "yellow", "green", "done", "dropped")     # set by hand; gray is only ever automatic


class CliError(Exception):
    """Anything the arguments or the environment got wrong: exit 1."""


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
    """(resolved owner, the problems the server's `task_create` would refuse on), writing none.

    The owner's kind comes from the registry files; the lint (`lint_human_item`) and the reach
    rule (`classify`) are hubdb's own functions run here. Reach against live state, the
    duplicate check and whether a named type exists are the hub's to decide at the real create.
    A type other than General is a custom type, whose tasks neither lint shapes.
    """
    from backend import hubdb as H
    problems = []
    title, body = str(title or "").strip(), str(body or "")
    general = str(type or "").strip().lower() in ("", H.GENERAL_TYPE)
    target = owner_from_registry(owner)
    if not target:
        problems.append(f"{owner} is not in registry/employees.yaml or registry/people.yaml")
    if H.is_bot(actor) and H.classify(f"{title}\n{body}", to_actor=target) == "escape":
        problems.append("the task reaches outside the hub (rule 8): a real create is refused "
                        "and repeating it quarantines you")
    if H.is_human(target) and general:
        problems += H.lint_human_item(body, title=title)
    elif not title:
        problems.append("give it a title that says what you are asking for")
    if H.is_bot(actor) and general:
        # plain-English titles: a warning this week, a refusal once TICO_TITLE_LINT=refuse
        problems += [f"{p} (title lint, {H.TITLE_LINT})" for p in H.lint_title(title)]
    return target, problems


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
    owner, problems = task_problems(actor, args.owner, args.title, body, getattr(args, "type", None))
    if problems:
        print("\n".join(f"- {p}" for p in problems))
        return 1
    print(f'ok: would create "{args.title.strip()}" for {owner} '
          f'({words_outside_quotes(body)} words outside quoted drafts)')
    return 0


# ----------------------------------------------------------------------------- parser
def parser():
    p = argparse.ArgumentParser(prog="hub", description="the company hub: messages, tasks, status")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("whoami").set_defaults(fn="whoami")

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

    question = sub.add_parser("question", help="ask other bots a question and wait; answer one asked of you").add_subparsers(dest="sub")
    s = question.add_parser("ask")
    s.add_argument("words", nargs="*")
    s.add_argument("--wait", type=float, default=60)
    s.set_defaults(fn="question ask")
    s = question.add_parser("answer")
    s.add_argument("message_id")
    s.add_argument("text", nargs="?", default="")
    s.add_argument("--unknown")
    s.set_defaults(fn="question answer")

    note = sub.add_parser("note", help="a quiet note for a bot: no run now, its next run reads it").add_subparsers(dest="sub")
    s = note.add_parser("create", help="leave a bot a quiet note: no run now, its next run reads it")
    s.add_argument("to")
    s.add_argument("text", nargs="?", default="")
    s.add_argument("--text-file", dest="text_file")
    s.set_defaults(fn="note create")
    s = note.add_parser("list", help="quiet notes, newest first: left for you and by you")
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
    docs = sub.add_parser("doc", help="the team's docs: read, search and write internal docs, list linked ones, ask the Librarian").add_subparsers(dest="sub")
    s = docs.add_parser("archive", help="archive an internal Doc with your rights")
    s.add_argument("ref")
    s.set_defaults(fn="doc archive")
    s = docs.add_parser("list", help="internal docs by path")
    s.add_argument("--prefix", help="only paths starting with this, e.g. sales/")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="doc list")
    s = docs.add_parser("read", help="one internal doc in full")
    s.add_argument("ref", help="a doc id or a path; manual:<name> for a page of the Tico manual")
    s.set_defaults(fn="doc read")
    s = docs.add_parser("search", help="search internal and linked docs, then the Tico manual")
    s.add_argument("q")
    s.add_argument("--limit", type=int)
    s.add_argument("--manual", dest="collection", action="store_const", const="manual",
                   help="only the read-only Tico manual (how to do something in Tico)")
    s.add_argument("--team", dest="collection", action="store_const", const="team",
                   help="only team docs")
    s.add_argument("--market", action="store_true", default=None,
                   help="also the market's notes, entities and evidence")
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
    s.add_argument("parent_id")
    s.add_argument("--owner", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--body", default="")
    s.set_defaults(fn="task child create")
    s = task.add_parser("tree", help="show nested subtasks")
    s.add_argument("id")
    s.set_defaults(fn="task tree")
    s = task.add_parser("parent", help="move a subtree; empty parent clears it")
    s.add_argument("id")
    s.add_argument("parent_id")
    s.set_defaults(fn="task reparent")
    worktree = task.add_parser("worktree", help="create or attach this task's worktree").add_subparsers(dest="worktree_sub")
    for operation, argument in (("add", "repo"), ("attach", "path"), ("setup", "repo")):
        s = worktree.add_parser(operation)
        s.add_argument(argument)
        s.add_argument("--task", help="task id; defaults to this run's task")
        s.set_defaults(fn="task worktree " + operation)
    s = task.add_parser("create")
    s.add_argument("--private", action="store_true", default=None, help="only requester and assignee may read")
    s.add_argument("--owner", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--body", default="")
    s.add_argument("--body-file", dest="body_file")
    s.add_argument("--due")
    s.add_argument("--parent")
    s.add_argument("--label", action="append", help="a label (repeat, or comma-separate); a project is a label")
    s.add_argument("--top", action="store_true", help="put it at the top of the owner's queue")
    s.add_argument("--link", action="append", help="a URL to attach (a pull request, an issue, a document)")
    s.add_argument("--goal", help="the goal this task serves (hub goal list); optional")
    s.add_argument("--next-run", "--quiet", dest="next_run", action="store_true",
                   help="for a bot: do not wake it; its next run, whatever starts it, carries this task")
    s.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="print the checks a create would fail and write nothing")
    s.add_argument("--request-id", dest="request_id", help="BotOps continuation: the originating human chat message")
    s.add_argument("--type", help="task type id or name; defaults to General")
    s.add_argument("--step", help="step id or name in the type")
    s.add_argument("--number", type=int, help="movers: keep an imported ticket's number")
    s.set_defaults(fn="task create")
    types = task.add_parser("type", help="manage task types (movers only)").add_subparsers(dest="type_sub")
    bots_help = ("what every bot may do with the type's tasks: parties (only the bots on each task), "
                 "read (read and comment on all of them) or work (also change them)")
    s = types.add_parser("create")
    s.add_argument("--name", required=True)
    s.add_argument("--steps-file", dest="steps_file")
    s.add_argument("--bots", choices=["parties", "read", "work"], help=bots_help)
    s.add_argument("--numbered", action="store_true", default=None, help="number each task on the type")
    s.set_defaults(fn="task type create")
    s = types.add_parser("update")
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--steps-file", dest="steps_file")
    s.add_argument("--bots", choices=["parties", "read", "work"], help=bots_help)
    s.add_argument("--numbered", action=argparse.BooleanOptionalAction, default=None,
                   help="number each task created on or moved onto the type")
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
    s = task.add_parser("update")
    visibility = s.add_mutually_exclusive_group()
    visibility.add_argument("--private", dest="private", action="store_true", default=None)
    visibility.add_argument("--company", dest="private", action="store_false", help="human requester publishes the task")
    s.add_argument("id")
    s.add_argument("--title", help="a new title, checked as a new task's title would be")
    s.add_argument("--status", choices=list(TASK_STATUSES))
    s.add_argument("--note")
    s.add_argument("--owner")
    s.add_argument("--due")
    s.add_argument("--blocked-by", dest="blocked_by", help="the task this one waits on; '' clears it")
    s.add_argument("--on", dest="waiting_on",
                   help="with --status waiting: the person it waits on, so it is in their Needs you; '' clears it")
    s.add_argument("--goal", help='the goal this task serves; "" takes it off')
    s.add_argument("--quiet", action="store_true", help="keep detailed notes on the task")
    s.add_argument("--type", help="task type id or name")
    s.add_argument("--step", help="step id or name; sets status; an empty string clears it")
    s.add_argument("--step-rank", dest="step_rank", type=float, help="its place within its step, lower first")
    s.add_argument("--number", type=int, help="movers: the number of a task that has none")
    s.set_defaults(fn="task update")
    s = task.add_parser("comment", help="leave a comment on a task, on the record with your name")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--attach", action="append", help="file to attach; repeat for more files")
    choices = s.add_mutually_exclusive_group()
    choices.add_argument("--ask", help="JSON file containing structured questions")
    choices.add_argument("--choices", help="comma-separated option labels")
    s.set_defaults(fn="task comment")
    s = task.add_parser("comment-edit", help="change the text of a comment you wrote; it wakes nobody")
    s.add_argument("id")
    s.add_argument("comment_id", help="the comment's id (its id in the task's comments)")
    s.add_argument("text")
    s.set_defaults(fn="task comment-edit")
    s = task.add_parser("comment-delete", help="take back a comment you wrote")
    s.add_argument("id")
    s.add_argument("comment_id", help="the comment's id (its id in the task's comments)")
    s.set_defaults(fn="task comment-delete")
    s = task.add_parser("delete", help="delete a task made by mistake: its requester or a mover, never a bot")
    s.add_argument("id")
    s.set_defaults(fn="task delete")
    s = task.add_parser("deleted", help="deleted tasks you may restore, newest first")
    s.set_defaults(fn="task deleted")
    s = task.add_parser("restore", help="put a deleted task back: whoever deleted it, its requester or a mover")
    s.add_argument("id")
    s.set_defaults(fn="task restore")
    s = task.add_parser("link", help="attach a link: the pull request you opened, an issue, a document")
    s.add_argument("id")
    s.add_argument("url")
    s.add_argument("--title")
    s.set_defaults(fn="task link")
    s = task.add_parser("label", help="add or remove labels on a task")
    s.add_argument("id")
    s.add_argument("--add", action="append")
    s.add_argument("--remove", action="append")
    s.set_defaults(fn="task label")
    s = task.add_parser("close")
    s.add_argument("id")
    s.add_argument("--note")
    s.set_defaults(fn="task close")
    s = task.add_parser("attach", help="attach a file to a task; prints the link for your note")
    s.add_argument("id")
    s.add_argument("file")
    s.add_argument("--name", help="the name people see; defaults to the file's own")
    s.add_argument("--poster", help="a PNG or JPEG preview")
    s.add_argument("--note", help="short note on this version (at most 500 characters)")
    choices = s.add_mutually_exclusive_group()
    choices.add_argument("--ask", help="JSON file containing structured questions")
    choices.add_argument("--choices", help="comma-separated option labels")
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
    s.add_argument("--brief", action="store_true", help="leave out bodies and acceptance criteria")
    s.add_argument("--all", action="store_true", help="the board: every task and every bot you may see, as {tasks, bots}")
    s.add_argument("--stuck", action="store_true",
                   help="only open work untouched for --hours that waits on nobody (BotOps's sweep)")
    s.add_argument("--hours", type=int, default=24, help="with --stuck: untouched for at least this many hours")
    s.set_defaults(fn="task list")
    s = task.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn="task show")
    s = task.add_parser("run", help="start a bot's task now, as the task")
    s.add_argument("id")
    s.set_defaults(fn="task run")

    goal = sub.add_parser("goal", help="goals: list yours, show one, set one, set its colour, edit it").add_subparsers(dest="sub")
    s = goal.add_parser("list", help="what you are for: your goals, the chain above them, your reports' goals")
    s.add_argument("--owner", help="someone else's: a bot slug or a person id")
    s.add_argument("--all", action="store_true", help="every live goal in the company")
    s.add_argument("--status", help="with --all: only these, comma-separated (red,yellow,green,gray,done,dropped)")
    s.set_defaults(fn="goal list")
    s = goal.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn="goal show")
    s = goal.add_parser("create", help="set a goal; a parent is optional, and whoever owns it gives the first colour")
    s.add_argument("--owner", required=True, help="me, a bot slug, or a person id (company: a company goal, the owner only)")
    s.add_argument("--title", required=True)
    s.add_argument("--parent", help="the goal this one supports, if any; not needed")
    s.add_argument("--body", default="")
    s.add_argument("--body-file", dest="body_file")
    s.add_argument("--top", action="store_true", help="put it first in the owner's order")
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
    s.set_defaults(fn="goal update")
    s = goal.add_parser("refresh", help="the Goal Manager's status pass: work automatic colours out again")
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
    s.set_defaults(fn="kpi create")
    s = kpi.add_parser("update", help="a change to what it measures is a new definition version")
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--owner")
    kpi_flags(s)
    s.set_defaults(fn="kpi update")
    s = kpi.add_parser("link", help="link a goal to a KPI, or change the target on the link")
    s.add_argument("goal_id")
    s.add_argument("kpi_id")
    target_flags(s)
    s.set_defaults(fn="kpi link")
    s = kpi.add_parser("unlink", help="take a KPI off a goal")
    s.add_argument("goal_id")
    s.add_argument("kpi_id")
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
    s.add_argument("--kind", required=True, choices=["goal_wording", "goal_kpi", "kpi_definition", "kpi_target", "flag"])
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
    s.set_defaults(fn="proposal decide")

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
    s = market.add_parser("sweep", help="one owner task for needs-human, listening tasks for unverified stale entities")
    s.add_argument("--today")
    s.add_argument("--unverified", action="append", default=[], help="entity-id=what to look for")
    s.set_defaults(fn="market sweep")
    s = market.add_parser("refresh", help="rewrite the weekly delta from market events; only a Monday changes it")
    s.add_argument("--today")
    s.set_defaults(fn="market refresh")
    s = market.add_parser("page", help="rewrite one market page (overview, coverage-universe, ...) from a Markdown file")
    s.add_argument("name", help="overview, structure-and-size, coverage-universe, people-who-matter, channels, "
                                "regulation-and-catalysts, theses or open-questions")
    s.add_argument("--body-file", dest="body_file", required=True, help="the whole page in Markdown; - reads stdin")
    s.set_defaults(fn="market page")

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

    routine = sub.add_parser("routine", help="what this bot is told on a schedule (docs/routines.md)").add_subparsers(dest="sub")
    s = routine.add_parser("list")
    s.add_argument("--bot", help="another bot's routines; yours by default")
    s.set_defaults(fn="routine list")
    s = routine.add_parser("set", help="create a routine, or update the one with this key")
    s.add_argument("key", help="a stable name, letters, digits, dots, dashes or underscores")
    s.add_argument("--title", required=True)
    s.add_argument("--cron", help="five fields, in --timezone (America/Los_Angeles by default)")
    s.add_argument("--on", help="a Tico event instead of a time: meeting.ready")
    s.add_argument("--text", default="", help="what the bot is told each time")
    s.add_argument("--text-file", dest="text_file")
    s.add_argument("--timezone")
    s.add_argument("--bot", help="set it on another bot you own; yourself by default")
    s.add_argument("--disabled", action="store_true", help="keep it, but do not run it yet")
    s.set_defaults(fn="routine set")
    s = routine.add_parser("update", help="change one routine by its id or key; --enable or --disable turns it on or off")
    s.add_argument("id", help="the routine id from `hub routine list`, or its key")
    s.add_argument("--title")
    s.add_argument("--text")
    s.add_argument("--text-file", dest="text_file")
    s.add_argument("--cron")
    s.add_argument("--on")
    s.add_argument("--timezone")
    s.add_argument("--bot", help="the bot it belongs to, when it is not yours")
    switch = s.add_mutually_exclusive_group()
    switch.add_argument("--enable", action="store_true")
    switch.add_argument("--disable", action="store_true")
    s.set_defaults(fn="routine update")
    s = routine.add_parser("delete")
    s.add_argument("id", help="the routine id from `hub routine list`")
    s.set_defaults(fn="routine delete")
    s = routine.add_parser("run", help="run a scheduled routine now")
    s.add_argument("id")
    s.set_defaults(fn="routine run")

    appr = sub.add_parser("approval").add_subparsers(dest="sub")
    s = appr.add_parser("request",
                        help="ask for a yes; if the owner already said send, mail send --approve <their message id>")
    s.add_argument("--kind", required=True, choices=list(APPROVAL_KINDS))
    s.add_argument("--payload-file", dest="payload_file")
    s.add_argument("--payload")
    s.add_argument("--task")
    s.set_defaults(fn="approval request")
    s = appr.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn="approval show")

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

    s = sub.add_parser("run", help="a bot's runs").add_subparsers(dest="sub").add_parser("list", help="a bot's recent runs")
    s.add_argument("bot")
    s.add_argument("--since")
    s.set_defaults(fn="run list")

    team = sub.add_parser("team", help="team chart and icon").add_subparsers(dest="sub")
    s = team.add_parser("icon", help="set the public team logo (owner; PNG/JPEG/WebP up to 1 MB)")
    s.add_argument("file")
    s.set_defaults(fn="team icon")
    s = team.add_parser(
        "show", help="the team chart: humans, Slack, what they own, the bots with reports_to and group")
    s.add_argument("--person", help="that human and everyone under them")
    s.add_argument("--team", help="a group id, like engineering or sales: that group and the groups in it")
    s.set_defaults(fn="team show")
    s = sub.add_parser("health", help="what is wrong, and where").add_subparsers(dest="sub").add_parser(
        "check", help="what is wrong with the bots, most urgent first, each with its fix")
    s.set_defaults(fn="health check")
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
    s.set_defaults(fn="update redo")
    s = upd.add_parser("list", help="the bots' updates, newest first")
    s.add_argument("--kind", choices=("daily", "weekly"))
    s.add_argument("--bot")
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
    grok = sub.add_parser("grokbot", help="your Grok Bots in Tico (docs/grok-bot-sync.md)").add_subparsers(dest="sub")
    s = grok.add_parser("sync", help="sync Grok Bots from a JSON file: {\"bots\": [...], \"source\": ...}")
    s.add_argument("--file", required=True, help="the JSON body hub_grokbot_sync takes")
    s.set_defaults(fn="grokbot sync")

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
    s = bot.add_parser("create", help="build <slug> from a template in the workspace and register it; --record-only only registers")
    s.add_argument("slug")
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
    s = bot.add_parser("setup-done", help="a starter bot marks its setup done, once its setup is done")
    s.add_argument("slug", nargs="?", help="the bot; the one running this command by default")
    s.set_defaults(fn="bot setup-done")
    s = bot.add_parser("place", help="put a bot on a computer, as the person who asked (BotOps)")
    s.add_argument("bot")
    s.add_argument("--computer", help="a computer's label or id; the best one that takes it by default")
    s.set_defaults(fn="bot place")
    s = bot.add_parser("go-live", help="place it if needed, turn it on and start its setup (BotOps)")
    s.add_argument("bot")
    s.add_argument("--computer")
    s.add_argument("--no-setup", dest="no_setup", action="store_true", help="do not start its setup chat")
    s.add_argument("--routines-file", dest="routines_file", help="requested live schedules as a JSON array")
    s.set_defaults(fn="bot go-live")
    s = bot.add_parser("restore", help="bring an archived bot back, as the person who asked (BotOps)")
    s.add_argument("bot")
    s.set_defaults(fn="bot restore")
    s = bot.add_parser("archive", help="archive a bot you manage")
    s.add_argument("bot")
    s.add_argument("--successor")
    s.set_defaults(fn="bot archive")
    s = bot.add_parser("model", help="list the models, or change this bot's (BotOps)")
    s.add_argument("bot")
    s.add_argument("model", nargs="?", help="a model id or name; leave out to list")
    s.add_argument("--effort")
    s.set_defaults(fn="bot model")
    for verb in ("pause", "resume"):
        s = bot.add_parser(verb, help=f"{verb} a bot (BotOps)")
        s.add_argument("bot")
        s.set_defaults(fn="bot " + verb)
    s = bot.add_parser("branch", help="make your branch of a bot that allows branches")
    s.add_argument("bot")
    s.add_argument("--computer", help="your computer's label or id")
    s.set_defaults(fn="bot branch")
    assignment = bot.add_parser("assignment", help="temporary task-scoped instances of a persistent role").add_subparsers(
        dest="assignment_action", required=True)
    s = assignment.add_parser("list", help="list assignment branches and lifecycle history")
    s.add_argument("source")
    s.set_defaults(fn="bot assignment")
    s = assignment.add_parser("policy", help="enable the source role's direct-parent bot as allocator")
    s.add_argument("source")
    policy = s.add_mutually_exclusive_group(required=True)
    policy.add_argument("--enable", dest="enabled", action="store_true")
    policy.add_argument("--disable", dest="enabled", action="store_false")
    s.add_argument("--revision", type=int, required=True)
    s.set_defaults(fn="bot assignment")
    s = assignment.add_parser("create", help="allocate one existing delivery task to an isolated actor")
    s.add_argument("source")
    s.add_argument("--task", required=True)
    s.add_argument("--name", required=True, dest="display_name")
    s.add_argument("--key", required=True, dest="assignment_key", help="stable identity key; do not reuse for another task")
    s.add_argument("--generation", type=int, default=1)
    s.set_defaults(fn="bot assignment")
    s = assignment.add_parser("update", help="record a checkpoint or lifecycle transition")
    s.add_argument("assignment")
    s.add_argument("--revision", type=int, required=True)
    s.add_argument("--phase", choices=("working", "waiting_review", "waiting_release", "paused", "interrupted", "verifying", "archived", "cancelled"))
    s.add_argument("--name", dest="display_name")
    s.add_argument("--note", default="")
    s.add_argument("--checkpoint-json", default="{}")
    s.add_argument("--deployed-version", default="")
    s.add_argument("--acceptance-receipt", default="")
    s.add_argument("--learning-receipt", default="")
    s.add_argument("--evidence-receipt", default="")
    s.add_argument("--handoff-task", default="", help="existing source-role task to record as the follow-up on cancellation")
    s.add_argument("--reviewed-learning-file", help="read a generalized lesson reviewed for publication to the source role's shared trunk")
    s.add_argument("--confirm-learning-review", action="store_true",
                   help="confirm the lesson excludes task/customer details, private conversation content and credentials")
    s.set_defaults(fn="bot assignment")
    s = assignment.add_parser("cleanup", help="request guarded local cleanup after archive or cancellation")
    s.add_argument("assignment")
    s.add_argument("--revision", type=int, required=True)
    s.set_defaults(fn="bot assignment")
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
    modes.add_argument("--chosen", nargs="*")
    s.set_defaults(fn="bot repos")
    repos = sub.add_parser("repo", help="team repositories").add_subparsers(dest="sub")
    s = repos.add_parser("list")
    s.set_defaults(fn="repo list")
    for verb in ("tick", "untick"):
        s = repos.add_parser(verb)
        s.add_argument("full_name")
        s.set_defaults(fn="repo " + verb)
    s = repos.add_parser("product-create", help="preview, then confirm an exact private empty product repository (Owner only)")
    s.add_argument("name", help="exact GitHub repository name, without an organization")
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
    groups = sub.add_parser("group", help="the groups: sub-teams that nest, holding humans and bots").add_subparsers(dest="sub")
    groups.add_parser("list", help="the groups with their humans and bots").set_defaults(fn="group list")

    s = groups.add_parser("update", help="create (no group given), rename, move or fill a group (an owner or an admin)")
    s.add_argument("group", nargs="?", help="the group's id; leave out to create one from --name")
    s.add_argument("--name")
    s.add_argument("--parent", help="the group it is in; an empty one is the top")
    s.add_argument("--add-human", dest="add_humans", action="append", metavar="HUMAN", help="put a human in it")
    s.add_argument("--add-bot", dest="add_bots", action="append", metavar="BOT", help="put a bot in it")
    s.add_argument("--remove-human", dest="remove_humans", action="append", metavar="HUMAN")
    s.add_argument("--remove-bot", dest="remove_bots", action="append", metavar="BOT")
    s.set_defaults(fn="group update")
    s = sub.add_parser("api", help="BotOps: any v2 route, as the person who asked")
    s.add_argument("method", type=str.upper, choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    s.add_argument("path", help="/api/v2/... or the part after it")
    s.add_argument("body", nargs="?", help="a JSON body for a write; - reads it from standard input")
    s.set_defaults(fn="api")
    sub.add_parser("computer", help="the computers a bot may go on").add_subparsers(dest="sub").add_parser(
        "list", help="the computers a bot may go on, and what runs on each").set_defaults(fn="computer list")
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
    (("routine", "on"), ("routine", "update"), ("--enable",), None),
    (("routine", "off"), ("routine", "update"), ("--disable",), None),
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


def rename_argv(argv):
    """`argv` for the new spelling of an old command line, and the notice to print (or None when nothing was renamed)."""
    argv = list(argv)
    if argv[:1] == ["note"] and len(argv) > 1 and argv[1] not in ("create", "list", "delete") and not argv[1].startswith("-"):
        return ["note", "create"] + argv[1:], '"hub note <bot> <text>" is now "hub note create <bot> <text>"'
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
        args = p.parse_args(argv)
    except SystemExit as e:                 # argparse already said what was wrong, or printed help
        return 0 if not e.code else 1
    if not getattr(args, "fn", None):
        p.print_help(sys.stderr)
        return 1
    try:
        if getattr(args, "dry_run", False):     # never reaches remotecli: nothing to send
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
