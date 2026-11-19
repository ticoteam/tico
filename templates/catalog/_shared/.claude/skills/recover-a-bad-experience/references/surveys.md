# Surveys done right

A survey exists to find the customers the team let down and to find the causes. The score is a by-product. A team
that chases the score ends up with a nice number and the same problems.

## Three kinds, three jobs

| Survey | The question | When to send | What it is for |
|---|---|---|---|
| Satisfaction (CSAT) | "How satisfied were you with <this specific thing>?" on a 1 to 5 scale | Right after one interaction closes: a support conversation, a delivery, onboarding | Finding individual bad interactions fast |
| Effort (CES) | "How easy was it to <get this done>?" on a 1 to 7 scale | Right after a task the customer had to complete: a return, a setup, a cancellation | Finding steps that make customers work too hard |
| Relationship (NPS) | "How likely are you to recommend <team> to a friend or colleague?" 0 to 10 | On a schedule, not after an event; at most twice a year per customer | Tracking the whole relationship and finding quiet unhappiness |

Start with one. Default: satisfaction after support conversations. Add the relationship survey once there are enough
customers to get 20 or more responses a round.

How NPS is worked out: the share answering 9 or 10 minus the share answering 0 to 6. Sevens and eights count in the
base and in neither group.

## Rules for asking

- **One question plus one open box** ("What is the main reason for your score?"). Every extra question lowers the
  response rate and adds little.
- **Send from a mailbox a person can reply to.** A reply to the survey email is a response; read it.
- **Not during a recovery.** Check the recovery log and open tickets first. Survey after the follow-up in step 9
  confirms the fix held, or not at all.
- **Not too often.** No customer gets more than one survey in 30 days of any kind, and no more than two relationship
  surveys a year.
- **No reward for answering, no reward for a score, no asking for a high score,** and no telling the customer a
  teammate's pay depends on it.
- **No filtering who is asked.** Send to everyone who meets the rule, or to a random sample. Surveying only happy
  customers makes the number meaningless. The same applies to asking for public reviews: use
  `check-a-review-request-for-compliance` before any review request.
- **An unsubscribe is honoured** like any other email. Survey emails follow the team's sending policy and the lint.

## Closing the loop on a low score

A low score is CSAT 1 or 2, an effort score of 1 to 3, or NPS 0 to 6.

1. **Within 24 hours,** a personal reply. Bain's writing on the Net Promoter System describes leading companies
   contacting every customer who gives a negative rating within 24 hours; this library takes that as the default
   (see `references/sources.md`).
2. Read their record and their comment first. Then start at step 2 of SKILL.md. If the comment names a failure, this
   is a recovery. If there is no comment, ask one question: what went wrong.
3. Do not argue with the score, and do not ask them to change it.
4. Log it in `knowledge/recovery-log.md` with the survey as the source.
5. If they do not reply to your follow-up, leave it. One message.

A middling score with a comment that names a problem gets the same treatment. A high score with a comment gets a
two-line thank-you and, if they offered an idea, where it went.

## The outer loop

The inner loop fixes it for one customer. The outer loop fixes it for all of them.

- Every comment goes, word for word and with the score, to the owner of what it is about (product, billing,
  delivery, support). Use `tag-one-feedback-item` and `hand-off-feedback-evidence`.
- Weekly, group comments by cause and count them. The top three causes go on the recovery page with tasks.
- When a cause is fixed, tell the customers who raised it. That is the cheapest goodwill there is.

## Reporting a score honestly

Always show, together:

- the score;
- the number of responses and the response rate;
- the date range;
- who was asked (everyone, a sample, one segment).

And:

- Under 20 responses: show the counts for each answer and the comments. No percentage, no NPS figure.
- Compare a score only with the team's own earlier score, asked the same way. Published industry benchmarks use
  different questions, samples and timings; do not quote one as a target.
- A change smaller than the swing you would expect from the sample size is not a change. With about 100 responses, a
  move of a few points means nothing. Say "no clear change".
- Never report a score without the low-score follow-up numbers beside it: how many low scores came in and how many
  were answered within 24 hours.
- A score is never a teammate's or a bot's target. Time to answer low scores and causes removed are fair targets.

## Setup checklist

1. Choose the survey and the trigger; save them in `knowledge/recovery.md`.
2. Check which Tool sends it (help desk, email Tool, survey Tool) with `tico tool list`. If none can, send a plain
   email with the question and number links or ask for a one-digit reply; draft it with `tico email draft` and lint it.
3. Write the exclusion rule: open recovery, surveyed in the last 30 days, unsubscribed.
4. Decide where responses land and confirm you can read them. A survey whose answers nobody reads is worse than none.
5. Send to a small first group, read every response yourself, then widen.
6. Put the figures on the weekly recovery page.
