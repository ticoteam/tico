# Sources

All read on 2026-11-18 unless a line says otherwise.

## Authorities and industry bodies

- US Small Business Administration, Get business insurance: the main policy types, and the steps (assess risks, use a
  reputable licensed agent, shop around). https://www.sba.gov/business-guide/launch-your-business/get-business-insurance
- Insurance Information Institute (Triple-I), Insuring Your Business: Small Business Owners' Guide to Insurance,
  liability chapter: occurrence and claims-made, products and completed operations, professional liability, D&O,
  employment practices, what an umbrella leaves out.
  https://www.iii.org/publication/insuring-your-business-small-business-owners-guide-to-insurance/specific-coverages/liability-insurance
- Triple-I, same guide, workers' compensation chapter: state rules, Texas, small-employer thresholds, states listed
  on the declarations page, uninsured contractors, premium from payroll and class.
  https://www.iii.org/publication/insuring-your-business-small-business-owners-guide-to-insurance/specific-coverages/workers-compensation-insurance
- Federal Trade Commission, Cyber insurance (small business cybersecurity guidance, developed with the NAIC;
  undated). https://www.ftc.gov/business-guidance/small-businesses/cybersecurity/cyber-insurance
- US Consumer Product Safety Commission, Duty to Report to the CPSC: who must report, 24 hours, 10 working days.
  https://www.cpsc.gov/Business--Manufacturing/Recall-Guidance/Duty-to-Report-to-the-CPSC-Your-Rights-and-Responsibilities
- SBA, Hire and manage employees: workers' compensation through a carrier, self-insurance or the state programme.
  https://www.sba.gov/business-guide/manage-your-business/hire-manage-employees

## Secondary (leads only)

- ACORD 25 wording ("confers no rights upon the certificate holder") and the need for an endorsement to create
  additional insured status: law-firm and agents' association articles found by search (Miller Nash; Independent
  Insurance Agents and Brokers of America). The form itself was not read.
- Claim handling (contact the insurer or agent as soon as possible, document, proof of loss) and tail cover for
  claims-made policies: Triple-I and state regulator consumer pages found by search.
- Texas Department of Insurance history page, confirming Texas as the state where cover is optional.

## Public agent skills

- None found that covers business insurance. Anthropic's knowledge-work-plugins (Apache-2.0), claude-for-legal
  (Apache-2.0) and financial-services (Apache-2.0) were searched on 2026-11-18; none has an insurance programme,
  certificate or claims skill. The method here is built from the human guides above and from Tico's own narrow
  skills. claude-for-legal has a `renewal-tracker` for contracts; it was not used for text here.

## Tico (used as sub-steps)

- `templates/catalog/operations-manager/skills/check-a-renewal-deadline` ("not found in the document read", one
  owner, a first check date) and `chase-a-quiet-vendor`.
- `templates/catalog/contracts-manager/skills/check-quotes-against-the-current-text`.
- `templates/catalog/privacy-manager/skills/log-breach-facts-for-counsel`.
- `templates/catalog/logistics-coordinator/skills/check-a-claim-before-the-deadline`.
- `templates/catalog/_shared/.claude/skills/write-a-counsel-brief`.
- `docs/finance-tools.md`: `contact_update` with `renewal_on`, and the `finance.vendor.renewal_due` event.

## Gaps (not verified; the skill words these as "check")

- The list of states that sell workers' compensation only through a state fund is from general knowledge.
- NFIB's state-by-state workers' compensation page refused the fetch. No state agency page was read.
- No source was read on how carriers treat work done by AI agents. The skill tells the bot to ask the broker in
  writing.
- The renewal day counts, the 15 percent like-for-like limit and the "one month of operating expenses" retention
  default are this skill's working defaults, not figures from a source.
- Typical limits are deliberately not given. No reliable public benchmark was read.
