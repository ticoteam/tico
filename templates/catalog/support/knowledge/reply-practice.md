# Reply practice

The standing rules for who decides what, and what a draft may say. The team's own answers (its policies, its names,
its hours) belong in `escalation.md`, `voice.md` and the docs the Librarian answers from, not here. This file
holds what does not change from team to team.

## Who decides each kind of ask

| Ask | Who decides | What the draft may say |
|---|---|---|
| How to do something the docs cover | nobody: the Librarian's cited answer | the answer, in the customer's terms, with the next step |
| A question the docs do not cover | the doc's owner (a task to the Librarian) | what is known, and a marked gap for the rest |
| Refund, credit or discount | the person named for money in `escalation.md` | the policy the docs state, and a gap for the amount |
| A date, or that a fix is coming | the owner of the fix | "under review" and the next touch date, never a date |
| Outage or service down | the person named in `escalation.md`, at once | a holding reply: what we know, what we are doing, when we will update |
| Security report or data exposure | the security owner, at once | no detail in any reply until a person decides the text |
| Legal, safety or a threat of action | the person named in `escalation.md`, at once | no reply until that person decides |
| Cancellation or downgrade | the retention owner, if the team has one | the facts and the options in the docs; no offer |
| Feature request | the product owner | "passed on", with the link to any existing request; no promise |
| Bug that reproduces | whoever fixes bugs (`technical-support` or engineering) | "we are checking", with the ticket reference |
| A known issue that repeats | the owner named in `known-issues.md` | the same answer as the last reply, with the status it has today |
| An angry or very upset customer | the person named in `escalation.md` | a short, direct acknowledgement; a person writes the substance |

## Follow-up defaults

- Waiting on a customer: a first nudge after 3 days, a second 7 days after the first, then closed quiet with the date
  and the reason. A nudge adds something new and asks one question.
- Waiting on a colleague: ask once on the task, with the one question. Ask again only when the answer is due.
- Waiting on a fix: keep the ticket open. Name the fix's status in its line. Tell the customer when it ships.

## What a draft never does

- It never promises money, a date, a fix or an outcome that a cited doc or a person has not stated.
- It never names a person who has not agreed to be named.
- It never quotes an internal ticket number, a task id, a log line or a token.
- It never says "no one else has this problem" or "this is a known bug" without a count you read.

## Outside practice

- One AI reply vendor advises that a person approve every AI-drafted reply for the first two to four weeks, and flag
  any reply that cites no source. That is one vendor's advice, not a standard. This template keeps every draft
  unsent until a person decides, which is stricter.
- Zendesk publishes a stale-ticket recipe that reminds the customer after 4 and 6 days in Pending, then sets the
  ticket to solved at 7 days. It is an example, not a default. The windows in `playbooks/onboarding.md` are the team's
  own choice and stay that way until the team changes them.

## Sources

- 2026-11-01: the rules above are the team-neutral form of `playbooks/work-a-ticket.md` and `playbooks/onboarding.md`.
  The nudge defaults are this template's defaults, to be changed by the team's answer in setup.
- 2026-11-01: Robylon, "Preventing AI hallucinations in support" (vendor blog, 2026; search excerpt read, not the full
  page): approve every AI reply for two to four weeks; flag replies with no cited source.
- 2026-11-01: Zendesk help centre, pending-reminder recipe, "Bump-Bump-Solve" (undated; read in full): an example
  recipe with reminders at 4 and 6 days and solve at 7 days.
