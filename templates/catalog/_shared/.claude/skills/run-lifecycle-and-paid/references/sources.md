# Sources

Read 2026-11-18 unless a line says otherwise. All text in this skill is original. Sender rules and ad platform
thresholds change: anything more than 90 days old should be opened again before you rely on it.

## Mail: provider rules and law

- Google, "Email sender guidelines" (support.google.com/a/answer/81126). All senders: SPF or DKIM, TLS, valid forward
  and reverse DNS, spam rate under 0.3%. Bulk senders (more than 5,000 messages a day to Gmail): SPF and DKIM and
  DMARC (policy may be `none`), From-domain alignment, one-click unsubscribe on marketing and subscribed mail
  (`List-Unsubscribe` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, RFC 8058), keep the spam rate under
  0.10% and avoid 0.30% or more. In force since 2024-02-01. Rejections carry 5.7.26.
- Yahoo, sender best practices (senders.yahooinc.com/best-practices): the same authentication and one-click
  unsubscribe rules for bulk senders, a spam rate under 0.3%, unsubscribes honoured within two days. No published
  volume threshold.
- Microsoft, Outlook.com requirements for high-volume senders: SPF, DKIM and DMARC for domains sending more than
  5,000 messages a day, enforced from 2025-05-05 (rejections 550 5.7.515).
- US: CAN-SPAM Act and the FTC's compliance guide. No deceptive headers or subjects, a postal address, a working
  opt-out for 30 days after the send, opt-outs honoured within 10 business days.
- US: FTC rule on consumer reviews and testimonials, 16 CFR Part 465, effective 2024-10-21.
- UK: Privacy and Electronic Communications Regulations (consent, with the "soft opt-in" for a company's own
  customers). EU: ePrivacy Directive Article 13 as each country applied it. Canada: CASL. Summarised only: check
  locally before mailing outside the US.
- Apple Mail Privacy Protection (since 2021) fetches message content for the user, which inflates open counts.

## Paid

- coreyhaines31, `marketingskills` (MIT), `ads`. Ideas adapted: the weekly check order (spend, traffic, conversions,
  creative, landing page, tracking); one-word verdicts HOLD, FIX, TEST, INVESTIGATE with INVESTIGATE overriding;
  marking unavailable inputs MISSING; break-even cost from customer value; about 70/30 proven to test; raising
  budgets about 20% at a time; brand and non-brand separate; excluding customers.
- Meta's learning phase (about 50 optimisation events per ad set in 7 days) is from third-party summaries of Meta
  Business Help. Not confirmed on Meta's own page today.
- Blake, Nosko and Tadelis, "Consumer Heterogeneity and Paid Search Effectiveness: A Large-Scale Field Experiment",
  Econometrica, 2015. eBay's experiments found brand-keyword ads added almost nothing for a well-known brand. The
  basis for testing brand spend instead of assuming it works. See also `DISAGREEMENTS.md` in the marketing folder.
- Tico's own `templates/catalog/search-ads-owner/skills/choose-a-bidding-strategy-from-volume` for the conversion
  volumes that bidding strategies need.

## Tests

- Evan Miller, "How Not To Run an A/B Test" (2010-04-18). The peeking problem and the 26.1% example.
- The sample-size formula is the standard two-proportion formula with z values 1.96 and 0.84 (their sum squared is
  7.849). The quick check (16 x p x (1 - p) / d^2) is Lehr's rule of thumb. The table in `testing.md` was computed
  from the formula on 2026-11-18.
- The split check is a one-degree-of-freedom chi-square test; 10.83 is its value at a probability of 0.001. Kohavi,
  Tang and Xu, "Trustworthy Online Controlled Experiments" (2020), is the standard reference for sample ratio
  mismatch and for not slicing after the fact.
- coreyhaines31, `marketingskills` (MIT), `cro`. Idea adapted: review a page in impact order (value proposition,
  headline, call to action, hierarchy, trust, objections, friction) before testing anything.
- coreyhaines31, `marketingskills` (MIT), `emails`. Idea adapted: a sequence spec (trigger, goal, timing, exits,
  metrics) written before any copy; one email, one job.

## Tico's own skills

- `templates/catalog/email-owner/skills/design-an-ab-test` (folded in and replaced): one variable; judge by clicks or
  replies, never opens; say when the list cannot power the test.
- `templates/catalog/marketing-operations-manager/skills/plan-a-test-before-it-starts` (rules repeated here; the
  skill stays): do not test when every result leads to the same change; fix or test; a counter-metric; launch to a
  small share and check the split for two days; tests past 30 days with no end date are retired.
- `templates/catalog/email-owner/knowledge/email-practice.md` and `channel-playbook.md`; `docs/growth.md` (Growth
  Experiments, result labels, channel records).

## Written new for this skill

- The state map tied to programmes, and the order to build them in.
- The deliverability checklist. No skill library we read covers it.
- The fair-run default (three times the target cost, or 30 conversions) and the default frequency cap. These are
  defaults for a starting point, not published standards.

## Check before quoting

- Gmail, Yahoo and Outlook thresholds and error codes.
- Any ad platform threshold or policy.
- Consent rules outside the US.
