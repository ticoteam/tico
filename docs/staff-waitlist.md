# Review the Hosting waitlist (staff)

**Unreleased update:** Credit requests is merged, but the separate admin installation is still being verified.

Only Tico staff see this page. Open **Waitlist** on the admin page (admin.tico.team). It sits behind Cloudflare Access, so customers never reach it, and HQ only takes changes from the admin page with the staff billing key. Customers see [Joining the Tico waitlist](waitlist.md).

Without the billing key the page shows the list and says "Changes not connected".

## Read the list

The Waitlist page has five views. Each view's link shows HQ's count.

| View | Shows |
|---|---|
| **Waiting** | Sign-ups nobody has decided, oldest first. |
| **Approved** | Sign-ups staff approved. |
| **Declined** | Sign-ups staff declined, newest decision first. |
| **Approved (before waitlist)** | Accounts that had a hosted preview before the waitlist. They keep their servers. |
| **Credit requests** | Open requests from the customer’s account page, oldest first, with balance and usage. |

A row shows the name, email, company, the customer's answer (line breaks kept) and the sign-up date. A decided row also shows who decided and when. A declined row shows your private note, and "Email not sent" if the decline email failed.

The Overview shows **Waitlist: N waiting**. It links to the Waiting view.

## Decline a sign-up

1. Open **Waiting**.
2. On the row, enter an optional private note of up to 1000 characters.
3. Click **Decline**.

HQ keeps the entry as declined, with your email and the time, and sends the person one short email: "We can't offer a spot right now." The note is never sent. Sending the form twice makes one decline and one email.

If the person was decided already, HQ says "this person isn't waiting any more".

## Resend a decline email

When the email fails, the declined row says **Email not sent**. The decision stands. Click **Resend** on the row to send it again. Two Resends at once send one email.

## Approve

The Waiting rows have an **Approve** form with a starting credit pre-filled at $20.00. Approve does nothing yet: it sends nothing to HQ and says "Approve comes with the next update (Waitlist 2/2)".

## Handle a credit request

1. Open **Waitlist** on the admin page.
2. Select **Credit requests**.
3. Review the company, email, message, request date, **Balance when asked**, **Balance now**, and **Usage so far**.
4. Open **Add credit** on the request.
5. Check **Credit (dollars)**. It starts at $20.00. You can enter another positive amount, such as 5.00.
6. Select **Add credit**.

HQ adds a grant ending in 30 days and marks the request answered.
The credit counts through the end of its expiry date in UTC.
To close the request without adding credit, select **Dismiss** instead.
Neither action creates a card inside the customer’s Tico.

**Usage so far** includes recorded charges by product and model usage not yet billed.
An empty list says **No credit requests yet**.
For other billing adjustments, use [Manage a company’s billing](staff-billing.md).

If HQ does not answer, use **Send again** on the same form. A retry does not add the credit twice.
If credit was added but the request was not marked answered, the page says so. Retry that same form.

Models resume within five minutes once enough credit covers usage and brings the balance above the minimum.
Server restart follows separate billing coverage rules. Do not promise that adding $1 restarts both servers.
Low-balance notices and server restart-shortfall amounts belong to separate updates still under review.

## Who can do this

Only people on the admin page's allow list see the Waitlist. HQ checks the staff keys on every call: any staff key can read the list, and only the billing key (or the full staff key) can decline or resend. Adding credit and dismissing requests also require the billing key or full staff key. Each change records the signed-in staff email.

*Sources: ticoteam/tico-internal admin/README.md "Waitlist" (Dev 342), ticoteam/tico-services#120 (squash 028ce6bc), 2026-10-22. Not seen live: no access to admin.tico.team.*

*Credit requests sources checked 2026-10-23: ticoteam/tico-internal#16, admin/tico_admin/waitlist.py (Forms, credit_requests, submit_credit); ticoteam/tico-services#127, portal/account.py. Separate admin installation not verified.*
