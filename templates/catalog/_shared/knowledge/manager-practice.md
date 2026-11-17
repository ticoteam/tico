# Manager practice

The standing method for managing your team's pipelines. Each one's stages, owners and requirements are in
`knowledge/pipeline.md`; this file says how to act on them.

## How a task moves without you
- A task always starts in its pipeline's first stage, and its owner becomes the stage's owner.
- {{app_name}} moves a task to the next stage the moment every requirement is met and the next stage has room.
- When a bot owner's turn ends with nothing changed, it is woken again after 2 minutes, 15 minutes and 50 minutes. After three
  tries with no change, you get the task. After your three tries with no change, a person gets it.
- A task is Late once it has been in its stage longer than the stage's expected time. Snoozed time does not count. At
  the expected time you get it once; at three times that, a person gets it once.
- A task owned by a person is never chased. It shows Late only after its due date.

## What counts as a change
A move, a requirement changing state, a new commit, a new page version, an ask, a snooze, a sub-task filed. A comment
alone does not count.

## Snooze
- A snooze ends on its date, or when a person brings it back; the owner is then woken once.
- While snoozed: no wakes, no Late, no slot held in a stage with a limit.
- A bot's snooze with no end date is "On ice", and you are told once. After three snoozes by a bot in one stage, the next
  comes to you. Give every snooze a date.

## Moves you may make
- Reassign: for this stage only; it resets the tries.
- Move back: to any earlier stage, never refused. Moving back undoes the stage's on-enter work.
- Move on (push past): with a reason; refused for a requirement whose push-past switch is off (a merged pull request, a
  release) and for a people-only OK when a bot tries.
- Snooze, to a date.
- Close: never refused; its open sub-tasks close with it. A task a person asked for is theirs to close.

## Sources
- Tico docs/pipelines-v3.md: the never-stuck check, Late, Snooze, the moves, and C1, C4, C5, C6, C8, C24 and C25.
