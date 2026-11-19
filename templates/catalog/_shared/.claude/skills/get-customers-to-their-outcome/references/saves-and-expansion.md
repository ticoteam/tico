# Saves, renewals and expansion

## When a customer asks to cancel or downgrade

**1. Give the way out first.** The first reply either confirms it is done or gives the exact steps and how long they
take. Nothing is held back until they have listened to an offer. `check-the-cancel-path` confirms the path works
and is no harder than signing up was.

**2. Find the real reason, once.** If they gave one, use it. If not, ask one optional question in the same reply:
"Would you tell me the main reason? It helps us fix things." Do not make the answer a condition. Code what they say
with `code-a-cancellation-reason`, keeping their exact words beside the code. Typical codes, to adapt:

| Reason | Can the team change it? | Fitting response |
|---|---|---|
| Did not get the result | Often | Hands-on help to reach it, with a date; a pause while that happens |
| A failure of ours (bug, outage, bad service) | Yes | recover-a-bad-experience first; no offer until the fix is confirmed |
| Too expensive for the use they get | Sometimes | A plan that fits their real usage; never a discount to keep them on the wrong plan |
| Need has ended or is seasonal | No | A pause if the save policy allows; otherwise a clean goodbye |
| Missing something they need | Sometimes | Only an honest answer about whether and when it exists. No roadmap promise without an OK |
| Moved to a competitor | Rarely | Ask what decided it; record it; no counter-offer unless the reason is one of the rows above |
| Business closed, role ended, acquired | No | No offer. Help them export their data |
| Payment failed (not a choice) | Yes | A separate queue: fix the payment, not the relationship |

**3. One offer at most, matched to the reason.** Choose with `match-one-save-offer` from `knowledge/save-policy.md`.
State it once, plainly, after the way out. If the reason is one the team cannot change, make no offer. If they
decline, do not make a second.

**4. If they go, make it good.** Process it the same day. Confirm in writing: what ends, on what date, what they
keep access to until when, what happens to their data and how to get it, and that they will not be charged again.
No survey beyond the one question. People come back to teams that let them leave well.

**5. Count saves honestly.** A save is a customer still paying 30 days later, and again at 90. Log each save attempt
in `knowledge/save-log.md`: date, customer key, coded reason, offer, accepted or not, paying at 30 days, paying at
90 days. With fewer than about five cancellations a week, report counts, not rates.

**6. Learn which offers work.** Every quarter, read the log by reason and offer. An offer that is accepted but does
not hold to 90 days is postponing the loss, not preventing it; stop making it for that reason. Propose changes to
the save policy to the owner with the counts.

**7. Send the reason where it can be fixed.** Each coded reason that appears three times in a month becomes a task
for the owner of the cause, with the customers' own words.

### Failed payments

These customers did not choose to leave. Keep them out of the save numbers. Tell them plainly that the payment
failed, how to fix it and by when service is affected. Retry on the schedule the billing Tool supports. After 14
days unrecovered, the account is red and a person-quality message replaces the automated ones.

### Law to check

Subscription cancelling is regulated and the rules are moving. US default as of 2026-11-18: the FTC's 2024
click-to-cancel rule was struck down in July 2025 and a new rulemaking is under way; the Restore Online Shoppers'
Confidence Act and the FTC Act still require clear terms, consent, and a simple way to stop recurring charges;
several states go further (California's auto-renewal law as amended from 1 July 2025 is the strictest commonly
met). The EU requires a withdrawal function for online contracts from 19 June 2026. Check the current text for
where the customer lives. This skill's rule, that leaving is never harder than joining and one offer is the most
that stands in the way, is meant to be safe everywhere, but it is not legal advice.

## Renewals

Work back from the **notice date**, the last day either side can give notice, not the end date.

| When (default) | Do |
|---|---|
| 120 days before notice | Read the contract (`read-a-contract-for-renewal-terms`), count the date (`count-a-notice-deadline`), check health, hold the pre-renewal review |
| 90 days | Any open problem has an owner and a date. Any change the customer wants is known |
| 60 days | Send the renewal with `write-a-renewal-cover-note`: what they got against their goal, the terms, the date, how to change or cancel |
| 30 days | One reminder if no reply. For auto-renewals, the legally required reminder if one applies where they live |
| Notice date | Decision recorded. Outcome added to the calibration history |

- A standard renewal at current price and terms goes out on your own. A changed price or term needs an OK.
- A price increase is told early, plainly, with the date and the amount, and never hidden in a renewal notice.
- If the account is red at 120 days, the work is the red-account plan, and the renewal conversation waits for
  evidence that things have turned.
- Never let an auto-renewal pass silently for a customer you know is unhappy. Tell them the date.

## Expansion

Expansion is the customer getting more of the result. Offer it only when both hold:

1. the account is green, and the outcome in the plan is reached or clearly on track;
2. there is evidence of a goal or a limit the larger plan would serve.

Rate the evidence:

| Rating | Evidence | What to do |
|---|---|---|
| Strong | The customer stated a new goal the current plan cannot reach, or asked about a higher plan, more seats or another product; or usage has been at or over what they pay for across two billing periods | Hand to run-a-sales-deal with the outcome plan and the evidence |
| Medium | A second team or site is using it informally; a feature tied to a higher plan is being tried; the champion mentions growth plans | One helpful message that names what you saw and asks if it is a goal. No pitch |
| Weak | They resemble accounts that upgraded; a single spike; a guess | Nothing. Note it and look again next quarter |

Rules:
- Write the evidence down with its source and date before acting. "They seem ready" is not evidence.
- Lead with their goal, not the product: what they would get, in their terms and numbers.
- If a cheaper plan fits them better, say so. Moving a customer down to the right plan keeps them.
- Usage over the limit is a conversation, not a surprise invoice, unless the contract says otherwise and they were
  told in advance.
- Never raise expansion with a yellow or red account, in a recovery, or in the same message as an apology.
- The handoff to run-a-sales-deal carries: the outcome plan, the evidence and its rating, the people and what each
  cares about, the contract facts, and anything not to do.
