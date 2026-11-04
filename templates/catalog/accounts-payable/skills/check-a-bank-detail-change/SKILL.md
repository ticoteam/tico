---
name: "check-a-bank-detail-change"
description: "Handle a request to change a vendor's bank or remittance details, or a bill whose printed details differ from the vendor record: hold the vendor's bills, verify by callback on a number already on file, and record who verified it."
---
# Check a bank detail change

When to use: a vendor asks to change where it is paid, or a bill prints bank details that differ from `knowledge/vendor-details.md`.
Use it before any payment to that vendor. A request to change details is treated as suspect until it is verified.

Steps:
1. Log the request on the register the day it arrives: vendor, date, sender, channel (email, portal, phone, invoice) and what
   changed. Keep digits out of the note. At most, write "new account, last four 4410", and only if the request shows them.
2. Hold every open bill from that vendor and every bill for it in the next run. Name each hold in the run's report.
3. Do not reply to the request, do not call the number or follow the link in it, and do not apply the change. A reply tells
   the sender that the address is read.
4. Find the verification contact from a source the request could not have changed: the phone number in the vendor record,
   the signed contract, or an invoice received before the request date. Record the source by name (for example, "contract
   2025, page 6"), not the number.
5. Create a task for the person named in setup to make the call. The callback confirms with the vendor's known contact: the
   new details, the last four digits, and the number and amount of the next bill they expect to be paid.
6. Record the outcome in `knowledge/vendor-details.md`: who called, when, the source used, the result. Apply the change only
   after a confirmed result. A "not reached" result keeps the holds.
7. If the change is not verified within five business days, escalate to the Head of Finance with the holds and their total.
   Do not release a held bill to a changed account to meet a due date.

Checks before you finish:
- No change is applied without a verification recorded against a source the request did not supply.
- Every open bill from the vendor shows its hold and the reason.
- The request is kept with its date and channel, unedited.
- A lookalike domain, a changed sender name or urgency in the request is named as a flag.

Example (invented, for illustration): "Pine Works Ltd emailed on 2026-09-24 asking to change its account. Bills PW-3301 and
PW-3316, 4,820 in total, held. Callback to the number on the 2025 contract (page 6) on 2026-09-25 reached their accounts
contact, who confirmed the new account ends 2209 and that PW-3340 is the next bill. Change applied 2026-09-25, with the
verifier and the time recorded."
