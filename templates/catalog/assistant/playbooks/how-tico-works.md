# How Tico works, for queue questions

Read this when a person asks why something is on their queue, or how to make it stop.

## What puts an item on Needs you
- **A task a bot filed for the person** (`owner` is the person). Bots file these when they need a decision, a review, or a
  missing input. Most are routine output, not decisions.
- **A bot's question** to the person. A question is answered in chat with `hub question answer`.
- **An approval** (merge, publish, send, spend). Approvals wait until the person names them.
- **A hold**: a task marked waiting on the person.

## Who files what, by default
- **Release Manager**: one publish approval per release, and a release-notes task.
- **Technical Writer**: one "Docs for vX" task per release, from an automation. Releases can go out several times a day, so these
  pile up. The fix is the automation, not the tasks.
- **Health Monitor**: findings about bots and computers, including test pings. Repeated test pings are a source to stop.
- **BotOps**: support and health tasks, and questions about bot setup.
- **PR Reviewer**: a review task per pull request.
- **Task Manager**: tasks it created while routing work.
- **Routines and automations**: any task filed on a schedule or a trigger.

## Automations and watchers
A routine or automation has a trigger (a schedule, an event, a webhook, or a due date) and steps. A watcher files a task when
something matches. `hub origin show <task id>` names the automation, rule or run that made the item and how many siblings it
made this week.

## Org rules and Reach
An org rule is a standing instruction the bots follow, and it can stop a kind of task. `hub rules list` shows them. Reach is
what bots may put on a person: the people and bots near them by default. `hub team show` shows who reports to whom.

## Approvals
A person decides approvals. The Assistant approves only on the person's plain yes, after a read-back, and only when the person
has turned on "My assistant can decide approvals for me".

## Close the pile, then stop the source
Clearing the existing items does not stop new ones. Find the source with `hub origin show`, then stop it: pause or edit its
automation, add a rule, or give BotOps a task when it is a bot's own code. Report counts before and after.
