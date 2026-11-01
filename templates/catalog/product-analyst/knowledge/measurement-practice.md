# Measurement practice

Standing reference for how experiments, metrics and event counts are checked. Facts about this product's own events and definitions live in `definitions.md` and `launches.md`; this file holds general practice and dated sources. Where a source and this team's data disagree, the team's data wins for this team, and the disagreement is noted.

## Experiments

- Check the split before the result. A sample ratio mismatch, a split that differs from the planned one by more than chance allows, means assignment is broken and the result cannot be trusted. Kohavi, Tang and Xu (2020, chapter 21) treat it as a trust check that comes before any metric. In a 2019 study of large online experiments, Fabijan et al. found a mismatch in roughly 6% of them (secondary figure; check before quoting).
- Looking early inflates false wins. Reading a fixed-horizon test every day and stopping at the first p-value under 0.05 produces far more false positives than 5%. Johari, Pekelis and Walsh (2015, arXiv:1512.04922; talk "Peeking at A/B Tests", KDD 2017) describe always-valid p-values for continuous monitoring. Use them only when the tool implements them.
- Report an interval, not a p-value alone. The interval shows the range of effects still plausible.

## Product-market signals

- A "very disappointed" answer from 40% or more of users is a folk benchmark for product-market fit (Sean Ellis, around 2009). The wording and sample sizes behind it vary, and its author has called the threshold arbitrary. Use it as a prompt for a question, not as a test.

## Event counts

- A rate needs a denominator. A count at one moment does not become a rate by being divided by something else.
- A proxy (a similar event, a field with a related name) is named as a proxy, with what it can and cannot show.
- A drop to zero in an event count is usually an instrumentation or ingest fault until another source shows otherwise (general practice, derived from the checks in the skills).

## Sources
- 2020: Kohavi, R., Tang, D. and Xu, Y., Trustworthy Online Controlled Experiments, Cambridge University Press, chapter 21 "Sample ratio mismatch and other trust-related guardrail metrics".
- 2019: Fabijan et al., "Diagnosing Sample Ratio Mismatch in Online Controlled Experiments", KDD 2019 (secondary figure).
- 2015 (arXiv) and 2017 (KDD talk): Johari, Pekelis and Walsh, "Always Valid Inference", arXiv:1512.04922.
- Around 2009: Ellis, S., product-market fit survey question (secondary; no primary publication cited).
