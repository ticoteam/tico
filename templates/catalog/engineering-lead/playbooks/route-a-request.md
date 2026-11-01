# Route a request

Triggered when a task or message asks engineering for something and does not say who should do it, and for each
unowned or stuck item in the weekly summary. Budget 10 minutes. The outcome is one route, applied only as far as
the request and the Tools allow.

---

## Inputs

- The request: `hub task show <id>`.
- `knowledge/routing.md` (earlier decisions) and `knowledge/areas.md` (owners).
- Each candidate owner's open load: `hub task list --owner <person or bot> --status open --status doing`.

## Steps

1. **Read the request.** Restate it in one sentence: what is wanted, by whom, by when, and what the requester will do with it.
2. **Find the owner.**
   1. `knowledge/routing.md`: has a request like this been routed before? Follow that.
   2. `knowledge/areas.md`: which repository or area does it touch, and who owns it?
   3. The team's lines: a pull request needing a look is `pr-reviewer` (Senior Software Engineer); a release, its
      checklist or its notes is `release-notes` (Release Manager); an outage or an on-call handoff is
      `incident-scribe` (Site Reliability Engineer); an outdated README or API page is `docs-writer` (Technical
      Writer); an incoming bug or a test plan is `issue-triage` (QA Engineer); a vulnerable dependency or a leaked
      credential is `security-engineer`; a red or slow pipeline is `devops-engineer`; a design doc or an
      architecture decision is `software-architect`; a developer's public question or a sample app is
      `developer-advocate`. What users need, a spec or a roadmap question belongs to Product: route it to
      `product-lead` (Head of Product).
   If two owners fit equally, name both and say what decides it. If none fits, say so; never force a fit.
3. **Check load and urgency.** Note how many open tasks the owner has and whether anything on it is older than a
   week. Say it as a fact, never as a judgement of the person. The team's starting limit is about four active items
   per owner: a team choice, not a benchmark. Above it, say so in the proposal.
4. **Write the proposal** on the task, under 120 words: **Route to** <owner>, **why** (the area and any earlier
   decision), **urgency** and the date it matters, **what they need** (links), and an **alternative** owner. Ask with
   `hub task ask <id>` only if the owner or scope is unclear. Otherwise continue.
5. **Carry out the requested work** only when the task asks for it and a Tool allows it:
   `hub task create --owner <slug> --title "<the ask>" --parent <id>` with the links. On a no or a change, write the
   correction into `knowledge/routing.md` as a present-tense rule, dated. Commit, then
   `hub task update <id> --status done --note` with the owner chosen or the question still open.

## Output

A proposal on the task in the shape of step 4, and the child task when the task asks for one. An unowned area is
recorded as "no owner for <area>" in the next summary.

## Done when

- One owner is named, or the ambiguity is named with both options.
- The load fact is stated, and any item older than a week is flagged.
- Any correction is written to `knowledge/routing.md`.

## When you cannot tell

An area with no owner is the finding. Put it in the summary as "no owner for <area>" and ask the requester, not a guess.
