# Email playbook

How {{company_name}} runs email, and what the Email Owner may do by itself. This is the starter: the Email Owner fills
in the blanks at setup and publishes it as the page "Email playbook". To change a line, tell the Email Owner on a task;
it changes this file and publishes the page again.

## The basics

- **Channel tag:** `channel:email`. Every email task carries it.
- **KPI:** conversions from email. Read weekly in the email platform's conversion report, or in analytics with email
  as the source. It sits under the North Star.
- **Tools:** the email platform. Read is enough to start. A mailbox with replies to earlier emails, when connected.
- **Who puts changes live:** (filled at setup: the person who sends from the email platform). Until a Tool lets the
  Email Owner send, this person puts each email a person OK'd live.
- **What must never change or never be in an email:** (filled at setup).
- **Who gets email:** only people on a list who agreed to it. Each list and its promise is in `knowledge/segments.md`.
  The Email Owner never mails anyone who did not agree to it.

## What Watch checks

Daily, every morning.

- Each kept flow and campaign: bounces and spam complaints since the last check, against its range.
- On Mondays, each kept flow: conversions for last week, against its range.
- On Mondays, the KPI: conversions from email, for last week.

## Where ideas come from

- The email platform: flows and emails with many sends but few clicks or conversions, and ones that fell.
- Gaps in what goes out: no welcome flow, no flow for people who stopped using the product, one email sent to the
  whole list.
- Replies to earlier emails, and the questions customers ask in them.
- What competitors send (`tico market delta`), and what they offer that we do not mention.
- Kept flows that were retired: what to try instead.

## Upkeep the Email Owner does by itself

No person's OK needed. Done within granted Tools, or filed with the exact change for the person who puts changes live.

- Keep a kept flow running as a person OK'd it: fix a broken link, put back copy that was changed or lost.
- Remove addresses that bounce hard, as far as the email platform allows.
- Pause a send or a flow whose complaints or bounces jump, and say why on its task.
- Keep `knowledge/segments.md`, `knowledge/results.md` and `knowledge/do-not-email.md` current.

## What needs a person

- A new email, a new flow, a new subject line test or a new audience: that is a bet. A person OKs its Go-live page
  first.
- Anything on the "never" list above.
- Any change to a list, a segment or a contact beyond dropping hard bounces.

## Cutoffs: set these in Rules

- **Email pauses when the complaint rate passes N.**

This cutoff is off until a person sets N in Rules. {{app_name}} does not turn it on for you. The Email Owner may always
pause a send or a flow by itself, with or without a cutoff. It never sends to a bigger list, or more often, than an
approved experiment says.

## Never

- Mail anyone who did not agree to it, has unsubscribed, or is on the do-not-email list.
- Mail a list under a different promise than it signed up for: a newsletter list is not a sales list.
- Promise an open, a click or a sale.
- Write a number without its dated source.
