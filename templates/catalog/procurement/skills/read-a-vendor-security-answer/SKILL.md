---
name: read-a-vendor-security-answer
description: Sort a vendor's security answers into evidenced, claimed and missing, and check that any report or certificate is current, belongs to the vendor and covers the product the team would buy.
---
# Read a vendor security answer

When to use: when a vendor that will hold team or customer data returns a questionnaire, a trust page, a certificate or an
audit report.

Steps:
1. List each question from `knowledge/security-questions.md` with the vendor's answer, word for word, and the date it was
   given.
2. Mark each answer one way. **Evidenced**: you have read a report, certificate or contract clause. **Claimed**: the
   vendor says so, with no document. **Missing**: no answer, or a document promised and not yet received.
3. Before a report or certificate counts as evidence, check four things: it is current (its period or expiry date), it is
   the vendor's own and not a parent, subsidiary or partner's, its scope includes the product the team would buy, and it
   covers the controls for the data the team would hold. A report that covers another product is "claimed" for this one.
4. Read the exceptions in a report before you mark it evidenced. Listed exceptions or a qualified opinion are noted on the
   line, with what they say.
5. Turn each missing item into one line in the draft questions to the vendor, with the reason it matters.
6. Record the result in `knowledge/vendors.md` against the vendor, with the date each line was read.

Checks before you finish:
- No claimed line is written as verified.
- Each evidenced line names the document, its period or expiry, and its scope.
- Data location, sub-processors, breach notification time and deletion on exit each have an answer or are marked missing.
- A vendor with any missing item is not described as ready to approve.

Example (invented): "Data location: claimed, from the trust page read 2026-09-28 (EU hosting). Security audit report: evidenced
for the 2025 period, read 2026-10-02; the scope covers the core platform only, so the reporting add-on the team would buy is
claimed. Breach notification time: missing. Question drafted for the vendor."
