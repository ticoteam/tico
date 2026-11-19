# A change to a vendor's bank or remittance details

The most expensive mistake in payables is paying a real bill to a thief's account. The thief sends a believable
message ("we have changed banks"), often from the vendor's real, hacked mailbox, often just before a large payment is
due. Once the money moves it is rarely recovered in full. In a January 2026 survey of finance practitioners by the
Association for Financial Professionals, about three in four organizations reported attempted or actual payments
fraud in 2025, and email impersonation was among the most common routes (secondary coverage of the survey, read
2026-11-18).

So: every change is treated as fraud until a callback proves otherwise. There is no exception.

## What counts as a change

- A new account or routing number, or a new bank.
- A new payee name, a new country, or a new payment method (ACH to wire, bank to a payment app).
- A new remittance address for checks.
- A new email address or domain for "where to send remittance" or "who handles billing".
- Bank details on a bill that differ from the payee on file, with or without a note.
- A change entered in a vendor portal, if the company did not start it.
- A first payment to a new vendor whose details came only by email.

## The steps

1. **Hold every unpaid bill from the vendor.** Not only the one that carried the change.
2. **Do not reply** to the message, call a number in it, click a link in it, or use a "new contact" it names. If the
   vendor's mailbox is compromised, the thief reads the reply.
3. **Find a number the request could not have changed**, in this order: `knowledge/vendor-details.md` (with where it
   came from); a signed contract; a bill paid without trouble more than three months ago; the vendor's website typed
   by hand from a bookmark or the contract, not from the message. If none exists, say so and ask the owner or the
   teammate who knows the vendor to make contact.
4. **Call.** If the bot has a phone Tool, it calls. If not, file a sub-task for the named person with the script
   below. Speak to a contact the company already knows, or to the vendor's finance team through the main switchboard.
5. **Confirm three things**: that they asked for the change; the last four digits of the new account and the name on
   it; who at the vendor approved it. Never read the full new number to them; they read the last four to you.
6. **Second look.** A different person or bot from the one who took the request checks the record before the payee is
   changed. Tico asks a finance admin for a new or changed payee; that approval is the second look. Show them the
   callback record.
7. **Record** in `knowledge/vendor-details.md`: date, who called, who confirmed, the number used and its source, the
   last four digits. Not the full details.
8. **Change the payee** with `recipient_add`, the details going only into its payload. Then release the held bills.
9. **Tell the vendor's known contact in writing** that the details were changed and from which payment.

## Call script

    "This is <name> from <company> accounts payable. We received a request on <date> to change the bank account we
    pay you on. I am calling the number we have on file to confirm it. Did your company send that request? Could you
    read me the last four digits of the new account and the name on it? Who approved the change at your end?"

If they hesitate, do not know, or the digits differ: thank them, change nothing, and go to "If it looks like fraud".

## Warning signs

Any one of these raises the stakes; none is needed for the check to apply.

- Urgency: "today", "before the cut-off", "to avoid a late fee".
- Secrecy or a request to skip the usual process: "no need to call", "I am in meetings, email only".
- A sender address that is slightly off: a swapped letter, a different ending, a free mail service.
- A reply-to address different from the sender.
- The new bank is in a different country or region from the vendor.
- The account name is a person, or differs from the vendor's legal name.
- A change shortly before a large bill is due, or shortly after a new contact introduced themselves.
- A message from someone senior in the company asking for the payment to be pushed through.
- A bill that looks right but has new details in the footer and no note at all.

## What does not verify a change

- A reply to the email, however detailed.
- A letter on the vendor's letterhead, a "bank confirmation" PDF, a voided check image.
- A call to a number given in the request.
- A call that came in from the "vendor".
- A small test payment that "arrived fine". The thief will confirm it.
- The request coming from the vendor's real address. Mailboxes get taken over.

## If it cannot be verified

- The bills stay held. Tell the known contact, on the known number or address, that payment waits on verification
  and that the company will pay to the details on file if they prefer.
- After five business days without confirmation, raise it with the owner as a suspected fraud attempt.
- A due date passing is not a reason to release. If the vendor is real, they will confirm quickly; a late fee can be
  asked back afterwards.

## If it looks like fraud

1. Change nothing. Keep the message and its full headers.
2. Tell the owner the same hour, on the task: what arrived, what the callback found.
3. Tell the real vendor through the known contact: their mailbox or their customer list may be compromised.
4. Check the last 90 days of payments to that vendor for any that went to changed details.
5. Add the sender's address and the account's last four to the notes so a repeat is recognised.

## If money already left

Minutes matter.

1. **Call the company's bank's fraud line at once** and ask for a recall of the payment. Have the amount, date,
   paying account and the receiving details ready. A person with authority on the bank account may have to make the
   call: tell the owner by the fastest route the company uses for emergencies.
2. **Report it.** In the US, file a complaint with the FBI's Internet Crime Complaint Center (ic3.gov) the same day;
   a fast report gives the best chance of the receiving bank freezing the funds. Outside the US, the national fraud
   reporting body.
3. **Tell the insurer** if the company has crime or cyber cover; policies set short notice periods.
4. **Preserve** every message, the bill, the payment record and the callback notes.
5. **The real bill is still owed.** Agree a date with the vendor.
6. Afterwards, write what let it through and change the routine or the rule so it cannot repeat.

This is orientation, not legal advice. US default; check locally.
