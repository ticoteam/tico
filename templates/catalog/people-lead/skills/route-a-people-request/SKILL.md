---
name: route-a-people-request
description: Route one people request to the owner who holds that work, or to the named human when no bot holds it, with one line of reason and no decision made for anyone.
---
# Route a people request

When to use: when a people request arrives on a task or in the weekly summary and nobody has clearly been given it.

Steps:
1. Read what the request asks, not who sent it. Name the kind of work: hiring a role, finding people who have not applied,
   interview logistics, onboarding or handbook questions, offboarding and records, review cycles, benefits, pay bands or offer
   checks, surveys and recognition, or training. Use the route list in `AGENT.md` ("The people team's lines").
2. Check the owner in `hub team show` now, and read `knowledge/routing.md`. If no bot holds the work, the request goes to the
   human named in `knowledge/team.md`.
3. Check whether the request decides something about a named person: a hire, a rating, a pay change, discipline or a
   termination. If it does, do not route it to a bot. Send it untouched to the named human at once, with one line saying why.
4. Write one line: the owner, the reason, and the task link. Create the task for the owner
   (`hub task create --owner <bot> --title "..." --body "..."`) only when the request asks for it and a granted Tool allows it.
   Otherwise put the line on the task as a proposal.
5. If the route is new or has changed, record it in `knowledge/routing.md`.
6. If the same unowned kind of work comes up three or more times in a month, it is a hiring proposal. Follow
   `playbooks/propose-a-new-bot.md`. Create nothing yourself.

Checks before you finish:
- The request has one owner, or a named human where no bot holds the work.
- No bot was created, and no work was assigned to a human on your own.
- No decision about a named person went to a bot.
- The route is in `knowledge/routing.md` if it is new.

Example (generic, invented): A request asks for help with benefits enrollment before the window opens. `benefits` is in
`hub team show`, so the line reads "Owner: benefits (enrollment deadlines), reason: enrollment is its work, task 412 linked."
