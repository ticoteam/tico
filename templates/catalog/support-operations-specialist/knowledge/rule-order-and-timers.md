# Rule order and timers

How help desk rules, triggers, automations and SLA timers run, so an audit can say which rule caused a
misroute and why a timer read what it did. Each help desk differs. Check the rule order in this team's own tool
before you rely on a general statement here, and record what you confirmed on the date you confirmed it.

## Order of execution

- Rules in a list run from top to bottom. A rule that runs early can change a field that a later rule tests, so the route is
  what the whole run leaves on the ticket, not the first rule that matched. Trace the actual execution and each field change.
  Freshdesk documents top-to-bottom order for its automation rules.
- Some tools run two kinds of rule at different times. Zendesk triggers run when a ticket is created or updated.
  Automations run on a schedule, about hourly. A third-party guide (2026) says both run in list order. Check that
  against Zendesk's own documentation before you rely on it.
- A rule whose condition stays true can fire again on each later run. Each rule needs a condition that becomes
  false once it has acted, or it repeats on every pass.
- A time-based rule that runs hourly cannot act on a limit shorter than an hour. Freshdesk states this for hourly
  time triggers.

## Timers

- A first-reply target and a resolution target are separate clocks. Map each promise to its metric target within the applicable
  SLA policy. One policy can hold several targets, so check in the team's tool which policy and which target applies to each
  promise.
- Zendesk stops the requester wait while a ticket is Pending (waiting on the customer), and stops agent work time
  while it is On-hold. Timers cannot be reset by a rule. This is from a Zendesk help article read as a search
  excerpt, not the full page.
- Business hours and calendar hours give different numbers for the same promise. Write which one the policy uses,
  and compare it with the contract's wording.

## Sandbox and testing

- A Zendesk sandbox copies triggers, automations and shared macros. Webhooks arrive switched off, and a change made
  in production must be made again by hand. A sandbox test therefore does not prove the production setting.
- A test ticket that matches the conditions, sent in production or in a sandbox, is the check the change request
  names.

## Stale-ticket recipes

- Zendesk publishes a stale-ticket recipe as an example, not a default: it reminds the requester after 4 and 6 days
  in Pending, then sets the ticket to solved at 7 days. A team should choose its own windows and write them down.

## Sources

- 2026-11-01: Freshworks, Freshdesk help centre, "Automation rules" (page updated January 2026): rules run top to
  bottom; hourly time triggers run once an hour.
- 2026-11-01: Zendesk help centre, "About Zendesk sandbox environments" (undated): what a sandbox copies, webhooks
  switched off, production changes redone by hand.
- 2026-11-01: Zendesk help centre, "Bump-Bump-Solve" pending reminders recipe (undated): the 4, 6 and 7 day example.
- 2026-11-01: eesel.ai, "Zendesk trigger order of execution" (third-party guide, 2026): trigger and automation run
  timing and list order. Not checked against Zendesk's own pages.
- 2026-11-01: Zendesk help centre, article on pausing and resetting SLA timers (undated; search excerpt read, not the
  full page): Pending and On-hold pause rules; no reset.
