# Default playbook

Starting positions for a team that has not written its own. They are ordinary commercial positions for a small or
mid-sized United States company, written from general practice. No benchmark or survey is claimed for any figure. They
are choices, not law, and a lawyer has not approved them for this company.

How the bot uses this file: when `knowledge/playbook.md` does not exist, copy everything below the line into it under
the heading "Default positions, not yet adopted (copied YYYY-MM-DD)". Review against it, and say "default, not yet
adopted" on every review until the signer adopts or edits the positions. The signer can adopt it in one message; record
the date and remove the label.

How to read a row: **Preferred** is what to ask for and is rated green when the text matches. **Fallback** may be
accepted without asking and is rated yellow. Anything worse than the fallback is red: it needs a change and only the
signer can accept it. **Walk-away** lists the terms on which the advice is not to sign as written. "Wording" is the
only text the bot may insert for that line; where it says "none", a change needs counsel.

---

## Header (fill in at first use)

- Signer: [name]. Decides outside-playbook points: [name].
- Company legal name and home state: [from `knowledge/company.md`].
- Contracts lawyer: [name or "none"].
- Insurance carried: [policies and limits, or "not known"].
- Accepted once: [dated lines added after each deviation the signer accepts].

## N. Non-disclosure agreements

These apply when the team has no `knowledge/standard-nda.md`. Where it has one, the standard and
`knowledge/variations.md` come first.

| Line | Clause | Preferred | Fallback | Walk-away |
|---|---|---|---|---|
| N1 | Direction | Mutual whenever both sides will share. One-way in the company's favor when only the company shares. | One-way toward the company when only the other side shares. | One-way against the company when the company will share. |
| N2 | Definition and exclusions | Information marked confidential or that a reasonable person would understand to be confidential. Five exclusions: already known; independently developed; lawfully received from a third party; public through no fault of the recipient; disclosure required by law, with notice where lawful. | A marking requirement, with oral disclosures confirmed in writing within 30 days, when the company is mainly the recipient. | Any exclusion missing. "All information" with no exclusions. A marking requirement when the company is the main discloser. |
| N3 | Use and recipients | Use only for the stated purpose. Share only with employees, contractors and professional advisers who need to know and are bound by duties at least as strict. The recipient answers for them. | Affiliates added as recipients on the same terms. | Sharing with any third party at the recipient's discretion. A purpose so wide it covers general business use. |
| N4 | Period | Agreement term of 1 to 2 years. Duty of confidence for 3 years from each disclosure. Trade secrets protected for as long as they remain trade secrets. | A duty of 2 to 5 years. | A duty under 2 years when the company discloses. A perpetual duty for ordinary information when the company receives. |
| N5 | Residuals | No residuals clause. | None. | Any right to use information retained in memory. |
| N6 | Extras | None: no non-solicit, non-compete, exclusivity, standstill, licence of intellectual property, audit right or indemnity. A statement that no licence is granted and neither side must proceed with a deal. | None. | Any of them. |
| N7 | Return, remedies | Return or destroy on request, with a copy kept where law or routine backup requires, still under the duty. Either side may seek an injunction. | Written certificate of destruction on request. | Liquidated damages. Fee-shifting for one side only. A waiver of the need to prove harm for one side only. |
| N8 | Law, forum, assignment | The company's home state law and courts. No assignment without consent, except to a successor in a merger or sale. | The other side's United States state, or Delaware or New York. | Law or forum outside the United States. |
| N9 | Individuals | When the other party is an individual employee or contractor, the agreement carries the trade-secret whistleblower notice (see `knowledge/nda-practice.md` where the paralegal role has it). | None. | Missing notice: needs counsel. |

Wording for N lines: the team's standard NDA when it exists. Otherwise "none"; the bot strikes words and never drafts
NDA clauses.

## B. The company is buying (services, software, vendors)

