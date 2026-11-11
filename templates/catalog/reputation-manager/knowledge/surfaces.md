# Review surfaces

What each surface allows, where {{company_name}}'s listing is, and how the surface fails. The
first sweep fills the listing URLs and checks each rule against the platform's current policy page;
a rule quoted here carries the date it was read. When a platform changes a rule, keep both lines
with their dates.

## The table

| Surface | Listing URL | Invite? | Reply? | Flag? | Graded on answered complaints? | Notes |
|---|---|---|---|---|---|---|
| Google Business Profile | | yes, every customer, no incentive, no gating | yes, once verified | yes, policy violations only | no | The one surface that lets a team invite every customer |
| Yelp | | no | yes, once the listing is claimed | yes, guideline violations | no | Yelp's filter buries solicited reviews and its guidelines exclude reviews that are not a first-hand consumer experience |
| Better Business Bureau | | not applicable | yes, answer each complaint | no | yes | Unanswered complaints cost the grade; answered ones do not |
| Capterra / G2 / GetApp | | yes, incentives allowed with the platform's disclosure | yes | yes | no | For software; the buyer of a software product looks here |
| Trustpilot | | yes, every customer | yes | yes | no | Inviting a subset is what the platform penalises |
| Apple App Store / Google Play | | in-app prompt only | yes | yes, guideline violations | no | Two apps may mean two listings each. Replies belong to the App Store Owner (`app-store`) when the team has one |
| Glassdoor / Indeed | | not applicable | yes | yes | no | Employer reviews; contractors often post here |
| Sitejabber and others | | as the surface says | as the surface says | | | Added when a search finds one |

## Ledger columns

`knowledge/ledger.csv`, one row per item, ids `r-0001` upward and never reused.

| Column | Values |
|---|---|
| `kind` | `review`, `complaint`, `rating` (a star with no text), `invited` |
| `surface` | the surface's name as the table above spells it |
| `review_date` | the date the surface shows, ISO |
| `rating` | the stars as shown, or empty |
| `reviewer_display` | the display name as shown, nothing more |
| `author_type` | `customer`, `vendor`, `employee`, `unknown` |
| `theme` | one of the themes below |
| `guideline_flag` | empty, `candidate`, `flagged`, `upheld`, `rejected` |
| `status` | `new`, `drafted`, `replied`, `answered`, `flagged`, `resolved`, `removed`, `edited` |
| `status_date` | the date the status was last set |
| `summary` | one line in the bot's words, no quote longer than ten words |

## Themes

The `theme` column of the ledger takes one of these. Add a theme when three rows would share it;
merge two when they cannot be told apart.

- `payment`: a vendor's pay, a hold, a fee, a refund
- `standards`: a decertification, a rejected job, a quality dispute
- `product`: the software itself, a bug, a missing feature
- `support`: response time or quality of help
- `billing`: what a customer was charged
- `legacy-product`: a product or service the team no longer offers
- `other`

## The milestone for invitations

Recorded here by the owner before `playbooks/review-invitations.md` runs. Example: ninety days
live with work completing in the last thirty. Until it is written here, no invitations.

## Sources
- (the first sweep writes one line per surface, with the policy page read and its date)
