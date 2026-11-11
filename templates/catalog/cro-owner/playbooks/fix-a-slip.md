# Fix a slip

Your task is in Growth Monitoring's Slipping stage: a kept thing's number left its range. Budget: one run, two at most.

Outcome: either it is fixed and back in Watching, or it is retired (closed with the reason) and an idea is filed to
replace it.

The same text is shipped to every channel Owner. What you may fix by yourself is in `knowledge/channel-playbook.md`
("Upkeep you may do by yourself").

---

## 1. Find why

    tico task show <id>

Read the number, the range and the source in the comment that moved it here. Then find the cause: something changed on
our side (a page edited, a flow broken, a budget cut), on the outside (a competitor, a season, a platform change), or the
range was wrong.

## 2. Fix it, if your playbook allows

A fix that puts the kept thing back the way a person OK'd it is upkeep: do it within your granted Tools, or file the
exact change for the person your playbook names and wait for it:

    tico task file "Apply: <the fix>" --parent <id> --owner <person id> --body "<exactly what to change, where, and why>"

Then check the number is back in range, or that the cause is gone, and move the task back:

    tico task move <id> Watching -m "<what was wrong and what fixed it>" --version N

A fix that changes what was OK'd (new copy, a new audience, more spend) is not upkeep. It is an idea. Never say done
on a Slipping task: done ends the watch. A kept thing either goes back to Watching or is retired.

## 3. Or retire it

When it cannot be fixed, or a better bet should take its place, file the idea first:

    tico task create --owner me --pipeline growth-experiments --label <your channel tag> --title "<what to try instead>" --body "<why, in one line>. Replaces <this task>."

Then close this task with the reason: replaced by a better bet, or stopped.

    tico task close <id> -m "Retired: <replaced by <idea> | stopped, because ...>" --version N

If the range itself was wrong, say so, set a new range on the task with its reason, and move it back to Watching.
