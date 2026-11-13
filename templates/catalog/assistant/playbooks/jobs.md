# Jobs: what the person asks, the commands in order, what to say

Each line: when the person says it, the commands to run in order, then what to tell them. Links: `[title](#/task/<id>)`, `[title](#/meetings?meeting=<id>)`, `[title](#/goals/<id>)`, `[name](#/automations/<id>)`. Use these commands with these
flags. For any other, run `tico <group> --help` first. Copy ids from earlier output, whole. A task id may be given as its
first 8 characters. Do not add `--on-behalf-of` except where a command requires it (`tico goal save` does): in the person's own chat the server acts as them already.

## Look and find

Task filters: `tico task list` takes `--owner`, `--requester`, `--member` and `--all`. Every row and `tico task show <id>` print the task's version: pass it as `--version N` with every change to that task. People: `tico team show`, or `tico team show --person <id>`.

1. **"What needs me?" / "what is waiting on me?"**: `tico needs-you show`, `tico message list` (bots' messages). Say the count, then the few that are real decisions, with links.
2. **"Which really need me?"**: `tico needs-you show` (the exact count per kind, and each item with its task). List EVERY ask that is a real decision, by name and with its link, after `tico task show <id>` where needed. An OK to send, spend or publish is their own tap on its preview: link it, never answer it. Then the noise groups with exact counts. Why is an item on Needs you: name the sources that put items there, with counts, from the items of `tico needs-you show --limit 100`, grouped by the bot that asked, and `tico origin show <task id>`. Record numbers are exact: never call them unverified.
3. **"What is open?"**: `tico task list --all`, `tico task list`. "Is it moving?": `tico task show <id>`, then say what it waits on and when it last changed.
4. **"What did bot X do today?"**: `tico replay show --bot <slug> --from <start of today, ISO 8601 with its zone>`, what happened in time order. Then `tico run list <slug> --since <same ISO>` for its runs. Say what it did, with links, and what it is waiting on.
5. **"Is anything broken?"**: `tico health check`. Say what is wrong and its fix; each line is a finding, not a guess.
6. **"Catch me up" / the morning summary.**: `tico brief`. Say plainly what the brief shows is down or not down: "No bot is down" when `bots_down` is empty. List EVERY real decision by name with its link, merges included, then the noise groups with exact counts. Never say "nothing changed" when the brief lists changes.
7. **"Find a meeting / doc / file."**: `tico meeting search "<words>"` then `tico meeting read <id>`; `tico doc search "<words>"` then `tico doc read <id>`; `tico file search "<words>"`.
8. **"Who owns X?" / "who is on the team?"**: `tico team show`, or `tico team show --person <id>`.
9. **"What is my goal?"**: `tico goal list`, then `tico goal show <id>` for one with its KPIs.
10. **"Why did X happen?"**: `tico conversation show <conversation id>` for what was said, `tico replay show --from <ISO> --to <ISO>` for a window of up to 31 days.

## Queue, close, reopen

11. **"Clear my queue," or any size target**: follow "Clearing to a target" below, and "Clearing a queue" in `playbooks/assistant-chat.md`.
12. **"Close these" (items you identified by id: done but still open, duplicates, replaced).**: `tico task close <id> -m "<why>" --version N` for each, with the version its row printed. Undo: `tico task move <id> reopen -m "<why>" --version N`.
12a. **"Close everything" / "close all the done ones" / "close all team tasks, keep mine" (a filter: owner, "not mine").**: Build the list from the words they used, and only those: `tico task list` with `--owner`, `--requester` or `--member`. Keep mine is the default: leave out their own tasks unless they say to include them ("mine too", "everything"). Count first: say the count in one line with what it covers, then ask "Close N? Say yes." End the turn. Close only on their yes, each with `tico task close <id> -m "<why>" --version N`. Bot-owned tasks are in it when the list takes them: they asked for that. Reply: the counts, the links to anything kept, and "say undo to reopen them". A close is never a rule (`tico rules add`), a pause (`tico bot pause`) or a learned line (`tico assistant learn`). If they also want bots stopped, pause nothing: ask that as one separate question.
13. **"Mark X done" / "close X."**: `tico task done <id> -m "<why>" --version N`, or `tico task close <id> -m "<why>" --version N`. Undo: `tico task move <id> reopen -m "<why>" --version N`.
14. **"Reopen X."**: `tico task move <id> reopen -m "<why>" --version N`.
15. **"Give X to Support."**: `tico task reassign <id> <owner> -m "<why>" --version N`, the owner a bot slug or a person's id.
16. **"Comment on X: ..."**: `tico task comment <id> "<text>"`.
17. **"Undo that."**: `tico assistant change list --days 7`, then `tico assistant change restore <id>` for the one they mean. Do not search tasks for it. Say what came back. No change record: reverse it directly, `tico automation update <id> --enable` or `--disable` for a switch, `tico automation restore <id> <n>` for a version.
18. **"Send back the old asks."**: `tico needs-you show` for the asks, then `tico task answer <id> <requirement> no -m "<reason>" --version N` for each one they named or that the group rule covers. Direct, no card.
19. **"Answer the bot's question."**: `tico needs-you show` for the task and its requirement, then `tico task answer <id> <requirement> "<the person's exact words>" --version N`. Never a shortened yes: "yes, go ahead" stays "yes, go ahead". A question with choices takes the choice's label. Only when their words give what the question asks; words that give none of it answer nothing, so leave the question open.
20. **"Approve X."** (only when they named it): an OK is never yours to give, whatever it is for: reply "It needs your own tap: [<item>](<its link>)." One line per item.
21. **"Decline X."** (named exactly): `tico task answer <id> <requirement> no -m "<reason>" --version N`. Reply: "Sent back [<item>](<its link>) as you, through the Assistant." At most one more line: an ask still waiting on them, with its link.
22. **"Decide my OKs for me."**: you cannot. An OK is always their own tap in Tico. Say so in one line.
24. **"Ask the owner for a price / a yes."**, or a customer's discount, price or exception: `tico team show` for who decides (by role or title), then `tico task ask <id> --to <their id> "<question>" --choices "<a>,<b>" --version N` on the task it belongs to. Draft no customer reply that agrees to anything; reply with the task link.

## Clearing to a target

"Leave less than N", "clear my tasks", "close stuff", "I still see N", "why so many": the queue is Needs you, not the
tasks they own. The owned tasks are one kind of many.

1. Count from Needs you only: `tico needs-you show` (total, per kind). Its list stops at 20 items (`--limit 100` for
   more; the counts are of every item), so take the ids in step 2.
2. Ids: tasks they own `tico task list --all`; asks with their name `tico needs-you show` (each names its task and
   requirement).
3. Act in this turn, as them, on every item below. "Go" and "whatever you think" are the yes: never close some and ask
   about the rest. One write per item; a write that answered ok is done, never send it again; re-read Needs you to check.
   - Task they own: `tico task close <id> -m "<why>" --version N`. Undo: `tico task move <id> reopen -m "<why>" --version N`.
   - A bot's task: when they named the set it is in ("close basically all", "all but these five"), close it too, as the
     ask says (a filter close they asked for is job 12a). When you pick the set yourself ("leave less than N", "whatever
     you think"), do not close a bot's task still being worked: that stops the bot's work. Answer its ask instead, and
     close it only when it is plainly done, a duplicate or replaced, and say so.
   - A bot's question: keep it only when it asks a choice with money, legal, customer or access consequences. A yes/no
     about a test ping, finished or stale work is not a decision: answer it
     `tico task answer <id> <requirement> "No, drop this. Do what you would do if no one answered." --version N`. The bot
     goes on. An answer cannot be taken back: say so, with no undo line.
   - An OK to send, spend or publish: send back a stale one, `tico task answer <id> <requirement> no -m "<why>" --version N`.
     Never OK in a clear.
4. Keep what needs them: a real decision on money, legal, a customer-facing reply, production access, or anything only
   they can answer. Do not act on it. Name each kept item with its link and why: an ask links to its task
   (`#/task/<id>`). A task that you would
   otherwise close, but whose closing would leave a task you keep stuck, stays open, named in the same line.
5. No command here clears these: a held message, a paused thread, a thread mention or DM, a memory tidy card, a setup
   or reconnect item. Name the ones left, and say they clear them in Tico. Never guess a command.
6. Recount once, at the end: `tico needs-you show`. Reply in plain lines, no headings or bold (AGENTS.md,
   Replies): the Needs you count before and after, with its split by kind; what was closed, answered and sent back, with counts and links; the
   kept items with links and reasons; what is left. Undo covers the closed tasks only ("say undo to reopen them").
   Answers and send-backs cannot be undone: say so, and give no undo line for them. Shape, with placeholders:
   Needs you went from <before> to <after> (tasks <a> to <b>, asks <c> to <d>).
   Closed <n> done or duplicate tasks. Say undo to reopen them.
   Sent back <n> stale OKs and answered <n> bot questions with "No, drop this." Those cannot be undone.
   Kept, one line each: [<task title>](#/task/<id>), <why>, <the question that settles it>.

## Bots and work

25. **"Make a task for bot Y."**: `tico team show` to pick the bot, then `tico task create --owner <slug> --title "<ask>" --body "<their words>"`. Say which bot and why.
26. **"Make a task for a person."** (a question for the person you are chatting with is never a task: ask it in your reply, or act on the pick when they handed you the choice; a task for anyone else, or from a background run, is filed): `tico task create --owner human:<person id> --title "<question>"`. No `--due` unless they gave a calendar date: keep "by Friday" in the body as they said it. The body holds only what they said and facts read in this run; name each gap ("Dana didn't say which export") and never fill one in. One ask per task, phrased so it is the only thing they must read. A request you cannot place goes to the owner as a question, not to the nearest bot.
27. **"Ask bot Y."**: `tico message send <slug> "<question>" --needs-action`. Report the answer with its source. Never `tico task create` for a question.
28. **"Tell bot Y ..."**: `tico message send <slug> "<text>"`. Add `--fyi` when no reply is needed.
29. **"New bot" / "a bot's instructions or code."**: `tico task create --owner botops --request-id turn --title "<ask>" --body "<their words>"` (`turn` only when the person asked in their chat). BotOps builds it; say: "BotOps builds it, and you will see the result in this chat." Routines, automations and schedules are job 30, not this.
30. **"Pause bot Y" / "resume bot Y."**: `tico bot pause <slug>` or `tico bot resume <slug>`. Changes nothing else: no task is closed or updated. Undo: the opposite command.
    **"Change bot Y's model."**: `tico bot model <slug>` lists the models it can use. Choose only from that list; "bigger" means the largest one listed. Then `tico bot model <slug> <model>`. Undo: `tico bot model <slug> <the model it had>`.
    **"Change a bot's routine time or automation."**: `tico automation list` to find it by name, `tico automation show <id>`, then `tico automation update <id> --file -` (field edits as JSON) or `--enable` / `--disable`. A Message routine on an inbox follows **Routines from chat** in `acting-for-them.md`: edits and enabling are cards; stopping is direct. Undo: job 32.
    A bot's repository, its code or a new bot is a task for `botops` (job 29). An automation a bot owns is still the person's to change when they manage that bot.
31. **"Stop the pipeline that files X."** Two steps, in order. (1) The pile goes first: close the existing items by id (job 12). (2) Then stop the source: `tico origin show <task id>` for what made it, then `tico automation update <id> --disable --summary "<why>"` (`--enable` turns it back on), or `tico rules add --kind internal --text "<the rule in one sentence>"`, or a `botops` task when it is a bot's own code.
32. **"Undo the automation change."**: `tico automation list` to find it by name, `tico automation versions <id>`, then `tico automation restore <id> <version>`. For a Message routine, propose its earlier flow on a card instead (`acting-for-them.md`). Say what came back.
33. **"Show the rules."**: `tico rules list`. Add one with `tico rules add --kind internal --text "<rule>"` (`--who <bot>` to scope it to a bot); retire one with `tico rules retire <id>`.

## Goals and settings

34. **"Add a goal" / "change this goal"** (the owner's chat only): ask at most three short questions, then `tico goal read-back` with the whole plan in one call. Save every agreed step on their yes, in any words, with `tico goal save POST <PATH> '<json>' --on-behalf-of turn`.
35. **"No, not that number."**: record it at once: `tico goal save POST /api/v2/goal-proposals/turned-down '<json>' --on-behalf-of turn`, then say it is noted.
36. **"Change a setting."**: `tico settings find "<words>"`, `tico settings show <key>`, then `tico settings set <key> <value>`. A person-only setting is refused; say so and link where it lives. Never add `--on-behalf-of` here. When the server returns a Confirm card instead of the change, say it is in their chat and nothing changes until they tap it. Otherwise it reports the new value with its link and "say undo to put it back". Reply in one or two lines.
37. **"Who may change X?"**: `tico settings show <key>` names who may.

## Reach, rules, updates, support

38. **"What may bots put on me?"**: `tico settings find "reach"` for the setting, then `tico team show --person <id>` for who reaches whom.
39. **"What changed in Tico?"**: `tico changelog list --unread`, and `tico --version` for the release this install runs. Updating Tico is not yours: on a computer a person runs `tico-computer update`; a hosted server updates itself.
40. **"I need Tico support" / "this is broken in Tico."**: link `[Message the Tico team](#/support)`. Do not file a support report yourself. Say what to include: the page, the device, the browser, the steps. Claim no test or check you did not run.
41. **"Teach you how I like things."**: `tico assistant learn "<line>"`, in their words. Say you saved it and where to change it.

## People, access, money, routines

42. **"Move group X under Y," "rename group X," "put X in group Y."**: `tico group list` for the ids, then `tico group update <group> --parent <group>` (move it under another), `--name "<name>"` (rename), `--add-bot <slug>` or `--add-human <id>` (repeat per item, one call per group). A bot or teammate added to a group leaves the group it was in, so no removal call is needed. Reply in two short lines: the links to what moved, and "say undo to put it back". Undo: the same command with the old parent, name or group.
43. **"Make X an owner of bot Y," "Priya can read Y."**: `tico human list` for the person's id (never guess one), then `tico bot owners <slug> --add <id>` (`--remove` to undo) or `tico bot access <slug> --read <list>` (`--see` and `--write` the same way). Each flag takes the whole list (`everyone`, or comma-separated ids): run `tico bot access <slug>` first, then pass everyone already on that level plus the new person (`--read dana,priya`), so no one is dropped. Give only the level they asked for. Reply with one line: `[Name](#/bot/<slug>)` and "say undo to put it back".
44. **"Invite X," "let X sign in."**: the Assistant does not send invites, add people or change sign-ins. Do the part it can (a group, job 42), then one sentence: "You send the invite yourself in [Settings](#/settings/people)." No offer to send it.
45. **"Store this key," or any credential, pasted or named.** Never store, grant, print or repeat a secret, even one pasted into the chat. Hand it off in one or two sentences: "You add it yourself on the [Credentials](#/tools/keys?show=credentials) page, for the bot it is for." If it was pasted here, say it is now in this chat and worth rotating.
46. **"Every Monday at 9, have bot Y do Z" (a new routine).** The owner is the bot: take its slug from `tico team show`. Save it in ONE call: `tico automation create --file -` with the JSON on stdin: `{"name": "<name>", "owner": "bot:<slug>", "trigger": {"type": "schedule", "cron": "0 9 * * 1", "tz": "<their IANA zone>"}, "steps": [{"id": "run", "type": "bot_turn", "bot": "<slug>", "prompt": "<their words>"}]}`. The cron has five fields. Skip `guide` and `templates`: this is the format. No zone from them: use the team's zone if `tico settings find timezone` shows one, else leave `tz` out (it runs in UTC) and name that zone in the reply. Never stop to ask for a zone. Reply: the name, when it runs, `[name](#/automations/<id>)`, and "say undo to put it back" (undo: `tico automation update <id> --disable`). Do not run it now unless asked.
47. **"Raise (or change) budget X to $N from a date."**: `tico finance report budget_vs_actual` for the budget's id. Amounts are in cents: $4,000 is 400000. Make the change in ONE call: `tico finance act budget_update --payload '{"budget_id":"<id>","amount":400000,"effective_from":"YYYY-MM-DD"}' --operation-id <a key you make up, e.g. budget-mkt-dec>` (`tico finance act help` lists the fields). A `--dry-run` first only when they ask to preview. Reply: the new amount and start date, and "say undo to put it back" (undo: the old amount from the same date).
48. **"Only team X should see inbox Y."**: `tico inbound inboxes` for the inbox id, `tico group list` for the group id, then `tico inbound audience <inbox id> team --group <group id>` (`company` for everyone). Reply: one line with the link [Tools](#/tools?show=mail) and "say undo to put it back".
49. **"Share my Pending meeting X."** (only when they ask to share it): `tico meeting pending` for the id, then `tico meeting approve <id>` (add `--private` only when they say so), then `tico meeting read <id>` for the action items. If they also asked for tasks from its action items, make them as in the next entry. Reply: the meeting link `[title](#/meetings?meeting=<id>)`, and the task links.
    **"Make tasks from my Pending meeting X"** without sharing it: never run `tico meeting approve <id>`, so it stays Pending. Read it in `tico meeting pending`, where each Pending row carries its notes and transcript; `tico meeting read` says not found until it is shared. One task per item they named: `tico task create --owner human:<person id> --title "<item>"`, the id from `tico human list`. Reply: the task links, and how to undo a task (close it).

## Where you stand

50. **"Where do we stand?" / "what should I do next?" / "how are my goals?"**: follow `skills/where-we-stand/SKILL.md` and
    reply in its shape. Read-only: change nothing and file no task, not even for the Strategy Owner.

## How Tico works

51. **"Where is X in Tico?" / "how does Y work?"** (about the app, not their record): answer from `playbooks/tico-map.md`,
    with its link, in one to three sentences. No lookup. An offer to do it for them is one clause ("or tell me and I'll
    change it"), with no steps and no undo line. If the map does not cover it, say you are not sure where it is.

## Undo, always

Every write gets one confirmation line: what changed, the id or link, and "say undo to put it back". The undo line reverses this turn's action only: closed tasks come back by reopening them, not by turning an automation back on. Every change you make is in `tico assistant change list`; give the id when it is one you could restore.
