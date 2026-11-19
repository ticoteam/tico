# Legal hold: decide, place, track, release

A legal hold is the company telling itself, in writing, to keep everything about a dispute. Courts expect it once a
dispute is reasonably expected. Losing records after that point can cost more than the claim: a court can assume the
lost records were bad for the company, or decide the case against it.

United States default. The exact trigger and the penalties depend on the court and the state. When a lawyer is
engaged, the lawyer sets the scope and this file is the bot's part of the work.

## 1. Decide: does a hold apply?

The test: is a legal claim reasonably expected? A credible probability, not a bare possibility.

| Event | Decision |
|---|---|
| Court papers, a small-claims notice, an arbitration demand, a subpoena | Hold. Today. |
| A lawyer's letter, a notice under a consumer-protection law, a preservation demand | Hold. Today. |
| A government or licensing-board complaint or inquiry | Hold. Today. |
| The company decides to send a cease-and-desist or a demand of its own | Hold on the company's own records on the subject. |
| A customer says in writing they will sue, with specifics (a claim, an amount, a date) | Hold. |
| The company consults a lawyer about a dispute with a named person | Hold. |
| An injury, property damage or a data incident involving a customer | Hold, even before any complaint. |
| A card chargeback | Narrow hold: that customer's records. Widen it if a letter or a claim follows. |
| An angry message with a vague "you'll hear from my lawyer" and nothing more | Usually no hold. Log the decision and the reason; look again if anything follows. |
| An ordinary complaint or refund request | No hold. |

Every event in this table gets a line in `knowledge/legal-holds.md`, including the "no hold" decisions, with the
date, the reason and who decided. The log is the proof that the company acted reasonably.

## 2. Scope it

Narrow enough to be followed, wide enough to cover the dispute. Write four things.

- **Subject:** the customer, the order or account, and the issue. One or two sentences in neutral words.
- **Dates:** from the first contact with the customer (or the start of the conduct at issue) to today, and
  continuing until release.
- **People and bots:** every teammate who dealt with the customer or the issue, human or bot: sales, support, the
  person who did the work, billing, managers who were asked, the bots that wrote or sent messages. Include
  contractors who did the job. Include anyone who has left; their accounts must not be deleted.
- **Systems:** go through this list and mark each "holds records / none / unknown".
  - Email and shared mailboxes.
  - Tico: the tasks about the customer, their comment threads, chat threads and direct messages, files and
    pages, meeting notes and recordings, and each involved bot's repository (`knowledge/`, `reports/`, `memory/`).
  - Help desk, CRM, live chat, SMS and phone systems, call recordings and voicemail.
  - Orders, bookings, scheduling, job check-in and check-out logs, location logs, job photos.
  - Payment processor records, invoices, refunds, the bookkeeping entries.
  - The website and app as they were: terms, checkout and signup pages, pricing, ads and emails the customer saw,
    with version history.
  - Consent and account logs: timestamps, IP addresses, devices, clicks on "I agree", cancellation requests.
  - Personal phones and personal messaging apps used for work.
  - Paper, and backups.

## 3. Place it, within one business day

1. Write the log entry (section 6).
2. Send the hold notice (section 5) to each person and bot in scope. For a bot, post it on a task assigned to the
   bot and add a line to its instructions or memory for the life of the hold. For a contractor, send it by email.
3. Switch off scheduled deletion that would touch records in scope: retention rules, auto-archive, auto-delete of
   chats or recordings, log rotation. Do it where the bot's Tools allow; otherwise file a same-day task for the
   person who can, and note it in the log.
4. Export anything that will expire soon or is easy to lose: call recordings, chat transcripts, photos on a
   contractor's phone, pages that are about to change. Save exports in `reports/matters/<id>/evidence/` with the
   date pulled. Before a page or the terms are changed, save a dated copy.
5. Ask each person to confirm in writing within three business days. Chase once. Record who confirmed and when.

A hold does not stop the business. Normal work with the customer's account continues. It stops deletion and
alteration, and it stops new commentary: people in scope should not write opinions about the dispute in chat or
email, and should not discuss it with the customer.

## 4. Keep it alive, then release it

- Refresh every six months, and whenever the claim changes or a new person becomes involved: resend the notice,
  re-check that deletion is still off, update the log.
- When a teammate in scope leaves, keep their accounts and files; do not let offboarding delete them.
- If the claimant asks for their personal data to be deleted while the hold applies, the records stay. Hand the
  request to the privacy owner with a note that a legal hold applies, so the reply to the claimant is correct.
- Release only when all are true: the matter has ended (paid, dismissed, settled in writing, or decided and the
  time to appeal has passed); no related claim is expected; the limitation period for the claim has passed or a
  signed release covers it; and the approver, and the lawyer if one was engaged, say so in writing.
- On release: tell everyone in scope, switch normal retention back on, record the release date and who approved
  it. The evidence folder for the matter is kept under the team's normal retention rule for legal files.

## 5. The hold notice

```
Subject: Legal hold - keep all records about [neutral subject]. Action needed.

To: [each person, bot and contractor in scope]
From: [signer], [title], [Company legal name]
Date: [date]

1. The Company expects, or is in, a legal dispute concerning [customer or party] and [issue in neutral words].

2. From now until you are told in writing that this hold has ended, do not delete, edit, overwrite or throw away
   anything relating to that subject, dated from [start date] onward. This includes emails, chat and text
   messages, task comments, notes, drafts, photos, recordings, files, calendar entries and paper, on work systems
   and on personal devices used for work.

3. Switch off any setting that deletes or archives such records automatically, or tell [name] today if you
   cannot.

4. If you are unsure whether something is covered, keep it.

5. Do not discuss the dispute with [customer or party], and do not write opinions about it in email or chat.
   Send questions to [name].

6. Reply to this message by [date, three business days] to confirm you have read it and will follow it.

This hold stays in force until [signer] ends it in writing.
```

Keep the notice factual. It is likely to be seen by the other side one day. No views on who is right.

## 6. The hold log (`knowledge/legal-holds.md`)

One entry per decision:

| Field | Content |
|---|---|
| Matter | The id from `knowledge/matters.md` |
| Event and date | What triggered the decision, and when the team learned of it |
| Decision | Hold, narrow hold, or no hold, with the reason in one line |
| Decided by, on | Name and date |
| Subject and date range | As scoped |
| People and bots | Each name, date notified, date confirmed |
| Systems | Each system, what was switched off, by whom, when |
| Exports | What was exported, when, where it is saved |
| Refresh due | Date |
| Released | Date, approved by, or "in force" |

## What a hold is not

- Not a reason to collect everything the company has. Keep to the subject.
- Not permission to tidy, relabel or "organize" the originals. Work on copies.
- Not secret from the people it binds, and not to be forwarded outside them.
