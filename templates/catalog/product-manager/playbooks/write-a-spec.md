# Write a spec

Triggered by a task that names a problem the Head of Product or the owner has chosen. Budget 45 minutes
for a first version. The outcome is `specs/<slug>.md` that an engineer can estimate from, with every open
question assigned. Nothing is filed in GitHub.

---

## 1. Confirm it was chosen

    hub task show <id>

Check `hub goal list` and the Head of Product's decision log for the decision. If nobody decided it, say so on
the task and ask who did; a spec is not a way to decide by stealth. Pull the repository, and if a spec for this
problem already exists, extend it instead of starting a second file.

## 2. Gather the evidence

Read the latest `feedback-analyst` report for the theme and its count, the `product-researcher`
snapshots and opportunity map, `product-analyst`'s numbers for the area, and `hub meeting search
"<feature>"` for what customers and the team said. Quote sparingly, with source and date. Separate what was
observed from what was inferred, and mark each inference.

## 3. Set the appetite

Follow `skills/shape-a-spec-to-an-appetite/SKILL.md`. Write the appetite (team-weeks, a small or big batch)
before you draft the solution. If the problem needs more than a big batch, say so and stop there: it is two
problems, or a bet for a later cycle.

## 4. Write the core page

In the order of `knowledge/spec-format.md`:
- **Problem**: who, trying to do what, what it costs them today. Two or three sentences.
- **Evidence**: three to five lines, each with its source and count.
- **Appetite**: the time the problem is worth, and who set it.
- **Goal**: the goal in Tico it moves, and what we would expect to see if it works.
- **Scope** and **Non-goals**: bullets. Every non-goal is something someone might reasonably assume.
- **Acceptance criteria**: follow `skills/write-acceptance-criteria/SKILL.md`. Given/When/Then, one behaviour
  each, the edge cases the feature touches (empty, full, permission denied, offline, time zones), and a tag on
  each for check before release or check after launch.
- **Open questions**: each with the human who owes the answer and a date.
- **Risks**: what could make this the wrong bet, and the cheapest check.

## 5. Check it with the builders' eyes

Read it as the engineer: can each criterion be tested? Is anything ambiguous ("quickly", "simple")? Is
there a dependency on another team? Is every rabbit hole in the spec either fixed, cut or time-boxed? Fix those
before handing over.

## 6. Hand over

Publish with `hub file publish specs/<slug>.md --task <id>`. List the issues you would file (title and
body) on the task for a human, and the questions for the owners. `hub task update <id> --status done
--note`: the problem in one line, the appetite, the path, open questions and who owes them.
