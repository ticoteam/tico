---
name: choose-how-a-bot-wakes
description: "Choose what starts a bot's work (a schedule or event automation, a due-date automation, a webhook, a watcher, or no trigger) before you give it a routine, so each run earns its model cost."
---
# Choose how a bot wakes

When to use: before you give a bot a routine, an automation or a watcher, or when a request says "every morning", "each week", "tell me when", "watch for" or "check the queue".

Skills propose changes to systems only when the task asks for them and a granted Tool allows it. Make each change as the requester (`playbooks/rights.md`).

Steps:
1. Name the trigger the request describes:
   - a time: a schedule, a five-field cron in a named time zone;
   - something Tico does: an event from `hub event-types` (for example `meeting.ready`);
   - something outside Tico that posts to Tico: a webhook, whose id `hub trigger list` shows;
   - a date that comes round: a due-date trigger, which starts its run a few days before each due day;
   - something outside Tico that only needs looking at: a watcher.
2. Ask whether the bot must think each time. If a plain look is enough (a new item, a changed status) and only a change
   needs judgment, use a watcher (`playbooks/set-up-a-watcher.md`). A watcher runs a program with no model, and it
   wakes the bot only when it files a task or adds a note.
3. If the bot must think on each occurrence, make an automation whose step is a Bot turn:
   `hub automation create --file -`, with `owner` set to `bot:<slug>`, the trigger from step 1, and a prompt that names
   the one playbook the run follows. `hub automation guide` has the flow format. A new automation is saved off unless
   the flow says `"enabled": true`; turn it on only when the requester asked for that.
4. Count the runs before you accept a schedule. A schedule every few minutes is a model run each time. Choose the
   longer interval, or a watcher, unless the request needs the speed. A routine earns its place after a human has read
   a week of the same work done by hand (the note in the template's `bot.yaml`).
5. Put the time zone in the trigger, not in the prompt. For a due-date trigger, `open_days` sets how many days before
   the due day the run starts.
6. Report what starts the bot, how often, what one run costs in model use, and whether it is on or off.

Checks before you finish:
- One trigger for each job. No second automation does the same work.
- Every schedule has a time zone, and its prompt names one playbook.
- A watcher prints only new things: its second run prints nothing (`playbooks/set-up-a-watcher.md`).
- Each change went through the requester's rights. A refusal is reported with who can make the change, not routed around.
- You read it back with `hub automation show <id>` before you report it (`skills/verify-a-change/SKILL.md`).

Example (invented): The request is "Tell me each Monday what the support desk closed last week." The trigger is a
schedule: Mondays at 08:00 in the team's zone. No watcher, because the answer needs one model run a week. The
automation belongs to the bot `support-desk-reporter`, its one Bot turn step follows that bot's weekly report playbook, and
it is saved off. The report says: "Scheduled for Mondays at 08:00. Off until you turn it on. Each run is one model run."
