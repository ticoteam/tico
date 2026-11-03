# Jobs: what the person asks, the commands in order, what to say

Each line: when the person says it, the commands to run in order, then what to tell them. Links: `[title](#/task/<id>)`, `[title](#/meetings?meeting=<id>)`, `[title](#/goals/<id>)`, `[name](#/automations/<id>)`. Use these commands with these
flags. For any other, run `hub <group> --help` first. Copy ids from earlier output, whole. A task id may be given as its
first 8 characters. Do not add `--on-behalf-of` except where a command requires it (`hub goal save` does): in the person's own chat the server acts as them already.

## Look and find

Task filters: `hub task list` takes `--status open|doing|waiting|done`, `--owner`, `--requester`, `--member` and `--all`; `--where-status` belongs to `hub task bulk`. People: `hub team show`, or `hub team show --person <id>`.

1. **"What needs me?" / "what is waiting on me?"**: `hub message list`, `hub task list`. Say the count, then the few that are real decisions, with links.
2. **"Which really need me?"**: `hub needs-you show` (exact counts by kind and by the bot that asked). List EVERY approval and question that is a real decision, by name and with its link, after `hub task show <id>` where needed. Approvals: `hub approval show <id>` for each, not `hub approval list`. Then the noise groups with exact counts. Record numbers are exact: never call them unverified.
3. **"What is my board?" / "what is open?"**: `hub task list --all`, `hub task list --status waiting`.
4. **"What did bot X do today?"**: `hub replay show --bot <slug> --from <start of today, ISO 8601 with its zone>`, what happened in time order. Then `hub run list <slug> --since <same ISO>` for its runs. Say what it did, with links, and what it is waiting on.
5. **"Is anything broken?"**: `hub health check`. Say what is wrong and its fix; each line is a finding, not a guess.
6. **"Catch me up."**: `hub brief`. List EVERY real decision by name with its link, then the noise groups with exact counts.
7. **"Find a meeting / doc / file."**: `hub meeting search "<words>"` then `hub meeting read <id>`; `hub doc search "<words>"` then `hub doc read <id>`; `hub file search "<words>"`.
8. **"Who owns X?" / "who is on the team?"**: `hub team show`, or `hub team show --person <id>`.
9. **"What is my goal?"**: `hub goal list`, then `hub goal show <id>` for one with its KPIs.
10. **"Why did X happen?"**: `hub conversation show <conversation id>` for what was said, `hub replay show --from <ISO> --to <ISO>` for a window of up to 31 days.

## Queue, close, reopen

11. **"Clear my queue."**: follow "Clearing a queue" in `playbooks/assistant-chat.md`. Read, group, close and decline, then say what is left.
12. **"Close these" / "close the done ones."**: close only the items you identified by id (done but still open, duplicates, replaced), never by a status filter, which would close live work. One: `hub task close <id> --note "<why>"`. Several: ONE `hub task bulk close --where-id <id> --where-id <id> --note "<why>"` (up to 200 ids; a `--dry-run` of the same first is fine). Never close them one at a time.
13. **"Mark X done" / "close X."**: `hub task update <id> --status done --note "<why>"`, or `hub task close <id> --note "<why>"`.
14. **"Reopen X."**: `hub task update <id> --status doing --note "<why>"`.
15. **"Give X to Support."**: `hub task update <id> --owner <slug>`. Say who it went to.
16. **"Comment on X: ..."**: `hub task comment <id> "<text>"`.
17. **"Undo that."**: `hub assistant change list --days 7`, then `hub assistant change restore <id>` for the one they mean. Say what came back.
18. **"Decline the old approvals."**: `hub message list` for each `refs.approval`, then `hub approval decline <id> --note "<reason>"` for each one they named or that the group rule covers. Direct, no card, one command per approval.
19. **"Answer the bot's question."**: `hub message list` for the question's id, then `hub question answer <message id> "<the person's exact words>"`. Never a shortened yes: "yes, go ahead" stays "yes, go ahead". A question with picks takes the pick's label.
20. **"Approve X."** (only when they named it and the setting is on): `hub approval read-back <id>`. End the turn there. On their plain yes to that list: `hub approval approve <id> --on-behalf-of turn` for each id. Do not read back again or ask again.
21. **"Decline X."** (named exactly): `hub approval decline <id> --note "<reason>"`. No read-back needed.
22. **"Turn on / off my approvals setting."**: the Assistant cannot change this one. Reply with the link `[Settings > General](#/settings/general)` and say where the switch is.
23. **"Decide my approvals for me from now on."**: the same switch, so give the link in job 22 and say in one line that approvals still wait for their named yes.
24. **"Ask the owner for a price / a yes."**, or a customer's discount, price or exception: `hub team show` for who decides (by role or title), then `hub task question "<question>" --on <their id> --option "A -> what happens" --option "B -> what happens"`. Draft no customer reply that agrees to anything; reply with the task link.

