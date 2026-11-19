# Hiring a person

Use only after the OK in step 10 of the skill. The role here is the small scorecard: the lines a bot cannot do.

Who does what: you prepare, schedule, chase and write up. A named **hiring owner** (a person; by default the owner)
makes every decision about a candidate and signs the offer. In Tico, the Recruiting seat, the applicant list and the
Hire action are a person's to use. Bots can read seats and applicants.

The legal facts on this page were checked on 2026-11-18, mostly from law-firm and vendor summaries. They change often.
Before a post goes out or an offer is made, re-read the rule for the place the job can be done, at the state or city
labour agency's own page, and write the date read in `knowledge/filling-roles.md`.

## 1. Fix the criteria before anything else

From the scorecard, write four to six **criteria**: the things a person must show to hit the outcomes. Each one is
observable ("has run a month-end close alone", "explains a technical fault to a non-technical customer in writing").

- Mark each as **required** or **nice to have**. A requirement is something needed in the first six months. If the
  team would hire a strong person without it, it is not a requirement.
- Do not use years of experience or a degree as a criterion unless a licence demands it. Write what the years were
  supposed to prove.
- The criteria are now frozen. The post, the screening, every interview question and the scorecards use these and
  nothing else. Changing them mid-search means starting the comparison again.

## 2. Pay range

Ask the Compensation Analyst bot if the team has one (skill `set-a-pay-range-for-a-job-post`); otherwise ask the hiring
owner for the band. Write the range with its unit (per year or per hour), currency, and what sits outside it (bonus,
equity, benefits). A single figure is not a range, and neither is a range so wide it says nothing.

Default: show the range in every post, everywhere. As of 2026-11-18, laws requiring a pay range in job posts were in
effect in roughly thirteen US states and the District of Columbia (among them California, Colorado, Connecticut,
Hawaii, Illinois, Maryland, Massachusetts, Minnesota, New Jersey, New York, Rhode Island, Vermont and Washington), with
more scheduled. Many apply to remote jobs that could be done from the state. Showing a range always is simpler than
tracking the list. Check the rule for each place the job can be done.

## 3. The job post

Under 400 words. A person skimming on a phone should know in ten seconds what the job is, what it pays and whether to
apply.

```text
<Role title a candidate would search for> · <location or "Remote, <countries/states>"> · <pay range, unit, currency>

What you will do
<The mission sentence from the scorecard, then the 3-5 outcomes in plain words. Say that bots do the rest of the role
and name what they do.>

What you need
<The required criteria only. 3-5 lines.>

Nice to have
<0-3 lines, or leave the section out.>

How we hire
<The steps and how long each takes: application, one work task (time needed, paid or not), interviews (how many, with
whom), decision by <date>.>

How to apply
<One way. What to send. Who to ask for an adjustment to the process.>

<Equal opportunity line. Any notice the law requires about automated tools.>
```

Checks before it leaves the team:

1. Pay range present, with unit and currency.
2. Every "need" is a required criterion from step 1.
3. No coded words that suggest an age, sex or background: "young", "digital native", "recent graduate", "rockstar",
   "native speaker" (write the language level needed), "able-bodied".
4. The process described is the process you will run.
5. Each job board's own posting rules were read this month, dated in the knowledge file.
6. If any automated tool will touch applications, the notice required where the candidate lives is in the post.

Publishing is public and leaves the team: it goes out on the OK from step 10 or its own OK, per the settings.

## 4. Where to look

Post where people with this work already are. If the team has a Sourcer bot, its skills cover search strings,
checking a person before outreach, and a three-message outreach with a stop rule. Record, per source, how many
applicants met every required criterion. Drop sources that produce none after two weeks.

Referrals from teammates go through the same steps as everyone else.

## 5. Read applications: evidence, never a score

For each applicant, write one summary:

| Criterion | Shown / partly shown / not shown | The line you relied on (quoted) |
|-----------|----------------------------------|----------------------------------|

Rules:

