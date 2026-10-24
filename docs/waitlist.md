# Joining the Tico waitlist

Hosted Tico, the Hosting preview on tico.team, runs your Tico server for you. Signing up puts you on a waitlist. This page covers what you enter, what you see afterward, and what happens when staff decide. To run Tico on your own computer instead, start at [Install](install.md).

## Join the waitlist

1. Open the Hosting page on tico.team and enter your **company name**.
2. Choose how to sign in:
   - **Sign in with Google** to use your Google account.
   - **Create account** to make a Tico account with an email address and a password. Open the link in the email Tico sends you to verify the address.
3. On the **Join the Tico waitlist** page, check the company and email shown, then answer **What do you want your bots to do?** This answer is required and can run to 500 characters.
4. Optionally enter **Your name**.
5. Click **Join the waitlist**.

You see **You're on the waitlist. We'll email you when you're in.** The page has no server and no Tico models key yet. Both come after Tico gives you a spot.

Example answer: "Draft our weekly customer newsletter and tidy the support inbox every morning."

The limits are 200 characters for the company name, 500 for the answer and 100 for your name. If the form refuses one, fix it and click **Join the waitlist** again.

## After you join

- Sign in again at any time to see the same page. Nothing about your place changes.
- Signing up again with the same email keeps one place on the list. The place and answer you already have stay.
- Tico staff read your answer and company name to decide. They are the only people who see them.

## When staff approve you

**Unreleased:** approval is merged; HQ deployment, staff admin installation, and live acceptance remain unverified.

Staff normally approve $20 of starting credit, expiring 30 days after approval.
Setup creates two Starter servers—your Tico server and your bot computer—and a Tico models key.
When both servers are ready, **You're in: your Tico is ready** gives you the balance and sign-in link.

Sign in at [app.tico.dev](https://app.tico.dev/) with your Tico account and select your company.
From the account page, **Open your Tico** opens your ready Tico server.
If setup is still running, the page does not say ready. Staff can retry a failed setup or resend a failed email.

See [Your starting credit and what happens at $0](account.md#your-starting-credit-and-what-happens-at-0) for expiry, pauses, retained data, and recovery.

## Your automatic visual address

**Unreleased:** this describes the intended setup behavior; live availability is not confirmed.

Every new and existing company whose Tico server we host gets its own visual address, such as `acme.ticofiles.com`.
Tico sets it up automatically, free, whether or not you buy Tico file hosting.
You have no setup step or visuals on/off switch. Paid file storage is separate.

Until Tico verifies the address, your conversations use the fallback: pictures, video, and audio show inline.
Pages and SVG show a card with **Open**, which opens the isolated file viewer.
If address setup fails or is interrupted, Tico keeps retrying while you use the fallback.
You see the fallback visual status, with no pending or stuck address state.
Full inline visuals become available only after the address is verified.
See [Visuals in replies](visuals.md#with-and-without-a-visual-address).

## If Tico can't offer a spot

You get a short email with the subject **Your place on the Tico waitlist**. It says "We can't offer a spot right now." Staff can keep a private note about the decision. It is never sent to you.

## If you had a hosted preview before the waitlist

Accounts that had a hosted preview before the waitlist keep it. They don't join the waitlist and nothing changes for them. Staff list these accounts as **Approved (before waitlist)**.

## Check your account

Joining the waitlist does not give you a server or a Tico models key.
See [Your Tico account page](account.md) for waitlist status, balance, usage, and server states after approval.
That help describes an unreleased update whose deployment is still being verified.

Staff handle the list on [the admin page](staff-waitlist.md).

*Source: ticoteam/tico-services#120 (squash 028ce6bc), Waitlist 1/2, 2026-10-22.*

*Approval source checked 2026-10-23: services#132, hosting/approve.py (SignupSetup, holds, youre_in_text, YOURE_IN_SUBJECT); waitlist spec v3.4 §§2–3. No live sign-in, email, or provisioning verified.*
