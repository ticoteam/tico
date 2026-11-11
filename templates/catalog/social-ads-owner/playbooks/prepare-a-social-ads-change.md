# Prepare a social ads change

How to make the real thing for a social ads bet: a new campaign, new creative, an audience or creative test, or a
budget change inside an experiment. Used in Growth Experiments' Building stage (`playbooks/build-a-bet.md`, "Make the
real thing"), for a campaign request from a segment owner, and for a diagnosis ("why did cost per result jump?").
Budget 30 minutes.

Outcome: one change, made completely but not live, kept in this repository and attached to the task, ready for the
Go-live page. Nothing goes into an ad account in this playbook. It goes live only after a person OKs the Go-live page
(`playbooks/run-and-measure.md`), or as upkeep your channel playbook lists.

---

## 1. Read the request

    tico task show <id>

Find the segment, the goal, the budget, the date and who asked. If one of them is missing, ask once with
`tico task ask <id> --to <person> "<question>" --version N` and stop.

## 2. Check it against the targets

Read `knowledge/targets.md` and `knowledge/changes.md`. If a similar change was tried, say what happened. If the request
breaks a ceiling or a never-change rule, say so on the task and stop.

## 3. Build it

- **New campaign:** segment, objective, audience (prospecting or retargeting, with its exclusions), daily budget, the
  primary conversion and its target, and the landing page. Read the landing page. If it does not answer the ad's
  promise, say what to fix and route it to `cro-owner`.
- **New creative or test:** three variants, each testing one idea (the offer, the pain or the proof), each with its
  claims checked. Show each ad as it will look: image or video, headline, text and button. Write the test as
  `skills/read-an-audience-or-creative-test/SKILL.md` sets it out: the question, the count, and the earliest read date.
  Design work is a sub-task to the Design Owner (`design-owner`). A video goes to the Design Owner, who files it to the
  Video Owner; a person OKs the video's brief and cost before it is made.
- **Audience change:** from what to what, the audience size the platform estimates, and the reason with its numbers.
- **Budget change:** from which ad set to which, how much, within the step default in `knowledge/targets.md`, and the
  evidence from the last 28 days. A raise is a bet, never upkeep. A move across segments is the Growth Team Manager's
  decision (`growth-team-manager`): ask it on the task.
- **Diagnosis:** the numbers before and after, the factor that moved, the most likely cause, and the check that would
  confirm it (`skills/diagnose-a-rising-cost-per-result/SKILL.md`). A diagnosis that points to a fix is an idea, or
  upkeep if your channel playbook lists it.

A pause needs none of this: you may always pause. Name the ad or ad set and the spend it stops, and never pause the
only live ad in an ad set without a replacement ready unless a cutoff in Rules says to.

## 4. Check the claims

Before any new copy goes in, list each claim with its dated basis and its category, as the Claims section of
`knowledge/paid-social-method.md` sets out. A claim with no basis is removed or changed to what can be shown. The brand
check comes next, in `playbooks/build-a-bet.md`.

## 5. Hand it to the Go-live page

Attach the change to the task, and record it in `knowledge/changes.md` as "built". The Go-live page
(`playbooks/build-a-bet.md`) shows it as it will look, with its audience, budget and stop rule. For a campaign request,
tell the segment owner on their task, in under 150 words, what you built and that it waits on a person's OK.

Once it is live, record who put it live and when in `knowledge/changes.md`, and the date to read it two weeks later.
