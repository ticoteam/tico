# Escalation practice

Standing reference for how escalations are triaged, owned, updated and closed. It holds what is known about the practice and
where it comes from. The team's own triggers, severities and cadence live in `escalation-rules.md`. Where the two disagree,
the team's rules win and the disagreement is written in the case file.

## Severity is impact and scope
- Impact: what the customer can no longer do, or what it costs them: money charged, data lost or exposed, a deadline missed.
- Scope: one customer, a segment, or everyone. An outage for every customer is a major incident for the incident process
  (`incident-scribe`), not only an escalation.
- A common three-level scheme (SEV 1 critical, SEV 2 major, SEV 3 minor) puts a broad outage or data loss at the top, and a
  single-customer problem with a workaround in the middle. Use the team's labels; the scheme is a starting point.
- When unsure, choose the higher severity and say so. A human may lower it.

## Cadence is a team choice
The update cadence is set by the team in `escalation-rules.md`. No benchmark here is a target. One secondary guide suggests a
first update within 15 minutes at the highest severity. It is not an industry standard and is not used here as a rule.

## Case states
- open: an owner is named and the customer hears within the cadence.
- workaround: the customer can work, the cause is not fixed.
- fix in review: engineering has a change that is not yet live.
- fixed, awaiting confirmation: the change is live and the customer has not confirmed it.
- closed: the customer confirmed, or the case owner closed it with a reason on the timeline.

A case is not closed because engineering merged a change.

## Owner and clock
- One named human owns each case. A handoff does not reset the customer's clock: the next update date stays.
- A case waiting on engineering stays with the case owner. Engineering owns the fix; the case owner owns the customer's update.

## Waiting on the customer
A case waiting on the customer's export, a call or a decision is "waiting on customer", with the date asked. It keeps its
severity clock. It is shown as waiting until the date passes, and then goes to the top of the digest.

## After the case
Write one line: the cause, and what would have prevented it. Keep it blameless. Name the system or the step, not the person.
The practice follows the blameless postmortem approach described in the Google SRE book.

## Sources
- Atlassian incident management guidance on severity levels (SEV 1 critical, SEV 2 major, SEV 3 minor), read 2026-11-01.
  Atlassian publishes no customer-update cadence.
- A secondary SRE guide suggesting a 15-minute first update at the highest severity, read 2026-11-01. Not an industry
  standard; not used as a target.
- Beyer, Jones, Petoff and Murphy (eds.), *Site Reliability Engineering*, O'Reilly, 2016, on blameless postmortems. The
  chapter was recalled for this file, not re-read on 2026-11-01.
