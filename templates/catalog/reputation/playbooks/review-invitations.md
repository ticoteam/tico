# Review invitations: the paid honest review program

Not a schedule until the owner adds one. Runs when a task names it. The goal is a G2 page and a
Capterra family page with enough the team's product reviews that Sales has somewhere to point. The
first target is twenty reviews on each within sixty days of the first invitation.

---

## Where it runs, and where it never runs

| Surface | Paid honest reviews | Who pays and discloses |
|---|---|---|
| G2 (the team's product) | yes | G2's review campaign: {{company_name}} funds a gift card, G2 pays the reviewer, verifies them and labels the review "Incentivized" |
| Capterra, GetApp, Software Advice (one Gartner Digital Markets listing) | yes | Gartner's reviews program: same shape, their label |
| TrustRadius | yes, later, if leads look there | its own program |
| Google, Yelp, Trustpilot, App Store, Google Play | **never paid** | Google and the app stores allow an unpaid ask; Yelp allows no ask at all |

Confirm the current terms of each program on its own page at the start of every campaign and
date the line in `knowledge/surfaces.md`: which tier allows funded gift cards, the amount cap,
the verification and the label.

## The rule

1. **A milestone, not a mood.** Every customer who reaches the milestone `knowledge/surfaces.md`
   records is invited. Nobody skipped for seeming unhappy, nobody added for seeming delighted.
   Proposed until the owner writes it: a customer ninety days live with jobs completing in
   the last thirty.
2. **Same reward whatever they write.** One nominal gift card, the same for every reviewer, paid
   by the platform, never conditioned on a rating, never mentioned as a thank-you for a good one.
   Twenty-five dollars is the going rate.
3. **The platform discloses.** The review carries the platform's incentivized label. The
   invitation says the platform will label it.
4. **Never a suggestion of what to write, never a mention of stars.**
5. **One invitation, one reminder, then silence,** recorded as `invited` rows so nobody is asked twice.

## Where the list comes from

From Sales, through a task, with the fields needed (account id, contact, milestone date). No
customer data beyond an account id and a date is copied into this repository.

## How the invitation is sent

You never send it. The platform's campaign link goes out from {{company_name}}'s own email or the account
manager, as a requested send per batch when a person has turned mail sending on in Tico with the exact text and recipients. Under 90 words: why
they are being asked, the link, that a review of any kind is welcome, the gift card and that the
site will label it, thanks. The same list goes to the G2 and the Gartner campaigns the same day.

## Recording and reporting

Each invitation is a ledger row `kind: invited`, with the surface, the account id and the date.
The weekly sweep matches new reviews to invitations by date and surface, never by name, and
reports the response rate and the counts on both pages against the target.
