# Route a request

Triggered by a task or message that names a lead, an account, a campaign or a request and asks who should take it. Budget 10 minutes. The outcome is a routing proposal on the task that a human can use in one pass, applied within the requested work and granted Tools.

Inputs: the task, `knowledge/routing.md`, `knowledge/team.md`, `knowledge/calendar.md`, and the owner's open tasks.
Done when: the proposal is on the task in under 100 words; if the task asked for routing and a granted Tool allows it, the owner's task exists and is linked to this one; any rule the request taught is in `knowledge/routing.md`.

---

## 1. Read the request

    tico task show <id>

Find what it is, the deadline and who wants it. For a lead or account, check it is not already a customer: `tico meeting search "<account>"` and the pipeline source in `knowledge/pipeline-rules.md`. If the ask, the audience or the date is missing, ask the requester once with `tico task ask <id> --to <person> "<question>" --version N` and stop.

## 2. Match it to an owner

Use `knowledge/routing.md`, then the growth team's lines in `AGENTS.md`. A lead, inbound request or target account goes to the sales Owners first; a segment's growth to its segment marketing owner; a channel problem to its channel Owner; a stopped task in one of your pipelines is yours to decide (`playbooks/decide-a-stuck-task.md`). Otherwise use the marketing and sales roles named in `AGENTS.md`. If two owners could take it, say why one fits better. If none fits and the same kind of request keeps coming, propose a hire (`playbooks/propose-a-hire.md`) instead of routing it to a human again.

## 3. Check the load and the calendar

Read `knowledge/calendar.md` and the owner's open tasks. If the deadline collides with something already planned, say what would slip.

## 4. Write the proposal

On the task, in under 100 words: owner, one-line brief, deadline, what it displaces if anything, and the one thing the owner needs from the requester. Anything that leaves the team, changes a live page or changes the CRM is marked "draft until a person turns mail sending on in Tico" or "draft until a person approves the change".

## 5. Finish

When the task asks for routing and a granted Tool allows it, run `tico task create --owner <slug> --title "<what and for whom>" --body "<the request, the account, the source>" --parent <id>`, and log the routing in `knowledge/routing.md` if it teaches a rule. On a no, record the correction as a rule so the next proposal is right (`skills/review-routing-corrections/SKILL.md`). Then `tico task done <id> -m "..." --version N`. A no or a change is recorded and nothing is created.
