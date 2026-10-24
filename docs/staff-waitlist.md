# Review the Hosting waitlist (staff)

**Unreleased update:** approval, recovery controls, and Credit requests are merged.
The separate admin installation, HQ deployment, and live acceptance remain unverified.

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

## Approve a sign-up

1. Open **Waiting**.
2. Open **Approve** on the customer’s row.
3. Check **Starting credit (dollars)**. It starts at **20.00**. You can enter another positive amount, such as **5.00**.
4. Select **Approve**.
5. Open **Approved** to check **Credit** and **Status**.

HQ records the amount, approver, and approval time once, then starts both Starter servers and issues the Tico models key.
The grant expires 30 days after that approval time, including when setup needs a retry.
Before the Tico server becomes ready, HQ writes the grant and **Enforce**, **$0 minimum**, and **0-hour Hosting grace**.
The grant belongs to the same account the Gateway bills.

Both servers must be ready before HQ sends **You're in: your Tico is ready**.
The email includes the balance and a sign-in link to [app.tico.dev](https://app.tico.dev/).
Repeated approval requests do not create another grant, server, or completion email.

## Recover a failed setup or email

| Status under Approved | Meaning and action |
|---|---|
| **Approved, setting up** | Setup is still running. A pending installation confirmation keeps this status and delays the email. |
| **Approved, setup failed: … ($20.00 credit pending)** | Installation has not created the billing account. The approved amount is recorded, not yet granted. Read the reason, resolve it, and select **Retry setup**. |
| **Approved, setup failed: …** after credit was granted | The credit stays. Resolve the reason and select **Retry setup**. Retry preserves the existing Tico server and account once installed. |
| **Email not sent** | Setup succeeded but the completion email failed. Select **Resend**. Do not approve again. |

The pending amount matches the amount approved. The **Credit** cell also labels it **pending**.
Retry keeps the original amount, approver, and expiry. It does not add a second grant or duplicate a working server.
For example, retrying a failed bot computer leaves the installed Tico server and its credit in place.
No completion email goes out until both servers are ready and billing is configured.

If the reason says a failed server is kept, recover or end that failed machine on **Hosting** before **Retry setup**.
An already installed Tico server must be recovered in place. Retry cannot replace its billing account.

If setup finishes after the original credit has expired, the row says **credit ended before setup**.
Enforcement still applies with a $0 minimum and zero grace. Add credit through [Billing](staff-billing.md), then select **Retry setup**.
The original expired grant is not renewed, and no completion email goes out before recovery.

If HQ does not answer an action, use **Send again** on that same form.
Repeated requests keep one grant and setup; concurrent Resends send one completion email.

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
See [starting credit and credit notices](account.md#your-starting-credit-and-what-happens-at-0) for the customer’s view.
The account page and staff admin page show the same [amount needed to restart servers](account.md#more-credit-and-restarting) while credit is short.

## Who can do this

Only people on the admin page’s allow list see the Waitlist.
HQ checks the staff keys on every call. Any staff key can read the list.
Decline, Resend, Approve, Retry setup, Add credit, and Dismiss require the billing key or the full staff key.
Each change records the signed-in staff email.

*Sources: ticoteam/tico-internal admin/README.md "Waitlist" (Dev 342), ticoteam/tico-services#120 (squash 028ce6bc), 2026-10-22. Not seen live: no access to admin.tico.team.*

*Credit requests sources checked 2026-10-23: ticoteam/tico-internal#16, admin/tico_admin/waitlist.py (Forms, credit_requests, submit_credit); ticoteam/tico-services#127, portal/account.py. Separate admin installation not verified.*

*Approval sources checked 2026-10-23: services#132, hosting/approve.py (shown, approve, retry, on_installed, _grant, _send, SignupSetup); admin#17 reviewed head c7a0ab9c, admin/tico_admin/waitlist.py (Forms.approve/retry/youre_in, approval_credit, status_cell); Engineering’s accepted receipt ordering on Dev 344. Admin merge d47f5682; reviewed source has no subsequent delta per Builder’s receipt. No installation or real action verified.*
