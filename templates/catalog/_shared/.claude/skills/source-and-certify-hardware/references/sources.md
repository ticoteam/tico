# Sources

What this skill drew from. Ideas only; all wording is original. Every regulatory fact was written on 2026-11-18 and is
marked in `certification-map.md` as checked on the web that day ([W]) or from general knowledge ([K]). Most web checks
used secondary sources because the FCC's own pages refused automated reads. Re-read at the primary source before use.

## Skills and handbooks

- aklofas/kicad-happy (MIT): distributor sourcing skills and a pre-compliance EMC skill. Ideas adapted: read live
  distributor data and date it; pre-compliance is not certification.
- Platanor/hardware-compliance-handbook (CC BY 4.0): an agent-readable reference to the EU Cyber Resilience Act and the
  Radio Equipment Directive's cybersecurity act. Used to find which EU rules to list; dates were checked separately.
  No text reproduced.
- Cleo-Labs-IA/skills_library, electronics-compliance (MIT): a core "what must a seller do" engine with a module per
  market. Idea adapted: map by market and by what is inside the product.
- Tico catalog skills kept as sub-steps: see `../../MERGES.md`.

## Sourcing and cost

- Should-cost analysis and total-cost-of-ownership comparison are standard procurement practice (for example as
  taught by the Institute for Supply Management and described in Boothroyd, Dewhurst and Knight, *Product Design for
  Manufacture and Assembly*, third edition, 2010). The cost build-up here is a plain version written for this skill.
- International Chamber of Commerce, Incoterms 2020: the delivery terms. Summarised from general knowledge; the rule
  text was not read.
- Weighted supplier scoring with kill criteria is common practice; the factors, weights and anchors here are this
  skill's defaults, not taken from a source.

## Quality

- W. Edwards Deming, *Out of the Crisis* (1986): the all-or-none inspection rule and the fourth of his fourteen
  points (a single supplier per item, long term). Nicholas Squeglia, *Zero Acceptance Number Sampling Plans*. Jeffrey
  Liker, *The Toyota Way* (2004). Cited in `../../DISAGREEMENTS.md` from general knowledge; not re-read.
- ANSI/ASQ Z1.4, *Sampling Procedures and Tables for Inspection by Attributes*, and ISO 2859-1: the sampling scheme,
  code letters, accept and reject numbers and switching rules. The tables in `incoming-qc-aql.md` are an extract from
  memory. One row (code letter L: sample 200; accept 10 at AQL 2.5, accept 14 at AQL 4.0) was confirmed on 2026-11-18
  against a chart published by a quality-inspection firm. The standards themselves are paid documents and were not
  read.
- First-article inspection and golden samples: common manufacturing practice (the aerospace form of first-article
  inspection is standard AS9102; not used here).

## United States regulation

- 47 CFR Part 15 and Part 2 (eCFR): equipment authorization, SDoC, certification, modular approval (15.212),
  applicant attestations (2.911).
- FCC Covered List (fcc.gov/supplychain/coveredlist): addition of foreign-produced consumer routers, 23 March 2026;
  later conditional approvals and exemption notices (DA 26-390, DA 26-542, DA 26-957), per law-firm alerts.
- FCC "bad labs" proceeding: Report and Order adopted May 2025; recognition withdrawals from
  8 September 2025; further order reported in a Wiley alert dated 17 November 2026.
- OSHA Nationally Recognized Testing Laboratory programme (29 CFR 1910.7) and its current list of NRTLs.
- UL 62368-1; UL 2054; IEC 62133-2; UN Manual of Tests and Criteria, section 38.3: named only.
- Reese's Law and 16 CFR Part 1263; Consumer Product Safety Improvement Act: named from general knowledge.
- Tariffs: US Supreme Court decision of 20 February 2026 striking down tariffs under the International Emergency
  Economic Powers Act; Section 122 surcharge, 24 February to 24 July 2026; Sections 232 and 301 measures continuing.
  From news and law-firm summaries.

## European Union regulation

- Directive 2014/53/EU (RED); Delegated Regulation (EU) 2022/30 and its application date of 1 August 2025; EN 18031
  series cited 30 January 2025 with restrictions.
- Directive (EU) 2022/2380 (common charger).
- Directives 2014/30/EU (EMC), 2014/35/EU (LVD), 2011/65/EU (RoHS), 2012/19/EU (WEEE); Regulation (EC) 1907/2006
  (REACH).
- Regulation (EU) 2023/988 (GPSR), applying from 13 December 2024.
- Regulation (EU) 2019/1020 (market surveillance), Article 4.
- Regulation (EU) 2024/2847 (Cyber Resilience Act): reporting from 11 September 2026; main duties from 11 December
  2027.
- Regulation (EU) 2023/1542 (batteries) and Regulation (EU) 2025/1561 (postponing due-diligence duties to 18 August
  2027).
- Regulation (EU) 2025/40 (packaging): named only; dates not verified.

## United Kingdom

- Product Security and Telecommunications Infrastructure Act 2022 and its 2023 regulations, in force 29 April 2024.
- Continued recognition of CE marking in Great Britain: from general knowledge; check gov.uk.

## Not verified

- Any line marked [K] in the certification map.
- The FCC's own text of the 2026 Covered List actions and the latest lab order.
- Whether the FCC has adopted limits on listed components or modules inside otherwise unlisted products.
- The current US tariff position after 24 July 2026.
- AQL table cells other than code letter L.
