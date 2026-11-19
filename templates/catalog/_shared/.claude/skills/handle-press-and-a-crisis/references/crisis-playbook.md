# Crisis playbook

Law in this file is the US default with a few well-known rules from elsewhere, checked 2026-11-18. It is a list of
what to flag to counsel, not legal advice. Deadlines and duties depend on the place, the contract and the facts:
counsel decides.

## 1. What counts, and how big

| Level | Looks like | First public word | Approves |
|---|---|---|---|
| 1. Watch | A complaint gaining attention, a harsh review thread, a reporter asking about a rumour, a small fault customers have noticed | None yet. Monitor, start the fact log, prepare a statement. Answer individuals normally. | Nobody needed |
| 2. Incident | Customers are affected now and it is contained or being fixed: an outage, a wrong email, a billing error, a defect | A holding statement within 60 minutes of the facts being confirmed | The owner. Counsel if a trigger in section 5 applies. |
| 3. Crisis | Harm to people, personal data exposed, legal or regulator action, an allegation about leadership, a safety problem, national press | An acknowledgement on the company's own channel within 60 minutes, and fuller statements as facts are confirmed | The owner and counsel, every time |

The 60 minutes is this skill's default, taken from the common practice of speaking early. Speaking first matters only
when what is said is right: a late true statement beats an early wrong one.

Level goes up when: people could be hurt, personal data is involved, a regulator or lawyer appears, a reporter from a
major outlet asks, or it is still growing after four hours. When unsure between two levels, treat it as the higher.

Events to pre-draft for (pick the three most likely at set-up): the service is down; customer data went to the wrong
place; a product hurt someone or could; a public accusation against a named member of the team; a lawsuit or
regulator letter becomes public; a customer's complaint spreads widely; a founder or executive leaves suddenly; a
supplier or partner's failure reaches the company's customers.

## 2. Who does what

| Role | Does | Default |
|---|---|---|
| Approver | Says yes to every statement | The owner |
| Speaker | The one voice in public and to reporters | The owner, or whoever `knowledge/rules.md` names |
| Fixer | Leads the repair, confirms facts for the log | The teammate who owns the system or area |
| Counsel | Reviews wording, works out notice duties and deadlines, talks to insurers and regulators | The legal owner in Tico, and outside counsel where there is one |
| Writer | Fact log, statements, channel checklist, monitoring. This skill. | The PR or communications bot |
| Support lead | Answers individuals with the approved wording, reports what customers ask | The support owner |

Everyone else says one thing if asked: "Please see our statement at [link]. [Speaker] is the person to talk to."
Tell the team this at the start.

## 3. The first hour

| When | Do |
|---|---|
| Minute 0 | Open the crisis task. Start the fact log. Note the time the company became aware: legal deadlines can run from it. |
| By 10 | Size it (section 1). Pause scheduled marketing. One ask to the owner naming the level and what you need. |
| By 20 | Check the legal triggers (section 5). If any applies, the brief goes to counsel now, before drafting. |
| By 40 | Holding statement drafted from the confirmed list, with sources, approver and speaker named. |
| By 60 | Approved, then published in the order in section 6. Next update time set and on your task. |

If a pre-approved holding statement exists for this exact risk and no legal trigger applies, publish it as soon as
its facts are confirmed and tell the owner you did.

## 4. The holding statement

Three to five sentences. Each is one of these, in this order:

1. **What happened**, in confirmed facts, with the time. Plain words, no jargon.
2. **Who is affected**, and concern for them.
3. **What the company is doing now.**
4. **What affected people should do**, if anything. If nothing, say they need to do nothing.
5. **When and where the next update comes.**

| Say | Do not say |
|---|---|
| What you know, with the time you knew it | A guess at cause, scale or how long it will last |
| "We are still confirming X" | "We believe", "it appears", "probably" |
| Once, where people really were affected: "We are sorry for the disruption and worry this causes" | "We apologise for any inconvenience" (it shrinks it and reads as reflex); an apology for what the company did (it admits fault) |
| The counted number, once confirmed | "A small number", "minor", "isolated", "limited" without a count |
| What is being done, by the company | Who is to blame, inside or outside. A named person. |
| "We will update here at 13:00" | "No comment". "As soon as possible". |
| What people should do to protect themselves | Advice you have not checked with the fixer |
| The same words in every channel | A different story for press, customers and staff |
| Nothing about a matter counsel has not cleared | Cause, compensation or legal position, before counsel. Fault, ever |
| "This affected..." | "Out of an abundance of caution", "we take X very seriously" (said by everyone, believed by no one) |

Patterns, to be filled only with confirmed facts:

- **Service down:** "Since [time], [service] has been unavailable for [who]. We are sorry: we know people rely on it.
  Our engineers are working on it now. [You do not need to do anything / Do X meanwhile.] Next update here at [time]."
- **Data went to the wrong place:** "At [time] we found that [what] was [sent to / visible to] [whom]. It included
  [fields] and did not include [fields]. We have stopped it and are contacting everyone affected directly. Next update
  at [time]." Counsel first, always.
