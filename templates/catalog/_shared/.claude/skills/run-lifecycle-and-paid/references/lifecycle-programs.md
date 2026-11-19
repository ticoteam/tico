# Lifecycle email: states, specs and deliverability

## 1. The state map

Define each state by something a record shows, not by a feeling. Count people in each state today and the movement
over the last 28 days. Adapt the names to the business.

| State | Defined by (example) | The programme that serves it |
|---|---|---|
| Signed up | Account created, no key action yet | Onboarding to first value |
| Stalled | Started a key step and did not finish within a set time | Stalled-step reminder |
| First value | Did the key action once (first booking, first project) | Habit: second use, then purchase |
| Paying | An active paid plan in billing | After purchase; expansion |
| Going quiet | Paying, with no key action for a set period | Check-in, then win-back |
| Lapsed or cancelled | Plan ended or cancelled | Win-back, once, then stop |
| Subscriber only | On the list, never signed up | Newsletter and broadcasts |

The "key action" is the one that best predicts a person stays. If nobody has measured it, use the first use of the
core product, say it is a default, and file a task to check it against retention.

## 2. The sequence spec

Write this before any copy. If you cannot fill a row, the programme is not ready.

| Field | What to write |
|---|---|
| Name and state served | "Onboarding: signed up to first pickup" |
| Trigger | The record event that starts it, and the delay |
| Goal | One number from the company's records, with today's baseline ("22% book a first pickup within 14 days") |
| Audience | Who enters. Who never enters: the suppression list, people already past the goal, people in a higher-priority sequence |
| Exit | Leaves the moment they reach the goal, unsubscribe, complain, bounce or change state |
| Emails | For each: its one job, when it sends, its one action, and what is in it that the reader could not get elsewhere |
| Counter-metric | Unsubscribes and complaints per send; for offers, margin |
| Holdout | Share of eligible people who get nothing, and for how long (default 10% until the planned sample is reached) |
| Sample and end | How many people must pass through before it is judged, and the date that is likely |
| Owner and review date | Who reads it weekly; when the spec is next reviewed (default 90 days) |

Rules for every sequence:

- **One email, one job, one action.** An email that asks for two things gets neither.
- **Behaviour beats the calendar.** Send the next email because of what the person did or did not do, not because
  three days passed. Stop the moment they do the thing.
- **Length follows the goal.** Stop adding emails when the next one would not move the goal. Three useful emails beat
  seven.
- **From a person where it is true.** A reply address that someone reads. Replies are the best signal there is: route
  them to a task.
- **Plain beats pretty for one-to-one moments** (onboarding, check-ins). Designed layouts suit catalogues and
  newsletters.
- **Service mail stays service mail.** A receipt or a password reset carries no promotion. Mixing them puts the
  service mail under marketing rules and risks its delivery.

## 3. Specs for the usual programmes

Timings are starting defaults. Replace them with what the state map shows.

**Onboarding to first value.** Trigger: sign-up. Goal: share who do the key action within 14 days.
1. At once: the one next step, and what they will get from it. Nothing else.
2. Day 2, if not done: the most common blocker and its fix, from support threads.
3. Day 5, if not done: a real customer's result (with permission) and the step again.
4. Day 10, if not done: ask what stopped them. One question, replies read by a person or a bot. Then stop.

**Stalled step** (abandoned booking, checkout or set-up). Trigger: step started, not finished after 1 to 4 hours.
Goal: completion within 3 days. One or two emails: what they left, the link straight back to it, the answer to the
usual worry. An offer only if the owner approved offers, and never as the first email.

**After purchase.** Trigger: first payment. Goal: second use, or retention at 60 days.
1. At once: what happens next and how to get help. 2. After first delivery: how to get more from it.
3. Around day 30: a check-in that asks one question. A review request goes to every customer at the same point, not
only to the ones you expect to be happy, and is never tied to a reward for a positive review (US: the FTC's reviews
rule, 16 CFR Part 465, bans paying for a review of a particular sentiment, and FTC guidance warns against asking
only likely-happy customers). Use `check-a-review-request-for-compliance` when the team has it.