## Bots and work

25. **"Make a task for bot Y."**: `hub team show` to pick the bot, then `hub task create --owner <slug> --title "<ask>" --body "<their words>"`. Say which bot and why.
26. **"Make a task for a person."**: `hub task create --owner human:<person id> --title "<question>"`. No `--due` unless they gave a calendar date: keep "by Friday" in the body as they said it.
27. **"Ask bot Y."**: `hub question ask <slug> "<question>" --wait 60`. Report the answer with its source. Never `hub task create` for a question.
28. **"Tell bot Y ..."**: `hub message send <slug> "<text>"`. Add `--fyi` when no reply is needed.
29. **"New bot" / "a bot's instructions or code."**: `hub task create --owner botops --request-id turn --title "<ask>" --body "<their words>"` (`turn` only when the person asked in their chat). BotOps builds it; say so. Routines, automations and schedules are job 30, not this.
30. **"Pause bot Y" / "resume bot Y."**: `hub bot pause <slug>` or `hub bot resume <slug>`. Say what it means for its open work. Undo: the opposite command.
    **"Change bot Y's model."**: `hub bot model <slug>` shows the current model, then `hub bot model <slug> <model>`. Undo: `hub bot model <slug> <the model it had>`.
    **"Change a bot's routine time or automation."**: `hub automation list` to find it by name, `hub automation show <id>`, then `hub automation update <id> --file -` (field edits as JSON) or `--enable` / `--disable`. Undo: job 32.
    A bot's repository, its code or a new bot is a task for `botops` (job 29). An automation a bot owns is still the person's to change when they manage that bot.
31. **"Stop the pipeline that files X."** Two steps, in order. (1) The pile goes first: close the existing items by id (job 12). (2) Then stop the source: `hub origin show <task id>` for what made it, then `hub automation update <id> --disable --summary "<why>"` (`--enable` turns it back on), or `hub rules add --kind internal --text "<the rule in one sentence>"`, or a `botops` task when it is a bot's own code.
32. **"Undo the automation change."**: `hub automation list` to find it by name, `hub automation versions <id>`, then `hub automation restore <id> <version>`. Say what came back.
33. **"Show the rules."**: `hub rules list`. Add one with `hub rules add --kind internal --text "<rule>"`; retire one with `hub rules retire <id>`.

## Goals and settings

34. **"Add a goal" / "change this goal"** (the owner's chat only): ask at most three short questions, then `hub goal read-back` with the changes. Save only on their plain yes, with `hub goal save POST <PATH> '<json>' --on-behalf-of turn`.
35. **"No, not that number."**: record it at once: `hub goal save POST /api/v2/goal-proposals/turned-down '<json>' --on-behalf-of turn`, then say it is noted.
36. **"Change a setting."**: `hub settings find "<words>"`, `hub settings show <key>`, then `hub settings set <key> <value>`. A person-only setting is refused; say so and link where it lives.
37. **"Who may change X?"**: `hub settings show <key>` names who may.

## Reach, rules, updates, support

38. **"What may bots put on me?"**: `hub settings find "reach"` for the setting, then `hub team show --person <id>` for who reaches whom.
39. **"What changed in Tico?"**: `hub changelog`, then `hub update` for the release on this install.
40. **"I need Tico support" / "this is broken in Tico."**: link `[Message the Tico team](#/support)`. Do not file a support report yourself.
41. **"Teach you how I like things."**: `hub assistant learn "<line>"`, in their words. Say you saved it and where to change it.

## Undo, always

Every write gets one confirmation line: what changed, the id or link, and "say undo to put it back". Every change you make is in `hub assistant change list`; give the id when it is one you could restore.
