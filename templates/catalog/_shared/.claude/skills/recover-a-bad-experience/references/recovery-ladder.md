# The recovery ladder

How big the failure is decides how much you do. Size it by what it cost the customer, not by how loudly they said so.
The amounts and times here are starting defaults for this library. The team's own numbers in `knowledge/recovery.md`
replace them.

## Three levels

| Level | It is this when | Apology | Fix | Gesture | Who is told |
|---|---|---|---|---|---|
| 1. Annoyance | A small delay, a confusing step, a slow reply. Nothing lost but a little time | Only if the team caused it and the customer noticed: one line. Otherwise none; the fast fix says it | Yes | None needed. Speed is the gesture | Log only |
| 2. Real loss | They lost money, meaningful time, or use of what they paid for; a date they cared about was missed or put at risk; a second contact about the same thing | Yes, specific | Yes, done for them and confirmed | One, matched to the loss, inside the yes limit | Log, cause task |
| 3. Serious harm | A large loss, a spoiled once-only occasion, a privacy or safety problem, a failure that hurt their own customers, a third failure in 90 days | Yes, and from a named person if the owner wants | Yes, with a named owner and updates | One, usually above the limit, so ask | The owner, at once; legal if injury, safety, privacy or a threat is involved |

Move up one level when: the customer already contacted the team about this once; they have had another failure in
the last 90 days; the team broke a promise made during an earlier recovery.

Do not move up because the customer is rude, and do not move down because they are gracious.

## The apology

Apologise sparingly: only when the team really failed and its data confirms it, and then at once. An apology given
by reflex ("sorry for any inconvenience") is worth nothing and cheapens the real ones.

Five parts, in this order. Most apologies need three or four sentences in total.

1. **What happened, in their words.** "Your order should have arrived Thursday and it didn't."
2. **Sorry, owned.** "I'm sorry. That's on us." Use "we". Never "mistakes were made", "there was an issue", or
   "sorry for any inconvenience".
3. **What it cost them,** if you know. "I know you took the morning off to be in."
4. **What you have done or are doing, with a time.** Lead with what is already done.
5. **What happens next,** and that you will be the one to tell them.

Leave out:
- a cause you have not confirmed;
- any third party's name as the reason;
- "but", "however", "unfortunately", "if you felt", "as you can imagine";
- how hard the team works, how rare this is, or how busy it has been;
- any claim that it will never happen again, unless the cause is confirmed fixed.

When you do not yet know whether the team was at fault: do not apologise yet. Say plainly what you understood
happened to them, what you are checking, and when you will be back, inside the hour where you can. The moment the
data confirms a failure, apologise.

When it comes through a formal channel (the BBB, a lawyer, an agency, a threat to sue, a public crisis) or fault
could carry legal weight (injury, safety, a large financial loss): never admit fault. Say what you are doing, give a
contact, state no fault and no cause, use at most one line of regret for what they went through, and hand the
matter to the answer-a-demand-or-complaint skill. Some US states protect expressions of sympathy from being used as evidence and
some do not, and the rules differ by subject, so do not rely on that protection. Check locally.

## The fix

- Done for the customer, not explained to them.
- Whole, not partial. If part must wait, say which part and the date, and create the task.
- Checked in the Tool before you say "done": the refund shows as issued, the shipment has a tracking number, the
  account opens.
- Free of new effort for them. No forms, no "please send a photo" unless policy truly requires it and the amount is
  above the yes limit. Take their word inside the limit.
- If the same fix has failed once for this customer, do not repeat it. Pick a different route or a teammate.

## The gesture

Match it to what was lost:

| They lost | Fitting gesture |
|---|---|
| Time, or had to chase | Something that saves time next: expedited shipping, a priority flag, you doing the setup for them |
| Money | Money: a credit or refund beyond what is owed |
| Use of the service | Service: free days or a free month, sized at least to the time lost |
| An occasion | Something personal and specific to what they were trying to do |
| Trust, after a repeat failure | A named person, a direct line, and a check-in you actually make |

Rules:
- One gesture. Stating it plainly beats dressing it up.
- No strings: no minimum spend, no expiry shorter than 12 months, no survey or review asked for alongside it.
- It never replaces the fix, and it never arrives without the apology.
- Inside the yes limit, do it and tell them. Above it, ask with a recommendation, and do not tell the customer no.
- If the customer names what would make it right and it is inside the limit and reasonable, do that instead of your
  own idea.
- If they refuse a gesture, do not push. Note it.

## Asking above the limit

The ask shows the person everything needed to click once:

1. What happened and what it cost the customer, in three lines.
2. What has already been done (apology, fix).
3. The gesture you recommend, its cost, and why this size.
4. What the customer has paid in the last 12 months and how long they have been one.
5. What happens if the answer is no: what you will offer instead inside the limit.

Publish it as a short page and ask with
`tico task ask <id> --to <person> --kind ok "<gesture, amount, customer key>" --show page:<id> --version N`.
The OK covers exactly what was shown. If the amount changes, ask again.

## The log

`knowledge/recovery-log.md`, one row per recovery, customer keys not names:

| Date | Customer key | Moment | Level | Cause | Fix | Gesture and cost | Failure to apology | Failure to fix | Follow-up result |
|---|---|---|---|---|---|---|---|---|---|

Read it weekly for: the three causes that cost most; the moments that break most; whether time to apology is inside
the hour; total gesture cost against the monthly limit; any customer with three rows.

## When the customer is wrong, abusive, or asking for too much

- **The data shows no failure.** Do not apologise. Show you understood what they went through, show them what the
  record says, and offer what is on offer to anyone. Use the "clear no" pattern from answer-a-customer.
- **The ask is far beyond the loss.** Fix fully, offer the gesture the ladder gives, say plainly that the larger ask
  is not something the team will do, and tell them how to take it further if they wish.
- **A pattern of claims.** Use `flag-a-return-pattern`. A flag is a question for a person, never an accusation in a
  reply.
- **Abuse or threats.** One calm reply stating what you will do and that the conversation continues by email only.
  Tell the owner. Threats of harm go to the owner at once.
