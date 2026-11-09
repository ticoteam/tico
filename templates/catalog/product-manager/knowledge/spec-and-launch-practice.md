# Spec and launch practice

The standing method behind `playbooks/write-a-spec.md`, `playbooks/weekly-spec-review.md` and the three skills.
It holds what is known about specs and launches, so each run does not re-derive it. Every figure carries its
source and the year it was read. Check a figure at its source before you quote it in a spec or a review.

## Appetite, not estimate

- Basecamp's Shape Up (online edition, read 2026-11-01) sets a time budget before any design. The book's
  glossary: appetite is "the amount of time we want to spend on a project, as opposed to an estimate". An
  estimate "starts with a design and ends with a number"; an appetite "starts with a number and ends with a design".
- Small batch: a project a designer and one or two programmers can build in one or two weeks. Big batch: a project
  that fills a six-week cycle. The book's cycle is six weeks, with a two-week cooldown between cycles.
- Fixed time, variable scope: the time limit stays fixed and the scope adjusts to fit it.
- Shape Up's terms: a pitch is "a document that presents a shaped project idea for consideration"; a rabbit hole is
  "part of a project that is too unknown, complex, or open-ended to bet on". The book's contents name a pitch's
  ingredients as problem, appetite, solution, rabbit holes and no-gos. The pitch chapter was not read for this file.

## Launch readiness

- Google's Site Reliability Engineering book, chapter 27, "Reliable Product Launches at Scale", and Appendix E, the
  Launch Coordination Checklist (read 2026-11-01; the site page carries a 2017 copyright). The checklist's named
  sections are: architecture and dependencies; integration (DNS, load balancers, monitoring); capacity planning;
  failure modes; client behaviour; processes and automation; development process; external dependencies; and rollout
  planning with named owners and contingency. Rollback is not a named section; the chapter treats it through
  canaries and reversible flags.
- The chapter's rollout rule: a canary expands only after observation, and a change that does not pass its validation
  period is rolled back automatically. Feature flags are expanded gradually and can each be reverted at once.

## Acceptance criteria

- Given/When/Then with one behaviour per criterion is the common form (team practice, no single source).
- A criterion states the result a user sees. Work that ships a foundation (a table, a flag, an endpoint) is not
  acceptance of the user outcome; the criterion stays open until a user can do the thing.
- Tag each criterion before release or after launch. Some results are visible only with real data or real people.

## When to write a spec

| Situation | Working rule (team practice, not from a published source) |
|---|---|
| The problem is clear, the fix is small and local, and nobody disagrees on what done means | A ticket, no spec |
| The problem is clear and the solution is open, or the change touches data, money or several teams | A one-page spec with an appetite |
| Nobody has chosen the problem yet | No spec. Ask the Head of Product who decided |

## Sources

- Basecamp, Shape Up online edition: the shaping chapter and glossary (basecamp.com/shapeup), read 2026-11-01.
- Google, Site Reliability Engineering, chapter 27 and Appendix E (sre.google/sre-book), read 2026-11-01.
- Acceptance criteria and the working rule above: team practice, not a published standard.
