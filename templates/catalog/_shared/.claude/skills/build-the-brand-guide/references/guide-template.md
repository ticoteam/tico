# The brand guide: sections, bars and the Brand card

Each section has what it holds and a bar it must clear. Stop at the bar, not at the end of the template. A section
nobody can answer stays open with a note and an owner.

Decision rows (the promise, traits, taglines, sample verdicts) record `by`: `owner`, `accepted` or `default`. Rules and
facts (names, writing rules, contrast results) do not.

## Sections

| # | Section | Holds | Bar |
|---|---|---|---|
| 1 | Rule pack | At most 12 rules a bot loads before it writes | Every rule is testable. None contradicts the guide. No word or name lists. |
| 2 | Who it is for | One row per buyer type, primary first: the best customer as one person, the job they hire us for, where they meet us. Then who we do not write for, and why. | Each person is described by things you can see from outside. At least one anti-audience with its reason. |
| 3 | How customers say it | Verbatim quotes: quote, who said it, source, what it is evidence of. Negative ones too. | At least five verbatim quotes with sources, one of them negative. |
| 4 | Promise | The one line, the belief or alternative it answers, what only we have, what breaks it today, the rival swap result | Fails the swap with the primary buyer's strongest rival. Every property in it cites an allowed-claim ID. |
| 5 | Claims | Allowed (ID, claim, proof, source, owner, review by) and never (claim, reason, Catch phrases) | Every allowed claim has confirmed proof and a review date. No Catch phrase appears elsewhere in the guide. |
| 6 | Voice | Three or four traits: we are, but not, sounds like, not like, by. Then the rhythm. | Each trait has a "but not" and two real lines, one from the company's story or a customer. A rival would not accept the whole table. |
| 7 | Tone matrix | See below | At least eight moments, including bad news, an error and an apology. Every example line passes the claims and word rules. |
| 8 | Words | Use (word, instead of) and avoid (word, why, warn or block) | Customer words lead the use list. No word customers praise us with is avoided. |
| 9 | Names | Name, right form, rule, wrong forms seen and where | Every name in current material is listed. Every wrong form was actually seen. |
| 10 | Writing rules | Rule, example, how it is checked: case, numbers, dates, money, emoji, exclamation marks, reading level, alt text, link text, inclusive language | Each rule is testable and says how. Only rules with a reason. |
| 11 | Ready-to-use lines | Tagline, 25-word description, 100-word About, sign-offs for good news and for complaints, the main call to action | Word counts are counted, not estimated. The tagline fails the rival swap and was picked by the owner or is tagged default. |
| 12 | Where we speak | One row per surface in use: its limits, the pattern, an example, what never to do | Error messages say what happened and what to do. Support replies for the top two complaints give the real fix. |
| 13 | Look | Colour tokens, type tokens, logo, imagery, motion | See below. Every hex and file is from a real file or the owner. |
| 14 | On and off brand | Surface, text, verdict, why, sources, by. At least six on-brand and two off-brand. | Each off-brand piece names the rule it breaks. Sources sit beside the text, never in it. |
| 15 | Keeping it true | Owner by name, who may change what, version, review date, change log, open items with owners | A named owner, a date, and no decision row left untagged. |

## The tone matrix

The voice does not change. The matrix says which trait leads and what moves. Fill one row per moment the company
really has. Start with these and add one row for each extra buyer type.

| Moment | The reader is | Lead with | Dial up | Dial down | Example line |
|---|---|---|---|---|---|
| First hello (welcome, onboarding) | curious, a little unsure | | | | |
| Selling (homepage, pricing, ads) | comparing, sceptical | | | | |
| Good news (launch, a milestone) | interested if it helps them | | | | |
| Bad news (price rise, a removed feature) | wary | | | | |
| Something broke (outage, incident) | stressed, blocked | | | | |
| Apology (only when the team really failed; never by reflex, never in a legal or formal reply) | annoyed, owed something | | | | |
| Error message | mid-task, maybe for the third time | | | | |
| Support reply | wants the fix, wants to be heard | | | | |
| Billing and legal | careful | | | | |
| Social and community | relaxed, public | | | | |
| Partner, press or investor | professional, short on time | | | | |

Rules for every row:

- Clarity beats voice. If the trait makes the line harder to act on, drop the trait for that line.
- Humour is off in: something broke, apology, error, billing and legal. When unsure anywhere else, write it straight.
- Bad news goes first, in plain words, with what the reader can do next.
- An example line is a real line the company could send today. It passes the claims list and the word rules.

## The Look: tokens, not descriptions

Write each visual rule as a token another tool or person can apply without guessing.

**Colour**

| Token | Role | Hex | Use on | Ratio with its pair | Pass |
|---|---|---|---|---|---|
| `ink` | Body text | (from the file) | `paper` | (computed) | |
| `paper` | Page background | | | | |
| `signal` | The one accent | | | | |
| `ok`, `warn`, `stop` | States that carry meaning | | | | |

- One colour leads. Say roughly how much of each: a page that is all accent has no accent.
- Compute the ratio for every pair in use and for every mark that carries meaning. Use the formula in
  `review-copy.md`.

**Type**

| Token | Role | Family | Fallback | Weight | Size and line height |
|---|---|---|---|---|---|
| `display` | Headlines | | a system font that is always present | | |
| `body` | Running text | | | | |
| `mono` | Code and numbers, if used | | | | |

**Logo**: the approved files by name, the versions (full, mark only, one colour), the smallest size, the clear space, and
at least three misuse rules, each shown (stretched, recoloured, on a busy photo).

**Imagery and motion**: what the pictures show, what they never show, and how things move if they move.

If a hex code, a font or a logo file cannot be found, the cell says "open" with an owner. It is never filled with a
likely value.

## The Brand card proposal

`tico proposal create --kind brand_draft` takes a payload `{"sections": {...}}`. Use only these sections and limits. The
card refuses anything else, and what does not fit stays in `knowledge/brand.md` and on the guide page.

| Card section | Shape | From guide section |
|---|---|---|
| `name` | text, 60 characters at most | Names |
| `positioning` | text, 120 characters at most | Promise |
| `voice` | list of `{trait, do, dont}` | Voice: we are, sounds like, but not |
| `tone` | list of `{situation, guide}`, guide 300 characters at most | Tone matrix: the dial and the example line |
| `words` | `{use: [{word, not}], avoid: [{word, why, level}]}`, level is `warn` or `block` | Words |
| `naming` | list of `{name, rule, wrong: [forms seen]}` | Names |
| `visual` | `{colors: [{name, hex}], fonts: ["family (role)"], logo_files: [...], imagery}`, imagery 1000 characters at most | Look |
| `claims` | `{allowed: [{claim, proof, check, status}], never: [claim]}`, check is the owner and review date, status is `proven` or `due` | Claims |
| `examples` | list of `{text, verdict, why}`, verdict is `on` or `off` | On and off brand |

Check the current shape with `tico brand show` before you send: the card may have gained sections since this was
written (2026-11-18).

## Versions and change log

- The version goes up on every published change: 1.0 for the first guide the owner publishes, then 1.1, 1.2.
- One line per change at the bottom of `knowledge/brand.md`: date, what changed, why, who decided.
- Review every six months, and before any launch or rename. Three months when the guide was built on thin material.
- A change to a claim's proof, a product name or a price is made the day it happens, not at the review.
