# Route a request

Triggered by a task or message asking marketing for something ("we need a case study", "can we
announce this?"). Budget 10 minutes. The outcome is a routing proposal on the task that a human can
use in one pass. Route requested work by creating a task for the matching owner; do not take over their specialist work.

---

## 1. Read the request

    hub task show <id>

Find the ask, the deadline and who wants it. If the ask, the audience or the date is missing, ask
the requester once with `hub task ask <id>` and stop.

## 2. Match it to an owner

Use `knowledge/routing.md`, then the team's lines in `AGENT.md`: a post or article (`content`), a
search or AI-visibility question (`seo-visibility`), a social post or what people say publicly
(`listening`), a campaign email (`email-marketing`), a launch or positioning (`product-marketing`),
competitor facts (`hub market report`, which the Librarian curates), reviews (`reputation`), ads (`paid-media`), an event (`events`),
press (`pr`), the community (`community`), brand and voice (`brand`), tracking and lead handoff
(`marketing-ops`). If two owners could take it, say why one fits better. If none fits and the same
kind of request keeps coming, propose a hire (`AGENT.md`, Hiring) instead of routing it to a human
again.

## 3. Check the load and the calendar

Read `knowledge/calendar.md` and the owner's open tasks. If the deadline collides with something
already planned, say what would slip.

## 4. Write the proposal

On the task, in under 100 words: owner, one-line brief, deadline, what it displaces if anything,
and the one thing the owner needs from the requester. Anything that leaves the team or changes a
live page is marked "draft until a person turns mail sending on in Tico".

## 5. Finish

For requested routing, run `hub task create --owner <slug>` with the brief, link it to this
task, and log the routing in `knowledge/routing.md` if it teaches a rule. Then
`hub task update <id> --status done --note`. A no or a change is recorded and nothing is created.
