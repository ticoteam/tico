# Plan delivery

Triggered by a request for an outcome that spans one or more repositories, or by a human asking for a plan.
Budget 60 minutes. The outcome is a parent task with acceptance checks, and one child task per repository or area,
each with an engineer who has access. Nothing is sent outside the team.

---

## Inputs

- The requested outcome: `hub task show <id>` and its conversation.
- `knowledge/delivery.md`: the repositories, engineer owners and checks already agreed.
- `hub team show`: the engineer bots. Each bot's repository access comes from `hub bot repos <slug>`, not from the team view.

## Steps

1. **Restate the outcome** in one sentence, with who asked and the date it matters, if one was given.
2. **Write the acceptance checks**, as in `skills/write-a-delivery-plan/SKILL.md`.
3. **Map the repositories and areas**, each with its owner from `knowledge/delivery.md`. An area with no owner is a question, not a guess.
4. **Create the parent task** with the outcome and its checks. It stays open until every required child is done.
5. **Split it.** Create one child per repository or area with `hub task child`, carrying the repository, the expected
   behaviour, the dependencies and the checks.
6. **Assign each child** to an engineer bot with write access to its repository, checked with `hub bot repos <slug>`. If none has it, say so on the parent.
7. **Order the dependencies.** Name any release step the children need as its own item on the parent.
8. **Finish.** Write the plan as a comment on the parent with `hub task comment`, then `hub task update <id> --status done --note` for the request task, never the parent, which stays open until its children are done.

## Output

The parent task (outcome, acceptance checks, state), its children (repository or area, owner, expected behaviour,
dependencies, checks), the release steps, and a list of open decisions.

## Done when

- Every child has a repository, an owner with access, an expected behaviour and at least one check.
- Every acceptance check can be run or observed.
- Every dependency is ordered, and every area without an owner is a named question.
