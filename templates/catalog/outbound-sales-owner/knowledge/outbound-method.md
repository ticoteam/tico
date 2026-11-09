# Outbound method

The standing rules behind every run, first line, sequence and read-out. The figures marked "default" are replaced with the
team's own at setup, and the rules around them stay as they are. The consent basics are not legal advice.

## Target and volume

- The target is meetings booked per week, set at setup. Read it from `hub goal list`. Keep no second copy of it.
- Daily cap per sending mailbox: default 20 new first touches a day for four weeks. The cap rises only while bounces stay under
  2% and spam complaints stay at zero.
- Warm up new mailboxes and sending domains gradually. Watch bounces and complaints by mailbox, not only in total.

## Sequence design

- Default: four touches over about three weeks, on day 0, day 3, day 8 and day 15.
- Each touch brings one new reason: a fact, an answer, or a smaller ask. Never "just checking in", and never a repeat of the
  first touch.
- One ask per touch. A first touch is under 100 words; follow-ups are shorter.
- Plain text. No attachments. At most one link.
- The last touch is a clear close of the thread, with an easy way to say no.
- A contact is in one live sequence at a time.

## Stop rules

These fire in the run that reads them, before anything else is sent.

- **Any reply from the person**: the sequence stops. The reply goes to the right branch in `playbooks/outbound-run.md`. No
  automated follow-up is sent after a human has answered.
- **"Stop", "unsubscribe" or "remove me"**, in any form and on any channel: suppress the person and their address the same day,
  and the company too where the request covers the company. Honour every opt-out within 10 business days at the latest. Act
  well before that.
- **Hard bounce**: remove the address. **Soft bounce**: one retry two business days later, then remove.
- **"Not now"**: pause until the date they gave, or for 90 days.
- **Wrong person**: stop, and reroute the account to the right role under the list-building skill.
- **Customer, competitor or open-deal match**: stop, and send the account to the owner.
- **Last touch sent, no reply**: mark the contact "closed quiet". No new sequence goes to the same person for 90 days.
- **Spam complaint**: stop the sequences on that mailbox for the day, and report it to a person before anything else is sent.

## Guardrails

Each one is reported beside every sequence result.

- **Opt-out rate**: opt-outs as a share of sends. Default threshold 0.5%. Above it, stop the sequence and review its copy and its list.
- **Bounce rate**: bounces as a share of sends. Default threshold 2%. Above it, pause the mailbox and check where the list came from.
- **Spam complaints**: Google's sender guidance asks for a Postmaster Tools spam rate below 0.10% and warns against reaching 0.30%.
  Keep each sending domain under 0.10%. Check the provider's current page before you set a threshold.

## Metrics

- **Sends**: unique messages sent, by step. Drafted, queued and enrolled contacts are not sends.
- **Replies**: any human reply, counted once per contact per sequence.
- **Positive replies**: a reply that confirms interest in a conversation, information or a meeting.
- **Positive rate**: positive replies over sends, at a fixed window of 14 days after the last send.
- **Meetings booked**: meetings accepted from sequence contacts in the window. A meeting booked after the window is counted in the
  window in which it was booked.
- **Meeting rate**: meetings per 100 sends.
- Each sequence and each channel is reported on its own. They are never blended.

## Testing one change

- One variable changes. The hypothesis is one sentence.
- The control is the sequence as it runs now.
- The same contacts are split 50/50 at random before anything is sent.
- The primary metric is positive replies per unique send, at 7 days.
- The stop rules are written before the test starts.
- No call is made under 200 sends per side.
- The test is a proposal on the task. It runs only when a person approves it and a granted Tool allows it.

## Consent and the law (basics, not legal advice)

Check the current text with counsel for each market before a campaign starts.

- **United States, email (CAN-SPAM Act of 2003).** Commercial email to businesses or consumers does not need prior consent. It must
  carry accurate sender and routing information, must not use a deceptive subject line, must identify itself as an advertisement or
  solicitation where the law requires it, must include a valid postal address, must give a working opt-out, and must honour each
  opt-out within 10 business days. The FTC's business guide is the starting point.
- **United States, calls and texts.** Marketing texts or calls that use an autodialer or a prerecorded voice to mobile numbers need
  prior express written consent under the Telephone Consumer Protection Act. Treat text messages with no written opt-in path as off.
  State rules can be stricter.
- **United Kingdom (PECR and UK GDPR).** Marketing email to individual people, including sole traders and some partnerships, needs
  prior consent, unless the soft opt-in applies to an existing customer's similar products and an opt-out was offered when their
  details were collected and in each message. Corporate subscribers can be emailed without consent, but the sender must be identified
  and an opt-out offered. UK GDPR needs a lawful basis and a notice for any personal data used. Check the ICO's electronic marketing
  guidance.
- **European Union (ePrivacy rules and GDPR).** Marketing email to individuals needs prior consent, with an existing-customer exception
  and an opt-out in every message. For business addresses each country sets its own rule, and they differ: Germany requires consent for
  marketing email to businesses too, unless its existing-customer exception applies. GDPR needs a lawful basis for any personal data. A
  person whose data did not come from them must get a notice within one month. An objection to direct marketing stops the marketing at once.
- **Every market.** Never hide or disguise the sender. Keep one suppression list and check it before every send. Keep a record of where
  each contact came from and the basis for contacting them. Delete a person's data when they ask, where the law requires it.

## Rules that do not change

- A stop is recorded in the run that reads it.
- No rate, count or speed is written without a dated source and a window.
- Messages stay drafts until a person approves them and a granted Tool allows the send.
- No contact list enters a sequence without a recorded source and lawful basis.

## Sources

- The CAN-SPAM Act of 2003 (15 U.S.C. 7701 and following), and the FTC's CAN-SPAM compliance guide for business.
- The Telephone Consumer Protection Act (47 CFR 64.1200).
- The Privacy and Electronic Communications (EC Directive) Regulations 2003 (UK), and the ICO's electronic marketing guidance.
- Regulation (EU) 2016/679 (GDPR), Articles 6, 14 and 21. Directive 2002/58/EC (ePrivacy), Article 13.
- Germany: the Act Against Unfair Competition (UWG), section 7.
- Google Postmaster Tools sender guidance.
- 2026-11-06: written for this template from the names of these rules. The texts were not re-read for this run. Check each at source
  before a campaign, and record the date you checked it.