| Line | Clause | Preferred | Fallback | Walk-away |
|---|---|---|---|---|
| B1 | Limit of liability | A cap for each side of no less than the fees paid and payable in the 12 months before the claim. Outside the cap: the vendor's breach of confidentiality and data duties, its indemnities, and deliberate or grossly negligent acts. | The same 12-month cap with a higher, stated cap (a multiple of fees) for data and confidentiality breaches instead of no cap. | A cap under 12 months' fees. A cap that also covers the vendor's indemnity for intellectual property claims. A cap on the vendor's liability with none on the company's. |
| B2 | Indemnity | The vendor defends and pays third-party claims that its product infringes intellectual property, and claims caused by its breach of law or of its data duties. The company indemnifies only for content it supplies. | The intellectual property indemnity alone. | The company indemnifying for "any breach", for the vendor's own fault, or without a cap. |
| B3 | Intellectual property | The company owns its data and anything made specifically for it and paid for. The vendor keeps its existing tools and gives a licence to use them as part of the deliverables. | A perpetual, paid-up licence to deliverables for the company's business in place of ownership. | The vendor owning the company's data or works derived from it. |
| B4 | Data and security | Where the vendor handles personal data: a data processing agreement (reviewed with `review-a-dpa-against-the-position`), stated security measures, notice of a breach without undue delay, notice of new subprocessors, deletion or return at exit. No use of the company's data except to provide the service. | Breach notice within 72 hours. Use of aggregated data that cannot identify the company or any person, for service analytics. | Use of the company's data to train models or build the vendor's products. Sale of data. No breach notice duty. |
| B5 | Confidentiality | Mutual, on the terms of N2 to N4. | The vendor's form if it has the five exclusions and a duty of at least 3 years. | One-way against the company. |
| B6 | Warranties | The service matches its documentation, is performed with reasonable skill, complies with law and carries no malicious code. Remedy: fix, redo or refund. | Warranty limited to conformity with the documentation, with a refund remedy. | A paid service provided "as is" with no remedy. |
| B7 | Term and exit | Initial term of 12 months or less. Renewal only if the company does not give notice at least 30 days before the term ends. Either side may end for material breach not cured in 30 days. Prepaid fees for the unused period are refunded if the company ends for the vendor's breach. 30 days after the end to export data. | Renewal notice of up to 60 days. | Renewal notice of more than 60 days. A term longer than 12 months with no right to leave. No refund on the vendor's breach. |
| B8 | Law and disputes | The company's home state law and courts. | The vendor's United States state, or Delaware or New York. Arbitration in the United States under a named provider's rules. | Law or forum outside the United States. One-sided fee-shifting. |
| B9 | Insurance | The vendor carries general liability insurance, and cyber or errors-and-omissions cover where it handles data or gives advice, and shows a certificate on request. No minimum limits are set in this default; record the limits shown. | A written statement of cover in place of a certificate. | No insurance where the vendor enters premises or handles personal data. |
| B10 | Assignment | No assignment without consent, not unreasonably withheld, except to an affiliate or a successor in a merger or sale. | Free assignment to a successor with notice. | The vendor's right to end or re-price the contract if the company changes ownership. |
| B11 | Force majeure | Applies to both sides. Either may end the contract if it lasts more than 30 days, with a refund of prepaid fees. | A period of up to 60 days. | Applies to the vendor only. No right to end. |
| B12 | Price and payment | Fees fixed for the term. Payment 30 days from a correct invoice. Any increase at renewal needs written notice at least 60 days before, and a stated cap. Disputed amounts may be withheld in good faith. | Payment in 15 days. Annual payment in advance where B7 gives a refund right. | Uncapped increases during the term. Late fees above 1.5% a month. Suspension of service without notice. |

Wording for B lines: the numbers and words in each "Preferred" and "Fallback" cell may be inserted as they stand to
replace a number, a period or a short phrase in the vendor's text (for example striking "ninety (90)" and inserting
"thirty (30)" under B7). For a clause that is missing altogether, the bot may propose the last sentence of the cell
as a plain-language term sheet point, marked "to be drafted by the party whose paper this is". It does not write the
clause.

