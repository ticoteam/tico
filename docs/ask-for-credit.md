# Ask for more credit

**Unreleased:** this help describes the merged account-page update. Deployment is still being verified.

Send a credit request to Tico staff from your account page, even when your company’s servers are stopped.
You need a Tico account that belongs to the company and an account page with billing set up.

1. Open [app.tico.dev](https://app.tico.dev/).
2. Select **Sign in to Tico** and sign in with your Tico account.
3. Select your company from the **Company** menu.
4. In **Ask for more credit**, optionally fill in **Anything you’d like us to know?**
   The limit is 1,000 characters. For example: “We’d like another week to finish testing our support bot.”
5. Select **Ask for more credit**.

The page shows **We have your request from** with the request date, followed by **We’ll be in touch**.
Tico staff receive your request with the company’s balance and usage.
Nothing appears inside your Tico: there is no task or approval card there.
The request asks staff to review it. It does not add credit automatically.

## If you cannot send the request

If the page already shows a request date, staff have your open request. You do not need to send another.
If the form refuses your message, shorten it to 1,000 characters and try again.
If you see **No credit yet**, billing is not ready. Use **Support** on the account page if you need help.
If you cannot find the company, check that you signed in with the Tico account that belongs to it.

After staff add credit, check [your balance and server states](account.md).
Models resume within five minutes after enough credit is added. Server restart has separate credit requirements.

*Sources checked 2026-10-23: ticoteam/tico-services#127, portal/views.py (document, account), portal/account.py (ask, check_message), and portal/api.py (credit-requests route); spec v3.4 sections 2–3. Live request sending not tested.*