- **Someone was or could be hurt:** "We have learned that [what happened, confirmed]. Our first concern is for
  [those affected]. We have [stopped sales / contacted customers / reported it]. If you have [product], [what to do].
  Next update at [time]." Counsel and the insurer first, always.
- **An accusation about a person:** "We are aware of [the claim, described neutrally]. We are looking into it and
  have [the step taken]. We will not comment on individuals while that is under way. Next update at [time]."
  Counsel first, always. Name no one and characterise nothing.
- **A legal or regulator matter is public:** counsel writes or clears every word. Often one line: "We have received
  [the filing] and are reviewing it."
- **A complaint has spread:** answer the person directly and kindly first. Public reply: what you have done for
  them, without their private details. Do not argue the facts in public.

## 5. Legal triggers: flag to counsel at once

| Trigger | Why it is urgent | Note for the brief |
|---|---|---|
| Personal data exposed, lost or wrongly accessed | Notice duties with deadlines. EU and UK (GDPR Article 33): the regulator within 72 hours of becoming aware, where feasible. Every US state has its own breach-notice law, with different deadlines and content rules. Contracts with business customers often set shorter notice periods. | When the company became aware, what data, whose, how many, where they live, which contracts apply |
| A public company, and the incident may be material | US: SEC Form 8-K Item 1.05 is due four business days after the company decides a cybersecurity incident is material. Selective disclosure rules apply to what is told to whom. | Who knows, what has been said outside |
| Injury, death or a safety defect | Product-safety and workplace regulators can have very short reporting deadlines. Liability. | What happened, the product or place, what has been stopped |
| A lawsuit, a lawyer's letter, a regulator's inquiry, a threat of either | Anything said can be used. Records must be kept from the moment a claim is reasonably expected. | The document, who received it, what has been said so far |
| An allegation about a named person | Defamation, privacy and employment law | The source of the allegation. Do not investigate it yourself. |
| Wording that states a cause or offers compensation (wording that admits fault is never published at all) | It can be used as an admission. Some US states protect expressions of sympathy from being used as evidence, fewer protect admissions of fault, and many such laws cover only certain kinds of case. | The exact wording proposed |
| Anything insurance might cover (cyber, liability, directors) | Policies usually require prompt notice, and some require the insurer's consent before costs or admissions | Which policies exist; see the finance skill `manage-insurance` when the team has it |

**Keeping legal advice protected.** In the US, what passes between a company and its lawyer for legal advice is
protected. Sharing that advice with others, including outside PR people, can lose the protection: courts have gone
both ways on whether a PR firm's work is covered. So: counsel hires and instructs any outside crisis firm, counsel is
on those threads, and the lawyer's advice itself is not copied into the crisis task, statements or chats. The
statement shows the outcome of the advice, never the advice.

**Keep the records.** From the moment a claim or investigation is reasonably likely, nothing related is deleted or
edited: posts, messages, logs, drafts. If a public post must come down, counsel decides, and a copy is kept.

## 6. Who hears, in what order

1. **The people affected**, directly: email or in-app, from `write-a-customer-update`. They never learn it from the
   news.
2. **The team**: what happened, the statement, who speaks, what to say if asked.
3. **The company's own public channel**: status page or site banner, then social accounts.
4. **Partners, investors, insurers, regulators** as counsel and the owner direct.
5. **Reporters who asked**: the statement and the next update time. Do not pitch a crisis.

The facts are identical everywhere. Only the form changes.

## 7. Update rhythm

Defaults. Publish at the promised time even with nothing new.

| Situation | Update at least |
|---|---|
| Ongoing and customers are blocked | Every 60 minutes until fixed |
| Contained, cause being confirmed | Twice a day |
| Fixed, review under way | When the review is done, with a date given in advance |

Each update: what is new since the last, what is still unknown, the next time. A correction goes at the top:
"Correction: at 10:00 we said X. That was wrong. It is Y."

## 8. What to pause, and when to restart

Pause: scheduled social posts, marketing emails and sequences to affected people, ads on the affected product,
launch announcements, upbeat automated replies, review requests. Keep running: receipts, service messages, the
status page, support.

Restart item by item after the final statement, not all at once, and never the same day for a level 3. Read each
queued item again first: copy written before the event may now read badly.

## 9. Reporters during a crisis

- Answer every query, quickly, with the current statement and the next update time.
- One speaker. No one else comments, including on personal accounts.
- Nothing is off the record. Do not explain "background" to a reporter by chat.
- Do not confirm or deny a rumour beyond the statement. "We have nothing to add to our statement beyond what is
  confirmed. The next update is at 13:00."
- Correct factual errors with evidence. Do not dispute tone.
- Log each query: who, outlet, question, deadline, what was sent.

## 10. The review afterwards

Within a week, one page: the timeline from the first sign to the final statement; the time taken to confirm, to
draft, to approve and to publish; what customers and reporters asked that the statements did not answer; what was
slow and why; changes to `knowledge/rules.md`, to the pre-drafted statements and to who is reachable out of hours.
Written without naming who was at fault.