## S. The company is selling

| Line | Clause | Preferred | Fallback | Walk-away |
|---|---|---|---|---|
| S1 | Limit of liability | A cap for each side at the fees paid in the 12 months before the claim. No liability for indirect or consequential loss, or lost profits. Outside the cap: the customer's duty to pay, each side's indemnities, and deliberate misconduct. | A higher, stated cap of up to 24 months' fees for data and confidentiality breaches. | Unlimited liability for anything except deliberate misconduct or what the law forbids limiting. A general cap above 24 months' fees. |
| S2 | Indemnity | The company defends third-party claims that the service, as delivered, infringes intellectual property, with the usual exclusions (the customer's materials, changes the company did not make, combinations it did not supply) and the remedies of modifying, replacing or refunding. The customer indemnifies for its data and its misuse of the service. | An added indemnity for the company's breach of data protection law, inside the S1 higher cap. | Indemnity for "any breach". Indemnity for the customer's own fault. First-party indemnities. |
| S3 | Intellectual property | The company keeps the service and every improvement to it. The customer keeps its data. The company may use feedback freely. | Assignment of deliverables made only for that customer and paid for, with a licence back to the know-how. | Assignment of the service or of existing intellectual property. "Work made for hire" covering the core product. Source-code escrow. |
| S4 | Data and security | The company's own data processing agreement and its published security measures. Breach notice without undue delay. | The customer's data processing agreement if `review-a-dpa-against-the-position` rates it inside the team's position. Notice within 72 hours. | Security promises the company does not meet today. Audits on the company's premises. Notice periods under 48 hours. |
| S5 | Confidentiality | Mutual, on the terms of N2 to N4. | A duty of up to 5 years. | One-way against the company. |
| S6 | Warranties | The service performs materially as its documentation says. Remedy: fix, or refund for the affected period. All other warranties disclaimed. | A warranty of reasonable skill and care for services. | A warranty of fitness for the customer's purpose. A promise of results. A warranty with no limit on remedy. |
| S7 | Term and exit | 12-month term renewing for 12 months unless either side gives 30 days' notice. Either side may end for material breach not cured in 30 days. | Renewal notice of 60 days. Monthly terms. | The customer's right to end for convenience with a refund. |
| S8 | Law and disputes | The company's home state law and courts. | The customer's United States state, or Delaware or New York. | Law or forum outside the United States. |
| S9 | Insurance | Only the cover the company actually carries, at its actual limits (see Header). | A certificate on request. | Limits above what the company holds. Naming the customer as an additional insured. |
| S10 | Assignment | No assignment without consent, except to an affiliate or a successor in a merger or sale. | Consent not unreasonably withheld. | The customer's right to end the contract if the company changes ownership. |
| S11 | Force majeure | Applies to both sides. Does not excuse payment for service already delivered. | A right to end after 60 days. | Applies to the customer only. |
| S12 | Price and payment | Payment 30 days from invoice. Annual plans paid in advance. The company may change the price at renewal with 60 days' notice. Late amounts carry interest. | Payment in 45 or 60 days. | Payment in 90 days or more. Payment only when the customer is paid by its own client. Most-favored-customer pricing. A right to set off. |

Wording for S lines: the company's own template is the source for inserts. Where the company has no template, the
same rule as for B lines applies.

## Statements of work and order forms

Review against the governing agreement, not against this playbook. Flag any term in the statement of work or order
form that changes the governing agreement (liability, intellectual property, payment, term), and check the order of
precedence clause to see which one wins.

## Online terms that cannot be negotiated

Check only: B1 (is the vendor's liability effectively zero), B3 and B4 (who owns and may use the company's data), B7
(renewal and the last day to cancel), B12 (price changes), and the vendor's right to change the terms alone. Report
"accept", "accept with these risks noted" or "do not accept".