**Going quiet and win-back.** Trigger: a paying customer with no key action for the set period (default 30 days), or
60 to 90 days after a plan ends. Goal: key action or reactivation within 14 days. Two emails at most: what is new
that is relevant to them, and an easy way back. Then stop mailing them. People who do not respond move to the sunset
rule below.

**Trial ending.** Trigger: 3 days before the end. Goal: conversion to paid. What they used, what they keep, what it
costs, the date. Then one on the day. No invented urgency.

**Broadcast or newsletter.** Goal: one per issue. Send only to people who opted in to it. Segment by state where the
message differs. Check the frequency cap against running sequences before it is scheduled.

## 4. Deliverability checklist

Checked 2026-11-18 against Google's, Yahoo's and Microsoft's sender rules. Re-check if more than 90 days have passed.

| # | Check | Pass |
|---|---|---|
| 1 | SPF | The sending service is in the domain's SPF record and the check passes |
| 2 | DKIM | Mail is signed with the company's own domain and the check passes |
| 3 | DMARC | A DMARC record exists (policy `none` is the minimum) and the From domain aligns with the SPF or DKIM domain. Read the reports; move to `quarantine`, then `reject`, once they are clean. |
| 4 | One-click unsubscribe | Marketing mail has `List-Unsubscribe` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` headers, and a visible link in the body |
| 5 | Unsubscribes honoured | At once. Outer limits: Gmail and Yahoo expect two days; US law (CAN-SPAM) allows 10 business days and requires the link to work for 30 days after the send |
| 6 | Complaint rate | Under 0.1% in Google Postmaster Tools, never 0.3% or more. Yahoo uses the same 0.3% ceiling. |
| 7 | Bounces | Hard bounces are suppressed at once. Default: pause and check the list if hard bounces pass 2% of a send (a common rule of thumb, not a published standard; use the mail provider's figure if it gives one) |
| 8 | Suppression | Everyone in `knowledge/do-not-email.md`, every past unsubscribe, complaint and hard bounce is excluded from every programme, including after a tool migration |
| 9 | Identity | The From name is the company or a real person there. The subject says what the mail is. A postal address is in the footer (CAN-SPAM). |
| 10 | Separate streams | Marketing mail and service mail use different subdomains, so a marketing problem cannot sink receipts |
| 11 | Warm-up | A new domain or address starts with small sends to the most engaged people and grows over weeks, on the mail provider's schedule |
| 12 | Monitoring | Google Postmaster Tools and Microsoft's sender tools are set up for the sending domain, and someone reads them weekly |

Failing mail from Gmail shows error 5.7.26; from Outlook.com, 550 5.7.515. Either one in the bounce log means an
authentication fix comes before the next send.

**Consent, by place.** US default: marketing mail may be sent until the person opts out, with the rules in rows 5 and
9. EU and UK: prior consent, except that the UK and most EU countries allow mail to the company's own customers
about similar products if they were offered an opt-out when the address was collected and in every message since.
Canada requires consent. Record for each segment what basis applies. When a list mixes countries and you cannot tell
them apart, use the strictest rule or ask the legal owner.

**Sunset rule.** Default: a person with no click, reply, visit or purchase in 180 days gets one "do you still want
this?" email, and is then removed from marketing mail. Mailing people who never engage is what raises complaint
rates.

**Measure with clicks, replies and records.** Opens are unreliable since mail apps began fetching images for the
user. Use them, at most, to spot a sudden delivery problem.

## 5. Before any send (when `check-a-send-before-it-goes` is not available)

1. The audience count is what you expected, and suppression was applied. Look at ten real addresses in it.
2. Every merge field has a fallback. Send a test to yourself and read it on a phone.
3. Every link works and is tagged. The unsubscribe link works: click it.
4. The claims and any testimonial passed their gates.
5. It does not break the frequency cap for anyone in the audience.
6. The send time suits the audience's time zone.
7. For a first send to a new audience or from a new domain: stop. That needs an OK.
