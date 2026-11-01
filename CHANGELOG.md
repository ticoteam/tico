# Changelog

All notable changes to Tico are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Each release is published on
[GitHub](https://github.com/ticoteam/tico/releases), and its GitHub release page carries the section
below word for word as its notes.

## [Unreleased]

## [0.4.15] - 2026-10-27

### Fixed
- **SQL queries run at normal speed again.** In v0.4.14, `hub sql` and the SQL API could take many minutes and slow every other request. This release restores the earlier query path and reverts the v0.4.14 change to message searches. Results and access rules are unchanged ([Querying Tico with SQL](docs/hub-sql.md)).
- **Desktop app sign-in shows its instructions.** On macOS, the model sign-in dialog could open with an empty body, hiding the provider link and code, so Claude Code and Codex could not sign in from the desktop app. The instructions now show ([Add a connection](docs/computers.md#add-one)).

## [0.4.14] - 2026-10-26

Desktop app: the 0.4.3 build, unchanged in this release.

### Added
- **Change many tasks at once.** Select tasks on a board or list, or target them with a filter, then update their status, owner, tags or closed state in one action. A dry run shows the counts before you apply changes ([Changing many tasks at once](docs/tasks.md#changing-many-tasks-at-once)).
- **Rules can assign tasks and change tags.** A task rule can assign a task to a person or bot, add a tag or remove a tag ([Task rules](docs/tasks.md)).

### Changed
- **Route checks to available reviewers.** Configure a pool for each review and QA step. Tico assigns each check to the least busy eligible checker and waits when none is available ([Task check pools](docs/tasks.md#check-pools-who-takes-a-tickets-check)).
- **Set task flow rules for each step.** Configure status durations and backlog warnings on individual steps, and use verdict rules to choose where a ticket goes next ([Task flow](docs/tasks.md#step-flow-what-a-step-does-with-a-ticket)).
- **Restarted runs resume in the same provider session.** If a run used an approval or named an outside action, Tico holds it for review. Saved choices apply when the work lane is free ([Interrupted runs](docs/how-it-works.md)).
- **Health separates queued and held work.** Health shows jobs waiting behind an interrupted run and jobs held on purpose. Held work no longer makes a bot look stuck ([Health Monitor](docs/health-monitor.md)).

### Fixed
- **Owners can return tasks to Open and reopen Done.** Dev tickets return to On Deck; if a blocker remains, they stay Blocked with On Deck saved as their next step ([Reopen a task](docs/tasks.md)).
- **A check verdict takes effect after a hold clears.** A review or QA result advances its task after a person finishes the blocking subtask or answers the blocking ask. The task returns to the step it left ([Task holds and checks](docs/tasks.md)).
- **Computer busy checks use CPU usage.** Network waits no longer make a computer look overloaded when its CPU has room ([Computer status](docs/computers.md)).
- **Gateway address changes ask the owner first.** BotOps waits for the owner's confirmation before applying a new address ([AI Gateway](docs/ai-gateway.md)).
- **GitHub rate limits retry quietly.** A rate-limited 403 is treated as temporary; other 403 responses still report an access problem ([Task review gates](docs/tasks.md)).
- **Private tasks stay with an owner who can read them.** Check routing skips candidates without access, and check timeouts no longer close an ordinary subtask. Rules use the access of the person who last saved the action ([Task permissions and rules](docs/tasks.md)).
- **Renamed review steps still check GitHub.** A custom gated step gets the fresh check read before Tico moves its task ([Task flow](docs/software-factory.md)).
- **Message searches avoid repeat privacy checks.** Tico skips recursive checks for rows the existing SQL gate already allows; access rules and response shape stay the same ([Querying Tico with SQL](docs/hub-sql.md)).

## [0.4.13] - 2026-10-26

Desktop app: the 0.4.3 build, unchanged in this release.

### Added
- **Post an update on a goal.** Add a check-in to a live goal from its owner's Goals panel. Its colour follows the goal's measures when they set one, while your words stay with the goal ([Goals](docs/goals-and-kpis.md)).
- **See task automation in Settings.** Settings > Tasks groups automatic actions by task stage and type, with each rule's scope, wake behavior, state and recent usage. Edit a rule or switch it on from the list ([Tasks](docs/tasks.md#task-settings)).

### Changed
- **Managers can work on their reports' tasks.** Task access follows the current org chart, including managers above a report and department groups. Private person-only tasks and approval, verdict, comment and publishing rights keep their own rules ([Tasks](docs/tasks.md)).
- **Sub-goals get colours from their measures.** A sub-goal can receive an automatic colour without a person setting one first; personal colours and existing checks still take precedence ([Goals](docs/goals-and-kpis.md)).
- **Archived measures remain visible on linked goals.** Goal panels show archived KPIs as muted links and explain what they no longer count toward. Restoring or archiving a KPI recalculates linked goals ([Goals](docs/goals-and-kpis.md)).
- **A person can waive a Dev ticket's QA step.** The ticket owner or a manager can record a reasoned waiver for one exact pull-request revision. The waiver does not count as a QA pass ([Tasks](docs/tasks.md#software-review-handoffs-and-qa-defaults)).
- **Held and dismissed runs show their decision.** The run page and Pipeline identify who made the decision, when and why ([Sessions](docs/sessions.md#why-a-run-stopped-or-stalled)).
- **Quarantine stops a running turn with its reason.** Its jobs stay owned and can resume when the bot is active again ([How it works](docs/how-it-works.md)).
- **Reviewers prefer another model family when available.** If no other provider or family can run, a review can use the author's provider instead of waiting ([Tasks](docs/tasks.md#software-review-handoffs-and-qa-defaults)).
- **Recruit links to LinkedBots and names rehearsal mode.** The third-party options link opens LinkedBots, and the builder explains when `TICO_REHEARSAL=1` turns off HQ suggestions ([Recruit a bot](docs/onboarding.md)).

### Fixed
- **Blocked checks now explain the task hold.** A Review or QA check that waits or is blocked by another task puts its Dev ticket in Blocked with the reason. A matching open blocker ask is reused instead of sending another request ([Blocked tasks](docs/tasks.md#numbers-and-the-order-within-a-step)).
- **Green checks clear review holds.** Fresh all-green pull-request checks can satisfy Review questions and clear a review-gate hold ([Review handoffs](docs/tasks.md#software-review-handoffs-and-qa-defaults)).
- **Review moves retry on the right board.** A refused cross-board move retries on its destination board, and an expired hold clears when no retry can resolve it ([Review handoffs](docs/tasks.md#software-review-handoffs-and-qa-defaults)).
- **Actionable notices release stale task deferrals.** Due, snooze, resume and completed-check notices can restart an Open task held only by an outdated next-run deferral; real waits still hold it ([Picking work back up](docs/tasks.md#picking-work-back-up)).
- **Subtask results reach a Waiting parent sooner.** An unrelated open subtask no longer delays a result the parent is waiting for; other waits still apply ([Subtasks and PRs](docs/tasks.md#subtasks-and-prs)).
- **Review results survive varied note formats.** Valid verdicts in notes, comments and final replies are recognized for the current review cycle. A person can also finish their own check-titled child task without a review verdict ([Review handoffs](docs/tasks.md#software-review-handoffs-and-qa-defaults)).
- **Status pages skip finished company tasks.** They continue to include live tasks and private tasks while avoiding unnecessary reads of old completed work ([Status](docs/health-monitor.md)).
- **Health's queue counts match Pipeline.** Health and `hub pipeline` count a bot's queued jobs the same way. Jobs behind an interrupted run awaiting review and jobs held on purpose are named separately, not shown as queued ([Health](docs/health-monitor.md)).
- **Late Dev-ticket notices are reliable.** A notice is recorded only after a manager who can read the ticket receives it, so a ticket with no readable manager is told on a later pass. One ticket that fails no longer stops the rest of the sweep ([Waiting work](docs/tasks.md#work-that-is-waiting)).

## [0.4.12] - 2026-10-26

### Added
- **Set a bot's task effort and context length.** Each starts as **Same as bot** and can be set without a task model, so a longer context can run with the bot's own model. Chat keeps the bot's own settings ([Task settings](docs/harnesses.md#run-a-bots-tasks-on-another-model)).
- **See finished goals.** Done and dropped goals sit under **Finished (N)** in each owner's panel and on a bot's page, newest first. Open one to read it, **Reopen** it, or use **Undo** after **Goal removed** ([Goals](docs/goals-and-kpis.md#the-goals-page)).
- **Read and change settings from the command line.** `hub settings find`, `show` and `set` reach every Settings control that writes, with its name, its place and who may change it. A change uses your own rights ([Settings catalogue](docs/permissions.md#the-settings-catalogue)).

### Changed
- **Bots and tasks keep one session.** A bot's chat and each task keep one session across wakes. Auto-compaction is the only size limit, and a run that hits "prompt too long" compacts first. Each new session says why it started ([Sessions](docs/sessions.md#the-main-chat-and-task-sessions)).
- **Blocked tasks show before In Progress.** A task that cannot move appears in **Blocked** with its reason and start time. Its build clock pauses while blocked, and clearing a hold returns it to its previous step ([Blocked](docs/tasks.md#numbers-and-the-order-within-a-step)).
- **Stopped runs take one line each.** Needs you lists a bot's stopped runs one by one, each with its own **Try again** and **Don't retry** buttons. A computer restart or a usage limit is named as the reason ([Stopped runs](docs/sessions.md#why-a-run-stopped-or-stalled)).
- **Bot task filters are editable.** On a bot's Tasks page, **Needs You**, **Doing** and **Blocked** can be changed, and **Reset to default** restores them. The board icon in the Tasks header is gone ([Tasks](docs/tasks.md)).
- **Suggestion cards say what yes does.** An **Add a KPI** card reads "Add the measure … to …" and can take a target. A Strategy change reads **Add to draft** or **No, leave it**, with one realign button that counts what changes ([Proposals](docs/goals-and-kpis.md#proposals)).
- **Quiet Dev tickets get noticed sooner.** A ticket at a working step shows its age, amber after four hours. Its owner is woken once after two hours without activity, and the manager once after four hours ([Waiting work](docs/tasks.md#work-that-is-waiting)).
- **Step changes wake a bot only through a rule.** A task's step change reaches a bot only when a wake rule names it, so the Release Manager no longer gets every step change in its digest ([Wake rules](docs/tasks.md#wake-rules)).
- **Green checks count as review evidence.** When every GitHub check on a pull request is green, a Review step needs no written test receipt. A failed check still blocks, even when a rerun on the same head passes ([Review handoffs](docs/tasks.md#software-review-handoffs-and-qa-defaults)).
- **Refused review moves retry themselves.** When a Review 1 or Review 2 move is refused, Tico retries it once the cause clears. GitHub access blockers go to DevOps, not Head of Engineering ([Review handoffs](docs/tasks.md#software-review-handoffs-and-qa-defaults)).
- **BotOps waits for your Confirm tap.** A change BotOps makes for you, such as a role change, a new computer's code or a team rule, needs your tap on a Confirm card in your own BotOps chat ([BotOps](docs/permissions.md#botops-uses-the-requesters-rights)).
- **Chat Send responds at once.** Send and Stop acknowledge a tap immediately. A failed send says why: offline, signed out, rate limited, a busy server or an oversized attachment. Your draft and files stay ([Messaging](docs/messaging.md)).
- **Settings addresses open their tab.** Every Settings address opens its tab, so it can be bookmarked. A card you cannot edit names who can. **Remove bot** is now **Archive bot**, which asks first ([Navigation](docs/navigation.md)).

### Fixed
- **Tasks leave Closed on schedule.** A task moved out of Closed kept its old close time, so the 24-hour auto-close never ran again. The fix clears that time, and a one-time cleanup repairs existing tasks ([Done and Closed](docs/tasks.md#done-and-closed)).
- **Tasks finish without stale waits.** Old waits, ask cards and automated notices no longer keep a task open. Opening a task no longer restarts its auto-close timer ([Done and Closed](docs/tasks.md#done-and-closed)).
- **Tasks with a check model delete.** Deleting a task filed with a check model failed with a server error. It now deletes and restores in full. Restoring a task whose type was deleted puts it on General ([Deleting tasks](docs/tasks.md#deleting-tasks-made-by-mistake)).
- **Missed assignments start.** A bot assignment whose wake never ran gets one wake after an hour. After two more hours, Health and the manager's Needs you show the task and why its queued work still waits ([Health](docs/health-monitor.md)).
- **Reopened tasks start their queued work.** A stale next-run gate no longer holds a reopened task's queued job. Real waits still hold it ([Picking work back up](docs/tasks.md#picking-work-back-up)).
- **Held runs stay under review.** A held run keeps its review task across sweeps. Undecided for two hours, it appears in Health and the operator's Needs you, and queued work on the same task folds into the resumed run ([Why a run stopped](docs/sessions.md#why-a-run-stopped-or-stalled)).
- **Your messages reach a busy computer.** A message you send starts on a busy computer ahead of queued bot work ([Computers](docs/computers.md)).
- **Support replies stay visible after a send.** A slow refresh no longer replaces the message you just sent with an older view of the ticket ([Support](docs/support.md)).
- **Review token failures name their cause.** A failed GitHub sign-in shows the safe problem code and HTTP status. Timeouts and rate limits say to retry ([GitHub App](docs/github-app.md)).
- **Busy pages load faster on large teams.** Status, Health, Inbox and Files read each table once, not once per bot or file ([Health](docs/health-monitor.md)).

### Security
- **A bot's KPI stays hidden from people who cannot read the bot.** A goal names its KPIs to everyone who reads it. Linking a bot's KPI to a goal is now refused when a goal reader cannot read that bot, and a confirmed hidden KPI stays hidden ([Goals permissions](docs/goals-and-kpis.md#permissions)).
- **BotOps settings commands use the asker's rights.** A release schedule, team budget, task type, trigger or tag changed through BotOps now needs the rights of the person who asked, not BotOps' own ([BotOps](docs/permissions.md#botops-uses-the-requesters-rights)).

### Known issues
- Saving a bot's task effort without a task model returns a 422 error for bots on the company default model. Not fixed in 0.4.12.
- Compact-first is tracked per conversation. A session shared across conversations can miss one compaction. Not fixed in 0.4.12.

## [0.4.11] - 2026-10-25

Desktop app: the 0.4.3 build, unchanged in this release.

### Added
- **Gateway picks by department and level.** The AI Gateway lineup offers department, level and Fast picks, with model versions ([AI Gateway](docs/ai-gateway.md)).
- **Strategy lenses and market monitors.** Strategy adds lenses, market views by spend, market lenses, and monitors with briefings ([Strategy](docs/strategy.md)).
- **Send images to Gateway models that read them.** Models that can read images receive the pictures attached to a message ([AI Gateway](docs/ai-gateway.md)).
- **Fetch a page through the Gateway.** A bot can fetch a web page through the AI Gateway ([AI Gateway](docs/ai-gateway.md)).
- **Webhooks gain outgoing endpoints and rule actions.** Incoming webhooks have a v2 API, outgoing endpoints can be set up, and rules can act on webhook events ([Webhooks](docs/webhooks.md)).
- **Setup imports strategy.** A company file from tico.dev/setup brings its strategy with it ([Strategy](docs/strategy.md#setup)).

### Changed
- **Release now asks even with nothing pending.** A person's note on a release request is passed to the Release Manager even when no ticket is at Pending Release ([Releasing](docs/releasing.md)).
- **Status pages read less.** The status page batches per-bot reads and no longer loads task bodies ([Health Monitor](docs/health-monitor.md)).
- **Company link works for a local install without a public address.** A local install links to its Tico company with no public address ([Install Tico](docs/install.md#sign-in)).
- **Remove a person who left.** A person with no history can be removed from People ([Humans and access](docs/people.md)).
- **Hosted servers update when idle.** Hosted servers take the release Tico supports, when they are idle ([Updates](docs/updates.md)).
- **Support access sits under its request.** The button and log appear under the request, with one session ID shared with HQ ([Support](docs/support.md)).
- **The webhook relay takes the short address.** `hooks.tico.dev` works as a relay address ([Webhooks](docs/webhooks.md)).
- **Gateway shows Coming soon for services not live yet.** A service that is not live yet is labelled as such instead of failing ([AI Gateway](docs/ai-gateway.md)).

### Fixed
- **A raw page change no longer trips a bet.** A raw change on a monitored page does not count toward a strategy bet ([Strategy](docs/strategy.md)).
- **A task a Lead was handed starts.** Pulls no longer block the wake that starts it ([Tasks](docs/tasks.md)).

## [0.4.10] - 2026-10-25

Desktop app: the 0.4.3 build, unchanged in this release.

### Added
- **Create a teammate's password.** **Create password** in People makes a Tico account and shows its password once ([Humans and access](docs/people.md)).

### Changed
- **Self-run servers get their AI Gateway key from their company link.** The key comes from the company link rather than being entered by hand ([AI Gateway](docs/ai-gateway.md)).
- **The company heartbeat reports a new version within the hour.** A changed version is sent to Tico within an hour of the change ([Updates](docs/updates.md)).
- **Clearer wording for adding people and triage.** Add-human copy and triage wording are clearer, and "just now" shows for changes under a minute ([Humans and access](docs/people.md)).

### Fixed
- **People and Support pages show correct times and counts.** The invited time, Support count and rows, the access-off message and classification wording are corrected ([Humans and access](docs/people.md)).

## [0.4.9] - 2026-10-25

### Fixed
- **Setup recovers from interrupted research.** After a server restart, the strategy step shows a form instead of waiting for research that can no longer finish ([Setup](docs/strategy.md#setup)).
- **Late research leaves your strategy draft alone.** Research that runs past 10 minutes shows as failed. A late answer no longer fills the draft after the form has returned ([Setup](docs/strategy.md#setup)).
- **Clearing setup inputs cancels the old research.** Clearing both the website and description stops the running research and returns the strategy step to a form ([Setup](docs/strategy.md#setup)).
- **Setup recovers when a research start goes unconfirmed.** Going back and clicking **Next** again checks for matching research already started before creating another run ([Setup](docs/strategy.md#setup)).
- **Setup refuses non-public websites when you save.** An internal or private address is rejected before it can reach the Librarian's market task ([Setup](docs/strategy.md#setup)).
- **Goals keep links to hidden bets.** Saving a goal no longer clears its link to a retired or unpublished bet the editor cannot see. The selector shows **Retired or unpublished** instead of **None** ([Goals](docs/goals-and-kpis.md#the-goals-page)).
- **Summary autosaves no longer disrupt other strategy edits.** Saving a bet or publishing uses the latest draft revision after the summary saves, avoiding a false conflict ([Strategy](docs/strategy.md#the-page)).
- **Publish recognizes citation and guide changes.** The button becomes available when only a bet's citations or the strategy guide changed ([Publishing](docs/strategy.md#publishing)).
- **Moving bets detects conflicting strategy edits.** Each position change checks the draft revision. If a move fails partway through, the page reloads the saved order ([Strategy](docs/strategy.md#the-page)).
- **SQL reads show the team's current vision.** The company profile follows the vision visible to the team, including after upgrades and the first strategy publication ([Strategy](docs/strategy.md#what-bots-see)).

## [0.4.8] - 2026-10-25

### Added
- **Write and publish your team's strategy.** Owners can draft a summary, vision, bets and four strategy sections, then publish numbered versions with a note. The draft stays private to the owner. Bots read the published summary on each run; the CEO Bot proposes changes for the owner to confirm ([Strategy](docs/strategy.md)).
- **Connect goals to the bets they serve.** A bet's health comes from its KPI-backed goals. A threshold crossing, changed market evidence or overdue check date prompts the CEO Bot to propose a response. Publishing a rewritten or retired bet asks the owner whether to keep, move or retire its goals ([Strategy checks](docs/strategy.md#the-fit-check)).
- **Setup starts with your strategy.** **What are you building** and **Your bets** turn a website or description into claims you can keep, edit or strike. Research fills a draft without publishing it or overwriting your edits. Without a model or after failed research, you can complete the same fields by hand ([Setup](docs/strategy.md#setup)).
- **Manage wake rules in Settings.** **Settings > Rules** brings routines and wake rules together. Rules can apply to a team, task type, department, bot or task, with outcomes such as keeping a note, batching news or waking a bot. Each run records its rule, and the page shows seven-day runs and no-op rates ([Wake rules](docs/tasks.md#wake-rules)).
- **Choose a profile for each bot.** **Doer**, **Reviewer**, **Lead**, **Responder**, **Monitor** and **Custom** control what wakes a bot and which tasks it takes. Choose one in the bot editor or with `hub bot profile`. Leads can receive a daily digest; Custom lets you edit the rules ([Profiles](docs/how-it-works.md#profiles)).
- **Task Manager follows up on stalled work.** This built-in helper checks departments hourly during weekday business hours and acts on quiet tasks, overdue questions and missing handoffs. Empty checks start no model run. It is on by default; the owner can pause it. Private tasks remain outside its access ([Task Manager](docs/task-manager.md)).
- **Run budgets stop runaway bot loops.** Team defaults allow 300 runs per hour and 2,500 per day for each bot, with per-bot overrides. A bot that reaches its budget keeps its queued work and shows the hold in Health. Release it or raise its budget to continue; a person's messages bypass this budget ([Run budgets](docs/how-it-works.md#what-happens-when)).
- **Schedule daily releases or request one now.** Owners and admins can set one or two daily release times. Pending work creates one task for the Release Manager; an unfinished release task is reused. Authorized people, the Release Manager and Head of Engineering can request **Release now** from the board, Settings or CLI ([Releasing](docs/releasing.md)).
- **Review and QA results name the reviewed revision.** Subtasks record an approve, changes, pass or fail verdict with the pull-request head, shown on task cards and lists. `hub task done --verdict --head` and the corresponding MCP tools return that result to the parent owner. Duplicate open checks for the same head are refused ([Review handoffs](docs/tasks.md#software-review-handoffs-and-qa-defaults)).
- **Review suggestions for quieter wake rules.** A weekly check suggests turning frequently unproductive wakes into notes. **Settings > Rules** shows the affected rule and recent results, with **Apply**, **Dismiss** and **Undo**. Protected wakes and scheduled or assigned work are excluded; no suggestion changes a rule automatically ([Wake rules](docs/tasks.md#wake-rules)).

### Changed
- **Market moves inside Strategy.** Open **Strategy > Market** from the sidebar; existing Market links redirect there. Vision moves from Goals to Strategy. Existing vision and description text starts a private strategy draft for the owner to review ([Strategy](docs/strategy.md)).
- **Profiles replace the Wakes setting.** New Doer bots batch ordinary task news for 15 minutes. Existing bots receive a profile on upgrade; former Token saver bots retain a 30-minute batch window. People, handoffs and awaited results keep their immediate paths, subject to bot holds ([Profiles](docs/how-it-works.md#profiles)).
- **Routine task news waits for the next run.** Events without a matching wake rule become notes. Ordinary comments and other bots' status changes no longer start a run by default. Assignments, review requests, blockers and awaited results have explicit rules; task-step rules control who hears each handoff ([Wake rules](docs/tasks.md#wake-rules)).
- **Pull-request checks produce one completed result per revision.** Individual check completions no longer wake the owner. After all checks finish, an eligible working task receives one result for that head; other task steps keep a note unless a completion wake is armed ([Task PR events](docs/github-app.md#task-pr-events)).
- **Quiet tasks are checked before waking their bots.** Stall and status checks skip live sessions and recorded waits. Quiet task triage can record a wait, close work found finished, or wake work found stuck; uncertain cases reach Task Manager. Owners and admins can turn triage off ([Waiting work](docs/tasks.md#work-that-is-waiting)).
- **Documentation follows the published release.** Publication creates one documentation task for the release and wakes the applicable QA owners. Release notes follow on their own task. Missing pre-release docs no longer hold the release checklist; installed changelog entries arrive in a later build containing the notes ([Releasing](docs/releasing.md)).

### Fixed
- **Fast failures no longer loop without a pause.** Failures within a minute start a 15-second wait that doubles on repetition. Five consecutive fast failures hold the bot, preserve queued work and notify its operator. Health names the error, and **Release** clears the hold ([Failure recovery](docs/how-it-works.md#what-happens-when)).
- **Held bots can recover after an outage.** A bot held for repeated fast failures tries one queued run after 30 minutes. Further fast failures double that wait up to eight hours; a normally completed run releases the bot and closes its Needs-you item. You can still release it immediately ([Failure recovery](docs/how-it-works.md#what-happens-when)).
- **Provider limits are recognized inside longer errors.** A weekly or other usage-limit message now applies the provider hold even when an older runner reports it inside a different error. The login waits until the stated reset instead of repeatedly retrying ([Usage-limit recovery](docs/how-it-works.md#what-happens-when)).
- **Oversized sessions stop repeating the same failure.** Errors such as “too many total text bytes” or “Prompt is too long” make the next run start a fresh provider session. An embedded usage limit still takes its usage-limit hold ([Session recovery](docs/how-it-works.md#what-happens-when)).
- **SQL history reads do less repeated privacy work.** Message counts and small turn or attempt queries avoid repeated history-wide checks while preserving row visibility and query deadlines ([Querying Tico with SQL](docs/hub-sql.md)).
- **Busy histories cause less waiting for bot work.** Idle claims and input polls avoid unnecessary database writes. Waiting-note reads skip settled history, and task, inbox and meeting-provenance reads reuse or batch lookups. Existing message and task privacy checks still apply ([How Tico works](docs/how-it-works.md)).
- **Unreadable HTTPS configuration gives a clear refusal.** An inaccessible Caddy configuration directory now returns a sanitized configuration error instead of leaking an unexpected startup exception on older Python versions ([HTTPS setup](docs/install.md#caddy-and-a-separate-visual-site)).
- **Task refusals stay beside the attempted action.** Failed moves show their reason on the card or row. A refused subtask stays in the Add subtask form with its title preserved ([Tasks](docs/tasks.md)).
- **Install help states the company-file size limit.** The company YAML file and its normalized plan must each fit within 512 KiB. The help now explains that setup refuses larger inputs before provisioning; this documents the existing limit ([Install Tico](docs/install.md#quick-start-on-your-own-computer)).

### Security
- **Docker Gateway turns use separate Linux users.** Gateway and isolation-capable harness turns receive private credentials and homes, with processes and temporary files cleaned before reuse. Checkout groups restrict access to sibling workspaces. Shared-login harnesses still run as the shared bot user, and base clones and runner operations remain shared. Update the Docker runner for this protection ([Isolation limits](SECURITY.md), [Computers](docs/computers.md)).

## [0.4.7] - 2026-10-25

### Added
- **The CEO Bot acts on goals at level 3.** At **Acts on its own**, it creates or reorders bot tasks to recover red or stalled goals and settles competing work between Heads. It records each decision with a reason and an **Undo**, and explains each goal recovery in a comment. Work reserved for the owner stays a proposal ([Your CEO Bot](docs/ceo-bot.md#the-three-levels)).
- **The CEO Bot answers from recorded rules.** At **Acts on its own**, it answers bots' questions only from recorded decisions, team rules or approved proposals, with a citation. Questions those sources do not cover go to the owner. A broken bot blocking a goal gets a repair request to BotOps ([Your CEO Bot](docs/ceo-bot.md)).
- **The turn fixer frees proven-dead turns.** It releases stopped starts and expired turns whose computer is confirmed no longer running the bot. Tasks and history stay. A slow reply or healthy wait keeps its place; a stopped start stays stopped. Turns that may have pushed, sent or paid wait for review before retrying ([The turn fixer](docs/computers.md#the-turn-fixer)).
- **Health explains company-turn incidents.** One **Company turns** finding shows wait reasons, oldest waits, recent progress, computer load and the likely bottleneck. An incident still open after five minutes gets one BotOps task, or Support if BotOps is unavailable. The owner gets one notice after an hour or when the main wait needs a person ([The turn fixer](docs/computers.md#the-turn-fixer)).

### Changed
- **Wake presets batch more background news.** Comments from Tico or bots, task changes and other nonblocking notices share the preset's batch window. Human messages, assignments, questions, awaited results, blockers, review requests, approvals and live feeds keep their immediate wakes. A held batch does not delay them ([Wakes](docs/creating-bots.md#7-harness-model-effort-and-fallback)).

### Fixed
- **Undo keeps the CEO Bot from repeating a change.** At level 3, a task change the owner previously undid becomes a proposal if the CEO Bot tries it again. This covers creating the same task for the same bot, reordering, reassigning and closing ([The CEO Bot log](docs/ceo-bot.md#the-ceo-bot-log)).
- **Batched notices no longer block immediate work.** A notice waiting for its batch no longer makes **Run now**, resumed work, meeting turns, update requests or automatic task starts look as though a run already waits. ([Wakes](docs/creating-bots.md#7-harness-model-effort-and-fallback)).
- **BotOps actions for a person wake bots immediately.** When BotOps acts with a person's delegated rights, notices of the action count as that person's action. They no longer wait in a wake-preset batch ([Wakes](docs/creating-bots.md#7-harness-model-effort-and-fallback)).
- **Update snapshot cleanup handles disappearing temporary files.** If SQLite removes a temporary journal during cleanup, the updater skips the missing file. An unfinished snapshot reports that it did not finish instead of failing with a traceback; the next attempt still clears leftover partial copies ([Releasing](docs/releasing.md)).

## [0.4.6] - 2026-10-24

Desktop app: the 0.4.3 build, unchanged in this release.

### Added
- **Decision outcomes are logged.** Ad-hoc decisions and a bot's task results record their outcome, for the decision readout ([Learnings](docs/learnings.md)).

### Changed
- **Company stop counts only turns holding a place.** A turn still waiting for its place no longer counts toward the company stop level ([Health Monitor](docs/health-monitor.md)).
- **Needs you loads a page at a time.** Long lists of items load in pages instead of all at once ([Tasks](docs/tasks.md)).
- **The company card rereads credit at most once a minute.** A self-run server's company card no longer rereads its credit on every view ([Install Tico](docs/install.md#sign-in)).

### Security
- **Runner and relay use the system trust store.** They verify TLS with the operating system's certificates first, with the bundled list as a fallback ([Updates](docs/updates.md)).

## [0.4.5] - 2026-10-24

Desktop app: the 0.4.3 build, unchanged in this release.

### Added
- **The CEO Bot logs its task decisions with Undo.** At **Acts on its own**, its decisions on bot tasks are logged, and the owner can Undo them. Work reserved for the owner stays a proposal ([The CEO Bot log](docs/ceo-bot.md#the-ceo-bot-log)).

### Changed
- **Busy-turn warnings and stops apply per person.** A person's bots get a warning at 40 busy turns and a stop at 100, counted across the org. The old "no more slots" alert is gone; an admin setting controls the levels ([Health Monitor](docs/health-monitor.md)).

### Fixed
- **Messages from bots to a held bot stay bounded.** Bot-origin messages waiting for a held recipient are kept in a bounded queue ([How it works](docs/how-it-works.md)).
- **Grok sign-in status is read safely.** The Grok auth status is read without following links, and its folder ownership check is bounded ([Computers](docs/computers.md)).

## [0.4.4] - 2026-10-24

Desktop app: the 0.4.3 build, unchanged in this release.

### Changed
- **Task-list links carry fewer fields.** Task lists and live task events omit empty link fields and link update timestamps. API clients that need the full link record should read the task detail or its links endpoint ([Build your own frontend on Tico](docs/custom-frontend.md#key-endpoints)).
- **Bot inboxes return bounded batches.** The messages inbox returns at most 100 readable messages and 100 readable tasks. API, CLI and MCP callers can use `more_messages` and `more_tasks` to tell when more are waiting ([The Tico API](docs/api.md#rate-limits-and-size-limits)).
- **Runner updates hand back waiting work sooner.** Turns waiting for a login, usage allowance, turn share or checkout return to the queue. Active turns have five minutes by default to finish before resumable work is saved and requeued ([Updates](docs/updates.md#a-docker-runner)).
- **Unchanged tasks get fewer stall checks.** Checks back off from 30 minutes to eight hours while a task stays unchanged. Changes to its state, owner, waiting person or relations restart the interval ([System routines](docs/system-routines.md#the-jobs)).
- **Custom frontends can narrow live task events.** Filter the event stream by task, or by the person or bot who owns or requested it. Streams reconnect after about fifteen minutes ([Build your own frontend on Tico](docs/custom-frontend.md#live-events)).
- **Old streamed reply fragments are cleaned up.** The hourly cleanup removes text-piece events for runs finished more than fourteen days ago. Saved messages and thought events remain ([Build your own frontend on Tico](docs/custom-frontend.md#live-events)).
- **Decision readouts retain more answers.** Stale ticket reviews, nightly learning routing and Slack routing record answer labels, values and confidence ([Learnings](docs/learnings.md#nightly-learning-run)).
- **Hosting help explains changed stop times.** The account and billing guides cover owner notices, updated grace deadlines and renewed reminders ([Your Tico account](docs/account.md#hosting-grace-and-retained-data)).
- **Chat pictures leave room for the reply.** Picture thumbnails use about a quarter of the message column. Open a picture fitted to the viewer, then zoom or pan ([Visuals in replies](docs/visuals.md#in-a-chat-or-task-conversation)).
- **Search and change both bot models.** One search replaces the Computer, Model and Effort filters in **Settings → Bots**. Bulk **Change model** offers **Bot model**, **Task model** or **Both models** ([Harnesses](docs/harnesses.md#run-a-bots-tasks-on-another-model)).

### Added
- **Ask about updates beside the feed.** Updates includes your existing Assistant chat, or the CEO Bot for the owner ([The Assistant](docs/assistant.md#on-the-updates-page)).
- **Task rules join Routines.** Create and edit rules in **Settings → Routines**, with department permissions and saved on or off controls. The default **Close 24h after done** rule closes eligible, untouched done tasks; reopening undoes a close ([System routines](docs/system-routines.md#task-rules)).
- **Setup lays out Recruiting seats.** Your chosen chart creates seats with job descriptions and reporting lines, ready for recruitment. Failed chart imports show a reason instead of a sample company ([Finish setup](docs/onboarding.md#the-team-builder)).
- **Invite people through Tico accounts.** Owners and admins can use **Send invite** on a person's roster row ([Humans and access](docs/people.md#invites)).
- **Compare your team with starter charts.** Ask the CEO Bot to propose Recruiting seats from Tico's published charts. One confirmation adds the proposed seats ([Finish setup](docs/onboarding.md#re-org-suggestions)).
- **Self-install setup carries full company charts.** AWS, SSH and local setup support charts up to 300 seats without a separate in-app import. Saved charts survive retries ([Advanced installs](docs/install-advanced.md#the-aws-server)).
- **Link a self-run server to its Tico company.** The **Tico company** card in **Settings → General** lets an owner or admin connect a server, with credit information and a daily version heartbeat ([Install Tico](docs/install.md#sign-in)).
- **Hosted pages and SVG can open inline.** When Tico file hosting is configured, cleaned copies of pages and SVG open inline from the company's file address. Access checks still apply ([Files](docs/files.md#tico-file-hosting)).
- **Hosted visuals gain an installation check.** Operators can apply and verify visual-origin configuration on an existing hosted installation ([Advanced installs](docs/install-advanced.md#apply-hosted-visual-configuration-on-the-same-installation)).

### Fixed
- **Sent chat drafts stay cleared after sign-in.** After a successful resend, the composer drops the saved draft, so sent text does not reappear ([Using Tico](docs/using-tico.md)).
- **Send clicks survive chat redraws.** A redraw between pressing and releasing Send no longer discards the click, including in task chat ([Using Tico](docs/using-tico.md)).
- **New bots show why their login is queued.** A bot waiting before its first turn shows the login wait reason ([Computers](docs/computers.md)).
- **Returning task models receive fallback progress.** A task model resuming its older provider thread also receives the latest completed task checkpoint ([Harnesses](docs/harnesses.md#run-a-bots-tasks-on-another-model)).
- **Busy bots can settle unrelated stopped work.** An active run no longer prevents recovery of stopped work on another task ([How it works](docs/how-it-works.md#what-happens-when)).
- **Stopped-run decisions stay visible and take effect.** **Try again** and **Don't retry** choices made while the related run is active are saved and applied when it ends ([How it works](docs/how-it-works.md#what-happens-when)).
- **Grok sign-in repairs its folder permissions.** The isolated Docker computer repairs ownership of Grok's default folder before sign-in, keeping the login location and file modes ([Computers](docs/computers.md#signing-out-and-switching-accounts)).
- **Long goals leave room for the bot name.** A wide desktop header gives up space before the bot name, so long goals truncate less ([Team chart](docs/org-chart.md)).
- **Task headers wrap when controls do not fit.** Search, task types and **New task** no longer overlap at desktop widths ([Task types and steps](docs/tasks.md)).
- **Large update snapshots share one time budget.** The Docker updater bounds the snapshot copy and the check together ([Updates](docs/updates.md#the-servers-own-updater)).
- **Task pages make fewer repeated database reads.** Task details, lists, status and Needs you batch repeated lookups within one read snapshot; visibility checks stay in place ([Task types and steps](docs/tasks.md)).
- **SQL history reads reuse permission checks.** Related task, message, run and meeting checks share request-local results within the existing time budget ([Querying Tico with SQL](docs/hub-sql.md)).
- **Busy database writes return a retry response.** A lock timeout returns `503 busy` with `Retry-After: 2`. Custom clients should wait and retry with the same `Idempotency-Key` ([Build your own frontend on Tico](docs/custom-frontend.md#errors)).
- **Idle tabs and unchanged pages do less work.** More pages pause polling while hidden, and image thumbnails load near the viewport ([Using Tico](docs/using-tico.md)).
- **Unsupported previews avoid unnecessary file delivery.** The media worker skips a version it cannot derive a preview for, such as an SVG. Missing preview tools still do not fail uploads ([Files](docs/files.md)).
- **Review refusals explain what could not be verified.** A blocked review move names the failed GitHub or check condition and asks you to retry the move ([Task types and steps](docs/tasks.md#task-types-and-steps)).

## [0.4.3] - 2026-10-24

Desktop app: the 0.4.3 build, released with this version.

### Added
- **Five more roles can become bots.** Recruit an **Equity Manager**, **Fundraising Manager**, **Controller**, **Scheduling & Bookings** or **Quality & Inspections** bot from the catalog ([Starter bots](docs/starter-bots.md)).
- **Edit what your Assistant has learned.** **What it has learned** lists your saved preferences under **About you**. Edit or delete a line there. This layer is private to you ([The Assistant](docs/assistant.md#what-it-has-learned)).
- **Teach the CEO Bot about the company.** Owners also get **About the company** on **What it has learned**: the vision, company goals and lessons taught in chat. Only the owner reads and changes it ([Your CEO Bot](docs/ceo-bot.md#what-it-has-learned)).
- **Drag tasks between board steps.** On desktop, drag a card to change its step or its order within a step. On phones, change the task's **Step** instead ([Task types and steps](docs/tasks.md)).
- **Change a task's owner from its card.** If you can edit the task, choose its owner to find and assign a person or bot.
- **See a teammate's plate.** Use the person picker on **Tasks** to see what waits on a teammate, what they own and what they asked others to do ([Task types and steps](docs/tasks.md)).
- **Task stats show trends over time.** Choose day, week or month bars with a moving average and a person filter. Date ranges extend to one year.
- **Customize a bot's sidebar.** If you manage the bot, use the pencil to reorder, hide and resize sections and to add or edit task-filter tabs ([Using Tico](docs/using-tico.md)).
- **Limit a bot to one task.** People who manage a bot can use `hub bot scope` to restrict its claims to one task and its direct subtasks. Other work stays queued ([Who can see, read and write to a bot](docs/permissions.md)).
- **Read pipeline counts in one request.** `hub pipeline`, the matching MCP tool and the pipeline API report step counts, time in step, waits, open pull requests, bot queues and failures ([Task types and steps](docs/tasks.md)).
- **Grant bots aggregate outcome statistics.** The signed-in owner can let a bot read weekly proposal outcomes and MCP tool statistics. These totals expose no proposal text or person's identity ([Querying Tico with SQL](docs/hub-sql.md#the-useful-tables)).
- **Set up Docs with the Librarian.** On an empty Docs page, answer **Where are your docs?** to register sources for the Librarian to map ([The Librarian](docs/librarian.md#first-run-setup)).
- **Guide the Librarian's market research.** Owners answer four questions about the business, buyers, competitors and what to watch. The finished research sets what Market tracks ([The Librarian](docs/librarian.md#first-run-setup)).
- **Tico file storage for provisioned companies.** When HQ has provisioned file hosting, new uploads can use Tico storage. Owners can move existing versions with checked copies, or move them back ([Files](docs/files.md#tico-file-hosting)).
- **Reviewer bots can use a separate GitHub identity.** Operators can configure a second GitHub App for reviewers. Reviews name the exact commit reviewed ([GitHub App](docs/github-app.md#separate-identity-for-reviewers)).
- **Review handoffs check current test evidence.** Engineering review handoffs check CI and a test receipt for the current pull-request head before waking a reviewer. Failing checks, or missing, stale or failed receipts, refuse the handoff ([Task types and steps](docs/tasks.md#software-review-handoffs-and-qa-defaults)).

### Changed
- **A person completes bot onboarding.** New bots learn and prepare in **Onboarding**, then report that they are ready. A person above them on the org chart marks onboarding complete. Onboarding bots cannot take tasks, run routines, send email or post to Slack ([Finish setup](docs/onboarding.md#a-bots-stages)).
- **Boards keep working steps in view.** **Waiting** becomes a tag on the current step. Declined tasks go to **Closed**; finished work sits under **Done** and **Closed** ([Task types and steps](docs/tasks.md)).
- **Recruit a role before creating its bot.** **Team → Add bot** creates a **Recruiting** seat. Pick from up to three candidates, one recommended; your pick creates the bot in **Onboarding** ([Finish setup](docs/onboarding.md#recruiting-a-seat)).
- **Owners chat with the CEO Bot.** The owner's chat and menu replace the Assistant with the CEO Bot. Everyone else keeps their Assistant, and earlier Assistant chats stay readable ([Your CEO Bot](docs/ceo-bot.md#your-chat-with-it)).
- **Eligible assigned tasks start on free turns.** Tico starts the highest-ranked eligible task when a bot has room. **Keep N tasks going** sets a task cap; **0** disables automatic starts ([Task types and steps](docs/tasks.md#automatic-starts)).
- **Waiting turns free computer capacity.** Computers and Health distinguish **Running**, **Starting**, **Waiting** and **Queued**. Update the runner to get these rules; older runners keep their existing leases ([Computers](docs/computers.md#turn-counts)).
- **Overloaded computers hold new starts.** Sustained CPU or memory above 90% for five minutes holds new starts. Running turns continue, and starts resume when the overload clears ([Computers](docs/computers.md#turn-counts)).
- **Account status explains each problem.** One dot beside your name shows the most serious status. Open the account menu for plain-language problems and a **Fix** action for each.

### Fixed
- **Scoped SQL history avoids scanning every past run.** Small and time-bounded run-history queries resolve only the runs they need. Queries past their deadline return a timeout that says how to narrow them ([Querying Tico with SQL](docs/hub-sql.md)).
- **Gemini works on isolated Docker runners again.** Turns that stopped with "could not write the Gemini settings" can start. Update the runner to get the fix ([Harnesses](docs/harnesses.md)).
- **Chat shows sending progress and uncertain delivery.** **Send** stays busy, then shows **Still sending…** on slow sends. A send that is not confirmed keeps the draft; Retry reuses its original identity for 48 hours in the same tab ([Using Tico](docs/using-tico.md)).
- **Disabled send controls explain why.** Empty Send controls say what to enter, and phone users see the busy reason below the composer.
- **Failed runs say what ended them.** Run details record the limit reason, runtime, login and model. A turn that never started returns to the queue with a backoff instead of appearing as a crash.
- **Changing a connection rechecks usage holds.** Switching a bot's connection or login rechecks the hold against its current connection.
- **Fallbacks skip the same exhausted login.** A fallback that uses the limited primary login is skipped; Tico tries an eligible alternative or holds for the reset ([How it works](docs/how-it-works.md#what-happens-when)).
- **Task-model fallbacks use the latest checkpoint.** If a task model cannot run, the bot's model starts a fresh session from the task checkpoint rather than resuming incomplete history ([Harnesses](docs/harnesses.md#run-a-bots-tasks-on-another-model)).
- **Missing Claude sessions recover with follow-ups.** When a saved Claude conversation is missing before any event, the runner starts a fresh session and carries follow-ups into it.
- **Session rotation keeps the previous thread until ready.** The runner saves a replacement thread only after its host confirms the new session, so a failed start no longer discards the previous thread. Update the server and runner for this confirmation.
- **Login warnings identify affected bot connections.** Computers warn when bots use an API-key login, whose billing is separate from subscription allowances. Health also flags missing or rejected logins ([Computers](docs/computers.md#connections)).
- **Missing cost data stays missing.** API cost totals and bot cost readings show no reading when relevant runs lack usage or a known price. A measured zero still shows **$0.00** ([Usage](docs/usage.md)).
- **People can close typed tasks.** A person can close any task, including tasks on boards with required steps.
- **Finished task lists load reliably.** Unfolded finished board columns load their tasks when restored, and a task that appears in both open and finished reads shows once.
- **Boards and desktop windows avoid idle redraws.** Unchanged live events no longer rebuild the board or the open task. Hidden windows pause animations, and repeated polls reuse unchanged responses.
- **Large viewer files stay visible.** Opening a file larger than the cache budget no longer revokes its address before display. The fix also applies to chat pictures ([Visuals](docs/visuals.md)).
- **Visual links survive delegated reads and first startup.** Visual addresses are signed for one reader while keeping delegated access checks. Concurrent first requests can no longer read a partly written visual secret ([Visuals](docs/visuals.md)).
- **Comment results distinguish delivery from a bot wake.** API responses set `woke` only when a bot job was queued. The additive `delivered` field identifies comments delivered to a person ([The Tico API](docs/api.md)).
- **Slack task replies include their context.** A bot's reply about a task names it and links to it. Private tasks stay out of Slack ([Tico in Slack](docs/slack-gateway.md)).
- **Dependent worktrees start after their blocker merges.** A dependent task gets its checkout once its blocker reaches the merged-code cutoff ([Task worktrees](docs/worktrees.md)).
- **Finished tasks shed rebuildable scratch.** After a closed task's worktree is removed, cleanup can remove its unused build caches and environments. Unsaved or unpushed work, notes and logs stay ([Task worktrees](docs/worktrees.md)).
- **Busy servers do less blocking write work.** Runner polling, scheduler work and meeting-provenance reads avoid repeated work and long write holds.

### Security
- **Send approvals cover the approved text.** A Tico email send approval includes the draft's text hash. Changing the text requires a new approval ([Message bots](docs/mail.md)).
- **Investor messages require a person's approval.** The Fundraising Manager template requires a separate yes for every investor message, including replies, forwards and invitations, even when sending is enabled.
- **Assistant task actions stay within your tasks.** A request authorizes a task change, comment or link only when you own, requested or hold a role on that task. The owner's CEO Bot chat uses the same rule ([The Assistant](docs/assistant.md#private-to-you)).
- **Bundled libraries and trust roots are refreshed.** DOMPurify, the PDF parser and the bundled TLS trust lists are updated.

## [0.4.2] - 2026-10-23

### Added
- **Sign in to Grok Build and Cursor from Computers.** Settings > Computers supports sign-in, sign-out and account switching for the computer's shared login. Grok reports its saved sign-in state. Cursor keeps Tico sign-ins in files; an existing macOS Keychain login needs a new sign-in from Tico.
- **Sign out of Muse Code and switch accounts.** Cancelling a switch preserves the previous login. Tico uses Muse's file credential backend on Mac as well as Docker. A login saved only in the macOS Keychain asks for a new sign-in from Tico; Tico does not read or copy that Keychain item. Update the server and runner to use these controls.
- **Health names blocked bots and stalled work.** The checks show queue counts, oldest ages, blocking reasons and actions for blocked bots, stalled tasks and bot pull requests.
- **Finished runs record tokens and estimated cost.** Usage includes cached tokens and records why usage is missing or a model is unpriced. Existing legacy usage history is retained.
- **Hosted setup carries company charts up to 300 seats.** A private plan bucket and verified file carry larger charts, and a retry can finish a failed first boot.
- **Live meetings can be private.** The person connecting a meeting can share it with selected people and attached bots. Unauthorized people cannot list or open it. Existing clients retain the default team visibility.

### Changed
- **First-run provider choices follow the selected plan.** Provider details appear for the applicable choice, phone layouts put the main choice first, and computer sign-in help follows the selected provider preset.
- **Held runs recover after a usage limit.** Eligible runs resume or dismiss when the limit resets. A ready fallback can run while the primary is limited, and a fallback startup failure waits for the reset.
- **The server gets CPU priority on shared Docker hosts.** The server, HTTPS front door and tunnel get a higher CPU weight than the runner. Under severe memory pressure, the runner is preferred for termination.

### Fixed
- **An explicitly cleared login stays cleared.** A stale Settings page cannot switch to a subscription after Clear key or Sign out. An unconfirmed sign-out keeps automatic key login blocked.
- **Renewed worktrees survive retired pull-request cleanup.** Reused worktrees and new trees for Doing tasks are kept when older work is removed or merged.
- **Separate model messages keep their paragraphs.** Muse, Grok, Cursor, Gemini and Antigravity replies preserve boundaries between messages instead of joining their words together.
- **Company-file import accepts all the goals setup suggests.** The goal limit rises from 20 to 325; the 300-seat limit is unchanged.
- First-run and sign-in documentation follows the current wizard.
- Prerelease candidates use their matching release notes. Product pull requests reject ordinary changelog edits, and nightly gate failures use a tasks-only service key.
- The runner state test compares table names with the declared schema instead of a stale count.

## [0.4.1] - 2026-10-23

### Changed
- **Each bot can keep up to 100 worktrees.** The per-bot worktree limit rises from 50 to 100. Adding or restoring a worktree still needs 5 GB free on the computer, and cleanup still waits until the bot is idle. The limit is separate from how many turns a computer runs at once ([Worktrees](docs/worktrees.md)).

### Fixed
- **The docs say when an automatic check skips an idle task.** An idle task unchanged since its owner's last settled checkpoint skips a plain automatic stall or status check. Wake behavior is unchanged: human messages still wake the bot, and running sessions keep their recovery checks ([Sessions](docs/sessions.md)).

## [0.4.0] - 2026-10-23

### Changed
- **Bots share their computer's sign-in.** Each AI tool has one login per computer. Per-bot named subscriptions are removed. Before upgrading, sign in the computer's login for each tool your bots use, and update its runner. To use another account, add another computer; it can run on the same machine. API clients must stop setting per-bot subscriptions and named-profile sign-ins ([Computers](docs/computers.md)).
- **Large sessions restart from a checkpoint.** By default, when a bot's last completed turn reads at least 400,000 input tokens, the next turn starts a fresh session. It uses the task's checkpoint and subsequent conversation instead of the old transcript. Update the runner to use this behavior ([Checkpoints and resume](docs/sessions.md#checkpoints-and-resume)).
- **The key scrubber catches more key-shaped text.** Setup chat, the company file and setup support tickets also remove 32 or more letters and digits in a row, however regular and whether alone or inside a longer token, as Tico HQ's copy does.

### Added
- **Choose a task model in Settings.** In **Settings > Bots**, click **Tasks** beside a bot's model to choose a harness, model and effort for its tasks. The bot editor also has a **Task model** row. Choose **Same as bot** to clear it. Chat keeps the bot's own model ([Task models](docs/harnesses.md#run-a-bots-tasks-on-another-model)).
- **Cheaper task models show a warning for sensitive work.** A bot with send, spend or publish approval history, or write access beyond its own repository, warns when its task model costs less than its own model from the same provider. You can still save and run it; the run note records the warning ([Warning rules](docs/harnesses.md#a-warning-about-a-cheaper-task-model)).
- **See your company's billing in Settings.** Owners and admins can open **Settings > More > Billing** to see balance, credits, daily and hourly usage, and monthly statements with CSV downloads. **Talk to us to add credit** opens the booking page. Billing needs a working Tico HQ connection; this page adds no card form.
- **Start a stopped hosted computer from Settings.** Owners and admins see their company's hosted computers in **Settings > Computers** and can start a stopped one. When Tico HQ refuses because the company is out of credit, the card keeps the refusal and links to the account page to add credit. This needs a working Tico HQ connection.
- **See computer load alongside bot runs.** Computer lists include recent CPU and memory readings, running turns and an overload flag. Run records keep start and end load snapshots, helping you investigate a slow turn ([Computer performance](docs/permissions.md#a-computers-performance)).
- **See why a run stopped or stalled.** `hub run list` shows each run's end reason, end time, last event and last heartbeat. An attempt with no heartbeat or event for five minutes shows as stalled in the run list and Health; it is not stopped. The runner keeps up to 64 KiB of scrubbed stderr per attempt for seven days, under the existing export rules ([Why a run stopped or stalled](docs/sessions.md#why-a-run-stopped-or-stalled)).
- **Every run names its purpose.** The SQL `turns` table and weekly run view carry a short purpose label, such as `review PR`, so you can group runs and tokens by what they were for ([What woke each run](docs/hub-sql.md#what-woke-each-run-and-what-it-did)).
- **Opt-in error reports for one install.** With a Sentry DSN and environment set, the server reports unexpected errors, scheduler failures and runner panics, tagged with the release and instance. Each report holds only the error class, Tico's own stack frames and the route template, never message, task or request text. `python -m backend.observability test` sends a harmless test error ([Server and runner errors](docs/observability.md#server-and-runner-errors)).

### Fixed
- **Stopped waiting turns stay stopped.** A turn stopped before it starts now settles. An expired lease or an old system notice no longer queues stopped or finished work again. Human follow-ups and explicit resumes still work ([Tasks](docs/tasks.md)).
- **Muse Code failures release the busy turn.** A terminal run result now settles the attempt even when the process stays open. Quota failures keep their usage hold and do not retry automatically ([Harnesses](docs/harnesses.md)).
- **SQL history reads avoid repeated full scans.** Scoped history lookups and recent-attempt counts use indexed reads while keeping the existing visibility rules and query limits ([Querying Tico with SQL](docs/hub-sql.md)).
- **Runner requests wait less behind database writes.** Server writers queue in order, and a lease renewal with most of its lease left no longer writes. Attempt input, renewals and events spend less time waiting on SQLite writer contention. Health warns when those requests exceed one second at p95 ([Health monitor](docs/health-monitor.md)).
- **Gateway models return sooner after fallback.** When another model finishes a rate-limited Tico-model turn, the bot tries Tico models again after a minute. Update the server and its computers to use the shorter wait ([Gateway status and retries](docs/ai-gateway.md#when-the-gateway-refuses-a-bots-run)).
- **Try now sends one queued run first.** After **Try now** on a usage limit, one queued run on the shared login goes first. A success resumes the bots waiting on it; another limit puts the hold back with its original end. A limit reported late by a turn started before the hold no longer blocks that run's success ([What happens when](docs/how-it-works.md#what-happens-when)).
- **Task updates point to the closing command.** The CLI no longer offers `closed` on `hub task update --status`. Trying it tells you to use `hub task close <id>`. MCP also directs requesters to `hub_task_close`; filtering a task list by `closed` still works ([Tasks](docs/tasks.md)).
- **Unchanged idle tasks skip redundant checks.** A status or stall check starts no turn when an idle task still matches its owner's checkpoint. Human messages, questions, approvals, dependency results and active-session recovery checks keep their existing paths ([Wake audit](docs/hub-sql.md)).
- **Self-filed tasks wait for their filing turn.** When a bot creates work for itself, the new task waits for that turn to finish, up to five minutes. Work the filing turn already finished or parked does not start again ([Task sessions](docs/how-it-works.md)).
- **Finished-task rechecks avoid repeated no-change replies.** A system-triggered recheck of a task already done or closed posts no reply when it made no changes. Human messages and required answers still get replies; withheld text stays on the run ([Reply audit](docs/hub-sql.md)).
- **Temporary model capacity keeps a short hold.** A provider's capacity refusal holds the login for two minutes without increasing the quota backoff. Real quota and billing limits keep their normal holds. Work that may have acted is kept for review rather than replayed ([Usage limits](docs/how-it-works.md)).

[Unreleased]: https://github.com/ticoteam/tico/compare/v0.4.15...HEAD
[0.4.15]: https://github.com/ticoteam/tico/compare/v0.4.14...v0.4.15
[0.4.14]: https://github.com/ticoteam/tico/compare/v0.4.13...v0.4.14
[0.4.13]: https://github.com/ticoteam/tico/compare/v0.4.12...v0.4.13
[0.4.12]: https://github.com/ticoteam/tico/compare/v0.4.11...v0.4.12
[0.4.11]: https://github.com/ticoteam/tico/compare/v0.4.10...v0.4.11
[0.4.10]: https://github.com/ticoteam/tico/compare/v0.4.9...v0.4.10
[0.4.9]: https://github.com/ticoteam/tico/compare/v0.4.8...v0.4.9
[0.4.8]: https://github.com/ticoteam/tico/compare/v0.4.7...v0.4.8
[0.4.7]: https://github.com/ticoteam/tico/compare/v0.4.6...v0.4.7
[0.4.6]: https://github.com/ticoteam/tico/compare/v0.4.5...v0.4.6
[0.4.5]: https://github.com/ticoteam/tico/compare/v0.4.4...v0.4.5
[0.4.4]: https://github.com/ticoteam/tico/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/ticoteam/tico/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/ticoteam/tico/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/ticoteam/tico/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/ticoteam/tico/compare/v0.3.75...v0.4.0

Older releases: [0.3](docs/history/CHANGELOG-0.3.md), [0.2](docs/history/CHANGELOG-0.2.md) and [0.1](docs/history/CHANGELOG-0.1.md).
