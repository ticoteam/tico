# Certification map: United States, European Union, safety listing

A starting map for an electronic product. **Everything on this page was written on 2026-11-18.** Each line is marked:

- **[W]** checked on the web on 2026-11-18, mostly from law-firm, test-lab and trade summaries rather than the
  regulator's own page;
- **[K]** from general knowledge on 2026-11-18, not re-checked that day.

Neither mark makes a line safe to rely on. Before a design decision, a lab booking or a declaration, read the rule at
the source named in "Verify at" and write the date read into `knowledge/hardware-compliance.md`. Rules in this area
changed several times in 2025 and 2026.

This map covers common consumer and commercial electronics. It does not cover medical devices, vehicles, aviation,
toys in detail, machinery, explosive atmospheres, or products that measure for trade. If the product is one of those,
stop and ask a compliance engineer.

## How to use it

1. From the settings, list the markets and what is inside the product: radio, battery, mains power, child use, skin or
   food contact, software that connects to a network.
2. Take every row below whose "applies when" is true.
3. Verify each at its source. Write, per row: applies or not and why, the route, the standards and editions the lab
   will test to, who signs, what goes on the product, box and manual, the date read and the link.
4. Give the result to the compliance engineer or the test lab to confirm before spending.

## United States

| Rule | Applies when | Route | Dated note | Verify at |
|------|--------------|-------|-----------|-----------|
| FCC Part 15, unintentional radiators (47 CFR Part 15 Subpart B) | Almost any product with digital electronics | Supplier's Declaration of Conformity (SDoC): test at a suitable lab, keep the report, name a responsible party located in the US, put the compliance statement in the manual. No filing with the FCC. Class B limits for home use, Class A for commercial only | [K] | eCFR, Title 47 Part 15 and Part 2 Subpart J |
| FCC Part 15, intentional radiators (Subparts C and E) | The product transmits: Bluetooth, Wi-Fi, other radios | Certification: test at an FCC-recognised accredited lab; a Telecommunication Certification Body (TCB) issues the grant; FCC ID on the label | [K] | eCFR Part 15; FCC Equipment Authorization pages |
| Certified radio modules (47 CFR 15.212) | The radio is a module that already has its own FCC ID | The host can rely on the module's grant only if every condition in the grant is met: the antenna type and gain, no unapproved co-location with another transmitter, RF exposure conditions. The host label says "Contains FCC ID: ...". The finished product still needs its own unintentional-radiator testing and SDoC. Changes outside the conditions need a permissive change or a new certification | [K] | The module's grant on the FCC ID search; the module maker's integration guide |
| Applicant attestations (47 CFR 2.911) | Any certification application | The applicant certifies the equipment is not "covered" equipment, and names an agent in the US for service of process | [W] | eCFR 47 CFR 2.911 |
| The FCC Covered List | Any product, part or maker with ties to listed entities or categories | Listed equipment cannot get a new authorization. The list has grown: on 23 March 2026 consumer routers produced abroad were added, with later conditional approvals and exemption notices. Check the maker of the product and of its radio parts against the list | [W] | fcc.gov/supplychain/coveredlist |
| Test labs and certification bodies | Choosing a lab | The FCC adopted rules in May 2025 barring labs, TCBs and accreditation bodies owned or controlled by foreign adversaries, began withdrawing recognition from some labs on 8 September 2025, and later extended the rule to labs "subject to the direction of" a prohibited entity (reported 17 November 2026). A report from a lab that is not recognised is worthless for certification. Confirm the lab's current recognition on the day you book | [W] | FCC list of recognised test firms and TCBs |
| Safety listing (UL, ETL, CSA, TÜV and other NRTL marks) | Mains-powered or battery products; anything a large retailer or marketplace will stock; workplace equipment | Not a federal legal requirement for most consumer electronics. OSHA requires listing by a Nationally Recognized Testing Laboratory (NRTL) for much equipment used in workplaces; retailers, marketplaces, insurers and electrical codes require it in practice. Any NRTL's mark for the right standard is equivalent. For audio, video, IT and communication equipment the standard is UL 62368-1, which replaced UL 60950-1 and UL 60065. The listing includes factory follow-up inspections | [W] for the legal position; [K] for the standards | OSHA's current NRTL list; the retailer's supplier requirements; the lab |
| Lithium batteries | The product contains or ships with one | Transport: the cell or pack needs UN 38.3 testing and a test summary from its maker before it can ship by air, sea or road. Safety: commonly UL 2054 or IEC/UL 62133-2 for the pack, as the end-product standard requires. Buying a pack that already has these reports saves a test programme | [K] | The battery maker's reports; the carrier's and IATA's current rules; the lab |
| Button and coin cells | The product uses them | Federal rule under Reese's Law (16 CFR Part 1263): secured battery compartment, warnings on product and packaging, tested to the named standard | [K] | cpsc.gov |
| Children's products | Designed or intended mainly for children 12 or under | Consumer Product Safety Improvement Act: third-party testing at a CPSC-accepted lab, a Children's Product Certificate, tracking label, limits on lead and phthalates. Get a specialist | [K] | cpsc.gov |
| State rules | Sold in the state | California Proposition 65 warnings; state energy-efficiency rules for chargers and power supplies; state battery and e-waste programmes | [K] | The state agency's page |
| Federal energy rules | External power supplies, battery chargers | Department of Energy efficiency standards; buying a compliant supply is the usual route | [K] | energy.gov; the supply maker's declaration |

