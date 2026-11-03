# Payments fraud and controls

The standing reference behind the checks in `skills/`. It holds what the surveys say about payments fraud and which controls
hold up. Every figure carries its source and the year it was read. Surveys measure what their respondents reported, so a
figure is quoted with its scope and not as a rate for every company.

## What the surveys say

- AFP, 2025 Payments Fraud and Control Survey (covers 2024): 79% of organizations were victims of attempted or actual payments
  fraud. Business email compromise (BEC) was the most common channel, named by 63% of respondents. Read through AFP's press
  release and summaries, 2026-11-01. The full report is for AFP members.
- AFP, 2026 Payments Fraud and Control Survey (covers 2025): 76% of organizations experienced attempted or actual payments fraud,
  as quoted by Corpay, read 2026-11-01. The 2026 summaries also report a higher share of organizations hit by BEC than the 2025
  edition. Check the AFP report for the exact figure before you quote it.
- AFP survey, as summarised by NeuGroup, read 2026-11-01 (edition not confirmed): 53% of respondents verify beneficiary payment
  details verbally, and 16% use an external service to validate them. Verbal checks are common and easy to skip under volume.
- FBI Internet Crime Complaint Center, 2024 report (released 23 April 2025): reported losses of 16.6 billion dollars across all
  cybercrime. Press coverage of the same report puts BEC losses at about 2.8 billion dollars. Read through press coverage,
  2026-11-01; check the FBI report for the exact figure.

## Controls that hold up

- **Callback on a number already on file.** Use the number in the vendor record, the signed contract or an invoice received before
  the request. Never the number in the change request or the email that asks for it. (CyberAware staff guidance, read 2026-11-01:
  treat every supplier bank change as suspect until a verbal call-back on a number already in the vendor master confirms it.)
- **Ownership, not format.** A format check says the account number looks valid. Independent account validation confirms the
  account belongs to the vendor. Only the second stops a mule account. (Corpay, "Vendor Verification and Banking Validation",
  read 2026-11-01.)
- **Two people for a change.** One person logs a bank change; another approves it after the callback.
- **Changes after approval are approved again.** A payment file edited after its approval goes back for approval.
- **A dormant vendor is verified before its next payment.** No payment to a vendor unpaid for twelve months without a fresh check of
  its details. (Team practice, not a survey figure.)
- **Log every change to the vendor record**, with who made it, when, and the source.

Where callbacks fail in practice, the cause is usually the process, not the knowledge of the rule: the call is the step dropped when
the run is due today. Build the hold into the run so an unverified change cannot be paid by default.

## Early-payment discounts

- The cost of not taking a discount is annualised: discount / (100 - discount) x 365 / (days to full payment - days to discount).
- 2/10 net 30: 2 / 98 x 365 / 20 = about 37% a year. Taking it beats almost any cash rate, so take it when cash allows.
- 1/10 net 30: 1 / 99 x 365 / 20 = about 18% a year.

## Sources
- AFP press releases and summaries of the 2025 and 2026 Payments Fraud and Control Surveys, read 2026-11-01
- FBI IC3, 2024 Internet Crime Report, through press coverage, read 2026-11-01
- CyberAware, guidance on supplier bank detail changes, read 2026-11-01
- Corpay, "Vendor Verification and Banking Validation", blog, read 2026-11-01
- NeuGroup, summary of AFP survey findings on payment validation, read 2026-11-01
