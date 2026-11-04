# Contact support for more credit

**Unreleased:** this help describes the merged account-page update. Deployment is still being verified.

The account page no longer has a credit request form. To ask Tico staff for more credit, send a message from **Support**.
On your account page, select **Contact support for more credit** to open it. You need a Tico account that belongs to the company.

1. Open [app.tico.dev](https://app.tico.dev/) and select **Sign in to Tico**.
2. Select your company from the **Company** menu.
3. Select **Support** in the menu.
4. Under **New request**, keep **Server** set to **Whole company**, or choose the server that needs credit.
5. Enter a **Subject** of up to 160 characters and a **Message** of up to 3,800 characters.
   For example, the subject “More credit for testing” and the message “Please add $20 so our support bot can finish testing this week.”
6. Select **Send request**.

Your request opens a thread on the Support page. Tico support’s replies appear in that thread under **Tico support**.
To answer, enter a reply and select **Send reply**.

## If you cannot send the request

If you sent a request before this change, Tico staff still have it. You do not need to send it again.
If the page refuses your message, check the lengths: the **Subject** is limited to 160 characters and the **Message** to 3,800.
If the account page shows **No credit yet**, your balance appears there once your account is ready.
If you cannot find the company, check that you signed in with the Tico account that belongs to it.

After staff add credit, check [your balance and server states](account.md).
Models resume within five minutes after enough credit is added. Server restart has separate credit requirements, described in [More credit and restarting](account.md#more-credit-and-restarting).

*Sources checked 2026-11-04 from the local origin/main of ticoteam/tico-services (90e43ae; a fetch failed, so the live remote is unconfirmed): CHANGELOG.md under Unreleased; portal/account.py lines 14–16; portal/views.py (the NAV list, the account page’s “Contact support for more credit” link, “No credit yet”); portal/support.py (the New request form, its limits, and the thread labels). Not tested live: sending a request and receiving a staff reply.*