## European Union

CE marking is the maker's own declaration that the product meets every EU rule that applies. There is no single "CE
certificate". The maker draws up technical documentation, signs an EU Declaration of Conformity listing each law and
standard, and affixes the mark. Keep the documentation for ten years after the product is placed on the market [K].

| Rule | Applies when | Route | Dated note | Verify at |
|------|--------------|-------|-----------|-----------|
| Radio Equipment Directive 2014/53/EU (RED) | Any radio | Covers radio performance, and also safety and EMC for radio products (so the two rows below are met through RED). Self-declaration when harmonised standards are applied in full; otherwise a notified body examines the design. A module's own compliance does not transfer to the finished product automatically; the product maker declares for the whole product | [K] | EUR-Lex; the Commission's RED page and list of harmonised standards |
| RED cybersecurity: Delegated Regulation (EU) 2022/30 | Internet-connected radio equipment; radio equipment handling personal data; radio equipment enabling money transfers | Applies since 1 August 2025. Standards EN 18031-1, -2 and -3 were cited in the Official Journal on 30 January 2025 with restrictions; where a restriction bites, or the standards are not applied in full, a notified body is needed | [W] | EUR-Lex; Commission RED page |
| Common charger (Directive (EU) 2022/2380, amending RED) | Rechargeable handheld devices in the listed categories | USB-C charging required for the listed categories from 28 December 2024, and for laptops from 28 April 2026; labelling about charger inclusion and charging power | [K] | EUR-Lex; Commission common-charger page |
| EMC Directive 2014/30/EU | Electronics without a radio | Self-declaration with testing to harmonised standards | [K] | EUR-Lex |
| Low Voltage Directive 2014/35/EU | Products without a radio rated 50 to 1,000 V AC or 75 to 1,500 V DC | Self-declaration with safety testing; EN 62368-1 for audio, video and IT equipment | [K] | EUR-Lex |
| RoHS Directive 2011/65/EU | All electrical and electronic equipment | Restricted substances under limits; supplier declarations and test data in the technical file; part of the CE declaration | [K] | EUR-Lex |
| REACH Regulation (EC) 1907/2006 | All articles | Tell customers about substances of very high concern above the threshold; notify the SCIP database. The candidate list is updated twice a year | [K] | echa.europa.eu |
| General Product Safety Regulation (EU) 2023/988 | Consumer products | Applies since 13 December 2024. A responsible economic operator established in the EU must be named, with contact details on the product or packaging; online listings need safety information; internal risk analysis in the technical file | [W] | EUR-Lex; Commission GPSR page |
| An economic operator in the EU (Regulation (EU) 2019/1020, Article 4) | CE-marked products from a maker outside the EU | An importer, authorised representative or fulfilment provider in the EU must be named and hold the declaration | [K] | EUR-Lex |
| Cyber Resilience Act, Regulation (EU) 2024/2847 | Products with digital elements: hardware or software that connects to a device or network | Reporting duties apply from 11 September 2026: actively exploited vulnerabilities and severe incidents are reported through the ENISA single reporting platform, with an early warning within 24 hours. Most other duties (security by design, vulnerability handling, documentation, CE marking under the Act) apply from 11 December 2027 | [W] | EUR-Lex; ENISA |
| Batteries Regulation (EU) 2023/1542 | Any battery, including those built into products | Phased duties: labelling and marking, restricted substances, CE marking for batteries. Portable batteries in products must be removable and replaceable by the end user from 18 February 2027. Supply-chain due-diligence duties were postponed to 18 August 2027 by Regulation (EU) 2025/1561 | [W] | EUR-Lex |
| WEEE Directive 2012/19/EU | Electrical equipment sold in a member state | Register as a producer in each country sold into, or appoint a representative; crossed-bin mark; pay into a take-back scheme | [K] | Each member state's register |
| Packaging | Any packaged product | Producer registration and fees per country. A new Packaging and Packaging Waste Regulation, (EU) 2025/40, replaces the old directive; its start date and duties were not verified here | [K] | EUR-Lex; national registers |
| Language and documentation | All | Instructions and safety information in the languages each member state requires; maker's and importer's name and postal address on the product or packaging | [K] | The directive's own text; national rules |

