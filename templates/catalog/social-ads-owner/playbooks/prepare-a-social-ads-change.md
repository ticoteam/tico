# Prepare a paid social change

Triggered by a task asking for a new campaign, a creative or audience test, a budget move, a pause, or a diagnosis ("why did cost per result jump?"), and by a change the weekly review proposes. Budget 30 minutes. The outcome is one change, prepared completely and waiting for review: no more than 150 words on the task. A change is applied only when the task asks for it and a granted Tool allows it.

---

## 1. Read the request

    hub task show <id>

Find the segment, the goal, the budget, the date and the requester. If one of them is missing, ask once with `hub task question "<question>" --on <person> --parent <id>` and stop.

## 2. Check it against the targets

Read `knowledge/targets.md` and `knowledge/changes.md`. If a similar change was tried, say what happened. If the request breaks a ceiling or a never-change rule, say so on the task and stop.

## 3. Build it

- **New campaign:** segment, objective, audience (prospecting or retargeting, with its exclusions), daily budget, the primary conversion and its target, the review date, and the landing page. Read the landing page. If it does not answer the ad's promise, say what to fix and route it to `cro-owner`.
- **New creative or test:** three variants, each testing one idea (the offer, the pain or the proof), each with its claims checked. Write the test as `skills/read-an-audience-or-creative-test/SKILL.md` sets it out: the question, the count, and the earliest read date.
- **Audience change:** from what to what, the audience size the platform estimates, and the reason with its numbers.
- **Budget move:** from which ad set to which, how much, within the step default in `knowledge/targets.md`, and the evidence from the last 28 days. A move across segments is the Head of Marketing's decision: ask it on the task.
- **Pause:** name the ad set or ad and the spend it stops. Never pause the only live ad in an ad set without a replacement ready.
- **Diagnosis:** the numbers before and after, the factor that moved, the most likely cause, and the check that would confirm it.

## 4. Check the claims

Before any new copy goes in, list each claim with its dated basis and its category, as the Claims section of `knowledge/paid-social-method.md` sets out. A claim with no basis is removed or changed to what can be shown. Record the approved copy and its sources in `knowledge/changes.md` before it is published.

## 5. Put it up for review

On the task, in under 150 words: the change, the money, the evidence, how it will be judged, and when. Name the person who reviews it: the requester for a campaign, or the owner for a routine change. Record it in `knowledge/changes.md` as "proposed".

## 6. Apply it when it is asked for

Only when the task asks for it and a granted Tool allows it: apply the change in the ad account, then record who applied it and when in `knowledge/changes.md`, and add the two-week check to the next review. Ad copy with a claim in it stays a draft on the task until it is applied under the same gate. Finish with `hub task update <id> --status done --note "..."`.
