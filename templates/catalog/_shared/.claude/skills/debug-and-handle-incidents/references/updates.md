# Severity and updates

## Severity

Starting table. A team replaces the times with its own in `knowledge/engineering.md`.

| Level | What it means | First response | Updates |
| --- | --- | --- | --- |
| SEV1 | The product is down or unusable for all or most customers; or data is being lost or exposed | At once | Every 30 minutes |
| SEV2 | A main feature is broken or badly degraded for many customers; or money is wrong for some | Within 15 minutes | Every 60 minutes |
| SEV3 | A lesser feature is broken for some customers, with a way round it | Within 1 hour | Once a working day |
| SEV4 | Cosmetic or very low impact | Next working day | When fixed |

The four levels and the first-response times follow Anthropic's `incident-response` skill as read on 2026-11-18. The
update rhythm is a starting value written for this skill.

When unsure between two levels, pick the more severe one and lower it when the count comes in. Raise the level the
moment the count or the kind of harm (money, data, security) says so. Write each change of level on the task with
the reason.

## Who does what

In a team of bots one bot often holds more than one of these. They are still separate jobs, and the task should say
who holds each.

- **Lead**: owns the incident task, sets severity, decides what is tried next.
- **Fixer**: reduces the harm and finds the cause.
- **Updates**: writes and sends every update on time. Never the same attention as the fixer's while a SEV1 is
  running; if one bot must do both, updates win at the promised time.

## The update

Every update, inside or outside the team, answers four questions in this order.

```
Incident: <title>
Severity: SEV<n>        Status: Investigating | Cause found | Fix released, watching | Resolved
Time: <UTC>

What is happening:   <the symptom, in the customer's words>
Who is affected:     <who and how many, from sources; or "not known yet">
What we are doing:   <the action in progress and what has been done since the last update>
Next update:         <a time, UTC>
```

Status moves forward only on evidence: "Cause found" after the repro confirms a hypothesis, "Resolved" after the all
clear check.

## Wording for customers

- Say what they see, not what broke inside. "Some orders are not showing in your history" beats "replication lag on
  the orders database".
- Give counts and times only from sources. If not counted yet: "We are still working out how many customers are
  affected."
- Do not guess the cause. "We have not found the cause yet" is honest and enough.
- Do not promise a fix time. Promise the next update time, and keep it.
- Say what they should do, if anything: "You do not need to do anything" or "Please do not retry the payment."
- If money was taken wrongly or work was lost, say so plainly and say what will be done about it once that is
  decided. Do not offer refunds or credits the owner has not approved.
- Facts, not fault. Never admit fault and never apologise for what the company did: a public statement can be used
  as an admission. Sympathy for the disruption, once, is fine. Name the cause in public only in wording the owner has
  approved after counsel has seen it.
- No blame, of the team, a teammate, a supplier or the customer.
- Short. Four to six sentences.

Every public statement during an incident (status page, customer email, in-app notice, social post, a reply to a
reporter) follows the statement rules of the `handle-press-and-a-crisis` skill: confirmed facts only, the same words
in every channel, nothing on cause, compensation or legal position before counsel, and fault never. If your role does
not have that skill, the owner's OK on the wording still applies, and press or social questions go to the teammate
who has it, on a task.

First update, as an illustration (invented):

"Some customers with a paused subscription were charged today. We have stopped the process that caused it, so no new
charges are being made. We are finding the cause and working out exactly who was affected. You do not need to do
anything. Our next update will be at 15:40 UTC."

Final update, as an illustration (invented):

"This is resolved. Between 13:00 and 14:36 UTC today, 11 customers with a paused subscription were charged. No
charges have been made to paused subscriptions since 14:36. Every one of those charges has been refunded in full;
refunds reach statements within 5 to 10 working days. You do not need to do anything. We will post a last update
here at 17:00 UTC tomorrow, once the refunds have settled."

## When personal data or security is involved

Stop before sending anything outside the team. Tell the owner at once on the task with what is known: what data,
whose, how many, since when, whether it is still exposed. Laws in many places set deadlines and required content for
notices about exposed personal data, and they differ by place and by kind of data. The owner and a lawyer decide who
is told, when and in what words. The bot's job is to stop the exposure, keep the evidence and keep the timeline
exact.

## Where updates go

| Audience | Where | Needs an OK |
| --- | --- | --- |
| The team | A comment on the incident task; teammates who follow the task see it | No |
| Support | The same task; Support uses the approved customer wording in replies | No, once the wording is approved |
| Customers | Status page, in-app notice or email, as set in `knowledge/engineering.md` | Yes, each new wording |
| Owner | An ask on the task when a decision or OK is needed | n/a |