**United Kingdom** [K]: Great Britain has said it will keep recognising CE marking for most product categories,
alongside its own UKCA mark; check the current list of categories and any end date on gov.uk. Connected consumer products must meet the security requirements
of the Product Security and Telecommunications Infrastructure regime (in force since 29 April 2024): no universal
default passwords, a vulnerability-reporting contact, a stated minimum period of security updates, and a statement of
compliance.

**Other markets** (Canada ISED, Japan, Korea, Australia, and others) each have their own radio and safety approvals. A
test lab can often extend one test programme to cover several; ask for that in the quote.

## Ways to shrink the programme by design

| Choice | Effect | Watch for |
|--------|--------|-----------|
| A radio module with its own FCC grant and RED documentation | Avoids full radio certification of the transmitter in the US; simplifies the EU radio tests | Use only the antennas and layout the grant and integration guide allow; no second transmitter nearby without checking; the finished product is still tested |
| An external power supply that carries its own safety marks and efficiency compliance | Keeps mains voltage out of the product; narrows the safety evaluation | The supply's marks must cover every market; keep its certificates on file; the product's own low-voltage side is still assessed |
| A battery pack or cell with UN 38.3 and safety reports | Avoids a battery test programme | Reports must match the exact part number; the charger circuit is still assessed |
| Parts with materials declarations | The RoHS and REACH file builds itself | Collect at first article, not at the end |
| ESD protection, filtering and a solid ground return in the first layout | Fewer failed emissions and immunity runs | Pre-scan at the first prototype |

## The certification plan page

One page, built from lab quotes, never from estimates.

1. **Matrix.** Market by rule: applies, route, standards and editions, who signs, marks and manual text, date read,
   source link.
2. **Tests.** For each: the lab, its accreditation and (US radio) current FCC recognition, samples needed and their
   configuration, duration, quoted cost, retest cost.
3. **Timeline.** Backwards from the ship date: paperwork and labels, a retest allowance, lab time, sample build,
   pre-scan, design freeze. Each bar is a task.
4. **Documents.** The technical file list, the declaration drafts, label and manual text, the EU operator and US
   responsible party and agent.
5. **Open questions** for the compliance engineer or the lab.
6. **Watch list.** Future dates that will change the plan, each on the calendar.

## Words to use carefully

- **Certified** (US radio): only when a TCB grant exists for this product's FCC ID.
- **FCC compliant** (SDoC): only when the test report exists and the responsible party has the declaration on file.
- **CE marked**: only when the declaration is signed and the technical documentation is complete.
- **UL listed** (or another NRTL): only with the lab's listing for this model, and the mark used exactly as licensed.
- **Pre-compliance passed** means "a scan found no obvious problem". It is never shown to a customer, a retailer or a
  regulator as approval.
