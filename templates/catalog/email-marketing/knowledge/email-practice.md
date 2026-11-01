# Email practice

The standing method behind `playbooks/draft-a-campaign.md`, `playbooks/weekly-email-draft.md` and `playbooks/onboarding.md`.
`voice.md` and `segments.md` hold the team's own voice and audiences. This file holds what is true about email in general.
Sources were read on 2026-11-01 through search excerpts unless marked otherwise.

## Sender rules for bulk mail

- Google's sender guidelines require SPF, DKIM and DMARC of bulk senders, meaning more than 5,000 messages a day to Gmail.
  Google and Yahoo both set a spam-complaint ceiling of 0.3 percent. Google advises keeping it under 0.1 percent.
- Marketing and promotional mail needs a one-click unsubscribe: a List-Unsubscribe header with one HTTPS link and the
  one-click POST option. Transactional mail is excluded.
- Yahoo and Google both expect an unsubscribe to be honoured within two days (48 hours).
- Microsoft announced SPF, DKIM and DMARC requirements for domains sending more than 5,000 emails a day, in 2025. Its
  enforcement details changed during 2025 and no 2026 confirmation was found. Check Microsoft's current documentation
  before quoting it.

## Unsubscribe and consent

- United States (FTC, "CAN-SPAM Act: A Compliance Guide for Business"): a valid physical postal address; an opt-out that
  works for at least 30 days after the send; requests honoured within 10 business days; no fee, no personal details beyond
  the address, and no step beyond a reply or one web page visit. The guide is marked as more than a year old; check it
  before quoting.
- United Kingdom (ICO guidance on electronic mail marketing and PECR): marketing to a customer without fresh consent relies
  on the soft opt-in, only for similar products, and only if a simple opt-out was offered when the details were collected
  and in every message. An opt-out buried in a privacy policy, or first raised in an order confirmation, does not count.
  Keep a do-not-contact record rather than deleting the address. Corporate subscribers are outside the consent rules, but
  a valid address for opt-outs still applies. In January 2026 the ICO fined a company 225,000 pounds for nuisance marketing
  where it could not rely on the soft opt-in.

## Opens are not a metric

- Apple Mail Privacy Protection, from iOS 15 and macOS Monterey (September 2021), downloads remote content, including
  tracking pixels, whether or not the person opens the email. Opens therefore count some machines. Clicks and replies are
  not affected. Most sources on this are vendor documents (Apple support and legal pages; Mailchimp, "Apple Mail Privacy
  Protection (MPP) FAQs").
- Practice: judge subject-line and content tests on clicks or replies. Use opens as a rough trend for the same list only.
  Watch unsubscribes and complaints as guardrails.

## Metric ladder

- Delivered, then clicks, then replies, then the action the email asks for, then revenue. Argue from the top of the
  ladder that the list can reach (practice).
- Published open-rate benchmarks disagree widely for the same year. Use the team's own baseline from its last three
  comparable sends. Quote a vendor benchmark only with its year and sample (practice).

## A/B tests

- State before the send: the hypothesis, the one variable, the primary metric, the smallest lift worth finding, the sample
  size and the send window. Do not stop early because one version leads.
- Sample size per variant, using Lehr's approximation (a standard rule of thumb, 1992): n is about 16 x p x (1 - p) divided
  by d squared, for 80 percent power at 5 percent two-sided significance. p is the baseline rate; d is the absolute
  difference to detect. Arithmetic example only: a 3 percent click rate and a 0.6 point gain need about 12,900 recipients
  per variant (16 x 0.03 x 0.97 / 0.006 squared, about 12,933).
- Vendor guidance varies. One platform suggests at least 5,000 subscribers per variant (Mailchimp, A/B testing guidance).
  Use the formula, not a floor.
- If the list cannot reach the sample, the test cannot be powered. Say so and propose a larger change, or a list that can
  be reached. That is a valid answer.

## Testimonials, results and guarantees

- The US FTC Consumer Reviews and Testimonials Rule (16 CFR Part 465) took effect on 21 October 2024. It bans fake reviews
  and testimonials, including ones written by AI for people who do not exist; rewards that depend on a positive review;
  and insider reviews without a disclosed tie to the business. Civil penalties apply to knowing violations, and the FTC
  gave a figure of $53,088 per violation in late 2025. Check the current figure before quoting it (FTC press release,
  August 2024; FTC questions and answers; FTC warning letters, December 2025).
- Practice: each quote or result has its file, date and permission before it is mailed. A guarantee keeps its terms in the
  same paragraph.

## Sequences

- A sequence has a stated stop event (a purchase, a reply, an unsubscribe, a booking). Each email has one job and one
  call to action (practice).
- The riskiest sequence is the one sent to people who never asked for it. Start from the promise each segment signed up
  under (practice).

## Sources

- Google, "Email sender guidelines" and "Email sender guidelines FAQ" (Gmail and Workspace Help), via search excerpts.
- Yahoo Sender Hub, best practices and complaint feedback loop, via search excerpts.
- Microsoft Defender for Office 365 blog, "Strengthening Email Ecosystem: Outlook's New Requirements for High-Volume
  Senders" (2025), via search excerpts. No 2026 confirmation found.
- Apple, Mail Privacy Protection support and legal pages; Mailchimp, "Apple Mail Privacy Protection (MPP) FAQs", via
  search excerpts.
- FTC, "CAN-SPAM Act: A Compliance Guide for Business", via search excerpts.
- ICO, "Electronic mail marketing" and "Respect people's preferences" guidance, and the ICO news item on fines (January
  2026), via search excerpts.
- FTC, "Federal Trade Commission Announces Final Rule Banning Fake Reviews and Testimonials" (August 2024), "The Consumer
  Reviews and Testimonials Rule: Questions and Answers", and the FTC warning letters (December 2025), via search excerpts.
- Lehr, R. (1992), "Sixteen S-squared over D-squared", Statistics in Medicine. The sample-size approximation.
- Mailchimp and Klaviyo A/B testing guides, via search excerpts. Used for the 5,000-subscriber floor only; no benchmark
  figures taken.
