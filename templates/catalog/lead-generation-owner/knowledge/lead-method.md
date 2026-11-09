# Lead method

The standing rules behind the weekly lead report, the lists and the hand-offs. `sources.md` holds the counts by source and
their tags. This file holds what counts as a lead, the rule that qualifies one, and how each hand-off is made, so each run
does not re-derive them.

## What counts as a lead

- A lead is a person or company with a dated sign of interest in the last 90 days: a demo or contact request, a form fill, a
  trial or sign-up, a reply to a sequence, a booked call, a referral, or a question sent to the team.
- A company that only matches the profile is a target, not a lead. Targets are counted on their own line and never enter
  the qualified count.
- Every lead carries one source tag. A lead with no tag is counted as "no source" and listed for the tracking owner. It is not
  qualified until it has a tag.

## The qualified-lead rule

A lead is qualified when every step below passes, in this order. The first step that fails is the reason it is not
qualified.

1. Source: one source tag.
2. Duplicate: no earlier lead for the same person or company is open or already counted this week.
3. Not blocked: not already a customer, not a competitor, not on the do-not-contact list.
4. Dated interest: a sign from the list above, dated within the last 90 days.
5. Fit: the segment, size, region and buyer role match the profile below, each from a dated public fact or a CRM field.

Until the profile below is written from the owner's answers, the report says "no profile yet" and counts nothing as qualified.

## Profile

Written at setup from the owner's answers, with its date: segment, size, region, buyer role, and one qualified and one
not-qualified example.

## Not-qualified reasons

One reason per lead, the first failing step: no source; duplicate; already a customer, competitor or blocked; no dated
interest; no match to profile.

## Counting

- The week runs Monday to Sunday. A lead counts in the week it arrived, not the week it was qualified.
- A rate is qualified divided by arrived, for the same source and window. The four-week average is the same rate over the
  last four full weeks.
- Cost per qualified lead only when spend per source is in an export. Otherwise it is "not measured".
- A source with fewer than ten arrivals is shown with its counts, but it is not rated.

## Hand-off rules

- Inbound: demo requests, contact forms, trial sign-ups, referrals, marketplace listings and event follow-ups go to
  `inbound-sales-owner`, picked up the same business day. The Growth Team Manager (`growth-team-manager`) routes every inbound lead there. These rules decide
  whether a lead counts as qualified, the score and reasons it carries, and its pick-up time. They never take an inbound lead
  off that queue.
- Outbound: replies to outbound sequences go to `outbound-sales-owner`, picked up within two business days.
- Every hand-off is a task that carries the source tag, the reason it qualified, the dated signal with its date, the profile
  match with its source, the pick-up time, and the CRM id or a link.
- A hand-off is never a message to the lead. The sales owner decides what to say and when.
- Marketing-ops keeps the tag convention and the measure of how fast each hand-off was picked up. Where these rules and its
  `knowledge/handoff.md` disagree, ask both in one task. Do not settle it alone.

## Sources

- 2026-11-06: rules written for setup from the routing rule in the Growth Team Manager (`growth-team-manager`) template (every inbound lead goes to the
  inbound owner) and the remit of the tracking owner's template (tags and hand-off delay). Those templates were read for their
  owned files and routing lines only. The team's own practice is to be confirmed at setup.
