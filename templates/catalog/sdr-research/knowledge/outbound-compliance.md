# Outbound compliance and sending

Standing reference for outbound prospecting. It is not legal advice. Check the current text with counsel for each market
before a campaign starts.

## Consent by channel

- **Email, United States.** The CAN-SPAM Act (2003) is an opt-out law: commercial email to anyone, business or consumer, needs no
  prior consent. It must not be misleading, must identify itself clearly as an advertisement, carry a valid postal address and a
  working opt-out, and honour each opt-out within 10 business days. Check at source whether an exception applies to the
  advertisement label: the [FTC guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business) and
  15 U.S.C. 7704(a)(5).
- **Email, United Kingdom.** PECR (the UK's electronic marketing rules) requires prior consent to market by email to individual
  subscribers, including sole traders and some partnerships, unless an exception applies. The soft opt-in can cover an existing
  customer's similar products when their details came from that sale and they were offered a simple opt-out at collection and in
  each message. Corporate subscribers can be emailed without consent, but the email must identify the sender and give a valid way
  to opt out. UK GDPR separately needs a lawful basis and a notice for any personal data used. Check the ICO's PECR email
  guidance before a campaign starts.
- **Email, European Union.** The ePrivacy rules require prior consent for email marketing to individuals, except to existing customers about the
  sender's own similar products, with an opt-out offered at collection and in every message (Article 13(2)). For business recipients
  each country sets its own rule, and they differ: Germany requires prior express consent for marketing email to businesses too,
  unless its existing-customer exception applies ([UWG §7, English text](https://www.gesetze-im-internet.de/englisch_uwg/englisch_uwg.html)).
  GDPR separately needs a lawful basis for personal data. Check each market's regulator before a campaign starts.
- **SMS and calls.** Marketing texts or calls to US mobile numbers that use an autodialer or prerecorded voice need prior express
  written consent under the TCPA (47 CFR 64.1200). Check at source with counsel: courts have narrowed the autodialer definition,
  and state laws can be stricter. Treat SMS without a written opt-in path as off until a consented path exists.

## Sender health

- Google and Yahoo announced sender requirements for high-volume senders in late 2023, enforced from February 2024: SPF and
  DKIM authentication, a DMARC record, one-click unsubscribe for marketing mail, and a spam complaint rate kept below 0.3%
  in Google Postmaster Tools.
- Vendor summaries disagree on details, such as whether DKIM is required at low volume and how Yahoo measures complaints.
  Use the providers' own sender documentation when you set a threshold.
- Warm up new sending domains and mailboxes gradually, and watch bounces and complaints by domain.

## Suppression

- Keep one suppression list for stops, bounces, customers and do-not-contact. Check it before every send, not only when a
  contact is first added.

## Sources
- 2026-11-01: web search on Google and Yahoo bulk-sender requirements. The results were vendor blogs that disagree on details.
  The 2023 announcements and the 0.3% complaint figure are stated from memory and were not re-read in this run.
- Consent rules above (CAN-SPAM, TCPA, PECR, GDPR, the ePrivacy rules, UWG §7): stated from memory; the statutes were not read in
  this run. The FTC guide page was checked on 2026-11-01 and exists, but it shows no general consent exception to the advertisement
  label. The ICO page was not reached; check it at source.