- Quote the application. No inference from a job title, an employer's name or a school.
- No total, no rank, no "strong" or "weak", no recommendation.
- Nothing about a protected characteristic, and nothing from which one could be read (photo, date of birth, marital
  status, a career gap's cause). Leave it out of the summary even when the application volunteers it.
- "Not shown" means the application does not say, not that the person cannot do it.
- Every applicant gets a summary in the same form. The hiring owner picks who moves forward.

Automated decision tools. Default: none. As of 2026-11-18: New York City Local Law 144 requires an annual bias audit
and notice to candidates before an automated employment decision tool is used; Illinois HB 3773 (in effect 1 January
2026) makes it a civil rights violation to use AI in ways that discriminate in hiring and requires notice; California's
Civil Rights Council rules on automated decision systems (in effect 1 October 2025) apply anti-discrimination law to
them and require four years of records; Colorado's AI law has been amended and delayed more than once, so read its
current status. An evidence summary that a person reads and decides from is the low-risk design. If the settings ever
allow scoring by a tool, a lawyer reviews the setup first.

## 6. The structured interview kit

Structured interviews are the best-supported single predictor of job performance in the research (Sackett and
colleagues, 2022). "Structured" means the three things below: same questions, same scale, scores before discussion.

**Questions.** Four to six, each tied to one criterion. Two kinds:

- *Past behaviour.* "Tell me about a time you <did the thing the criterion describes>." Follow-ups, the same for
  everyone: "What was your part?", "What happened?", "What would you do differently?"
- *Work task.* A short, real piece of the job, done live or beforehand, taking no more than 90 minutes. Same task and
  same time for every candidate. Say in the post whether it is paid.

No brain-teasers, no "where do you see yourself", and nothing about age, family or plans for one, health, religion,
origin, or arrest record. Do not ask what someone is paid now: many US states ban salary-history questions. Ask what
they expect instead. To check the right to work, ask only "Are you authorised to work in <country>?"

**One scale, four levels, for every question and every interviewer.**

| Level | Meaning |
|-------|---------|
| Poor | Did not show the criterion, or showed the opposite |
| Borderline | Showed part of it, or only with prompting |
| Solid | Showed it clearly, with a specific example and their own part in it |
| Outstanding | Showed it at a level that would raise the team's standard, with evidence |

For each question, write what a Poor, a Solid and an Outstanding answer sound like before the first interview. That
anchor is what makes two interviewers' scores comparable.

**Panel.** Each interviewer gets their own criteria, so nobody asks the same thing twice. Two to four interviewers is
enough. Every candidate meets the same panel in the same order where possible.

**Scheduling and candidate messages.** One message gives everything needed to attend or ask for a change: who, when
with time zone, how long, where or which link, what to prepare, how to ask for an adjustment. Never hint at the
outcome. The Recruiting Coordinator bot's skills cover slots and messages if the team has one.

## 7. Scorecards and the debrief pack

- Each interviewer writes a scorecard **alone, before talking to anyone**, within 24 hours: a level per criterion and
  the evidence heard for it. Chase a missing scorecard at 24 and 48 hours, then tell the hiring owner once.
- Build the debrief pack only when every scorecard is in. Lay the scores side by side per criterion, in the order
  submitted, with the evidence under each. Do not average and do not total.
- Mark every criterion where two interviewers are two levels apart. Those are the first items for the debrief.
- You add no view. The hiring owner decides: offer, no offer, or one more specific question to settle.

## 8. Decision, offer and close

1. The hiring owner records the decision and the reason against the criteria on the task.
2. **Offer.** Check the figure against the band (the Compensation Analyst's `check-an-offer-against-its-band`, if
   present). Draft the letter from the team's template: role, start date, pay, employment status, contingencies. A
   lawyer checks the template the first time and any change to it. A person signs and sends.
3. **Everyone else hears back.** Each candidate who interviewed gets a short, kind, plain message within two business
   days of the decision. No reasons that touch protected ground; no invented feedback.
4. **Records.** Keep applications, summaries, scorecards and the decision. US federal rules require hiring records to
   be kept at least one year; California requires four. Keep four years by default. Restrict who can read them.
5. **Background checks and right to work.** Only after a conditional offer, only if the hiring owner asks, and through
   a provider that handles the required disclosure and consent. In the US the employer completes Form I-9 within three
   business days of the start date. A person does this.
6. Close the search: mark the seat filled, take the post down, tell remaining applicants.

## 9. The first 90 days

Write check-ins at day 30, 60 and 90 from the scorecard outcomes (skill `write-a-30-60-90-check-in` if the team has the
HR Generalist bot). Each check-in reads the outcome numbers, names what is blocking the person, and records what the
team owes them. Performance concerns go to the person's manager, never into a bot's notes.

## What to check locally

- Pay-range and salary-history rules for each place the job can be done.
- Rules on automated tools in hiring.
- Whether the role is exempt from overtime (US: the Fair Labor Standards Act tests) and how it must be paid.
- Required notices and new-hire paperwork for the state.
- Outside the US: almost everything above differs. Use an employer-of-record service or local counsel.
