# Handle a cancellation

Triggered by a task or message where a customer asks to cancel, downgrade or stop renewing. Budget 10 minutes.

Inputs: the customer's own words; their plan, tenure and value (CRM or billing read); recent tickets and usage if readable;
`knowledge/save-policy.md` and `knowledge/cancellation-practice.md`.

Outcome: one coded reason, one matched offer or none, the cancel path stated, and a reply ready to use. Nothing changes unless an
accepted offer or a confirmed cancellation is requested.

Done when: the request has one reason code from what the customer said; the offer is one from the policy, or none with its reason;
the cancel path is checked with `skills/check-the-cancel-path/SKILL.md`; the line is in `knowledge/reasons.md`; and the reply is on
the task for the owner.

---

## 1. Read the request and the account

The customer's own words, their plan, tenure and value (CRM or billing read), recent tickets and usage if readable. If a request
for this customer is already open, add to it.

## 2. Code the reason

Follow `skills/code-a-cancellation-reason/SKILL.md`. Code from what they said, not what you suspect.

## 3. Match one offer, or none

Follow `skills/match-one-save-offer/SKILL.md`. Never invent an offer.

## 4. Prepare the reply

Under 120 words: thank them, acknowledge the reason in their terms, make the one offer if there is one, and say plainly how to
complete the cancellation and when billing stops. Check the cancel path with `skills/check-the-cancel-path/SKILL.md` before it goes
on the task. For a missing feature, say honestly whether it is planned only if a human or the docs confirm it.

## 5. Put it forward

Attach the reply to the task for the owner. If the customer accepted an offer, or confirmed the cancellation in writing, and the
task asks for the change, apply it with your Tools. Otherwise create one task for the billing owner naming the missing access. Log
the line in `knowledge/reasons.md` and commit.
