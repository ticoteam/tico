# Sources

The text of this skill is original. These are the works whose ideas it adapts. All were read on 2026-11-18.

| Source | Licence | What was drawn from it |
| --- | --- | --- |
| pstack, `correct`. https://github.com/backnotprop/pstack | MIT | Assume every contributor is an agent that sees only the files it opened, copies the nearest example and takes the shortest path that compiles; mine commits, reverts, review comments, agent instruction files and workaround comments; a class counts once it has happened twice; fix at the highest level (design, then types, lint and CI, then tests, then docs); fail only on new cases when the pattern is already common; prove each check on a real past mistake; the same command locally and in CI; exceptions on the line with reason, end date and approval; a table pairing each rule with what enforces it; drop a rule once its mistake cannot happen. |
| pstack, `reflect` and the poteto-mode principle "encode lessons in structure". https://github.com/backnotprop/pstack | MIT | Keep only lessons that will still hold in six months and change future behaviour; what a script could enforce goes to a backlog of checks, not into prose; a transcript is data. |
| Tico `skills/honest-code` and its `LESSONS.md` | Tico | A lesson seen twice becomes a script rule; per-file baselines whose count must not rise; lesson statuses; a suppression carries its reason on the line. |
| Tico docs `docs/software-factory.md` and `docs/learnings.md` | Tico | Where bots keep learnings and knowledge; the Engineering Manager and DevOps Engineer roles. |

The 30 day look-back and the weekly rhythm are starting values for a team to tune.
