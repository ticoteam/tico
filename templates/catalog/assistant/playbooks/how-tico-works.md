# How Tico works, for queue questions

Read this when a person asks why something is on their queue, or how to make it stop.

## What puts an item on Needs you
- **A task the person owns.** Bots file these when they need real work from them. Most are routine output, not decisions.
- **An ask**: a bot or a stage asks the person a question or for an OK, on a task. A question is answered on its task:
  `tico task answer <id> <requirement> "<reply>" --version N`. An OK to send, spend or publish waits for the person's own tap
  on its preview.
- **A paused bot** the person can fix, such as a sign-in to reconnect. One item per cause.
- **An @mention** from a person, until they reply.

## Who files what, by default
- **Release Owner**: one OK to publish per release, and a release-notes task.
- **Technical Writer**: one "Docs for vX" task per release, from an automation. Releases can go out several times a day, so these
  pile up. The fix is the automation, not the tasks.
- **Health Monitor**: findings about bots and computers, including test pings. Repeated test pings are a source to stop.
- **BotOps**: support and health tasks, and questions about bot setup.
- **PR Reviewer**: a review task per pull request.
- **Task Manager**: tasks it created while routing work.
- **Routines and automations**: any task filed on a schedule or a trigger.

## Automations and watchers
A routine or automation has a trigger (a schedule, an event, a webhook, or a due date) and steps. A watcher files a task when
something matches. `tico origin show <task id>` names the automation, rule or run that made the item and how many siblings it
made this week.

## Org rules and Reach
An org rule is a standing instruction the bots follow, and it can stop a kind of task. `tico rules list` shows them. Reach is
what bots may put on a person: the people and bots near them by default. `tico team show` shows who reports to whom.

## OKs
An OK to send, spend or publish is the person's own tap on its preview. The Assistant never gives it; it may send one back
when they ask.

## Close the pile, then stop the source
The queue's size is the total of `tico needs-you show`: asks, paused bots and mentions count too, not only tasks. Clearing the existing items does not stop new ones. Find the source with `tico origin show`, then stop it: pause or edit its
automation, add a rule, or give BotOps a task when it is a bot's own code. Report counts before and after.
