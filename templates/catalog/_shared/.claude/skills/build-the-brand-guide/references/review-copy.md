# Review a piece or an asset against the brand guide

Use this for any "is this on brand?" request, for a brand check on something about to go live, and for a miss the
monthly audit found. One piece per review. A person does not need to approve the verdict.

## Before you read the piece

1. Read the Brand card (`tico brand show`) and the rule pack at the top of `knowledge/brand.md`. If the team keeps
   `knowledge/rulings.md`, read it too: earlier rulings on close calls bind this one.
2. Note the surface (page, email, ad, post, slide, error, support reply) and the moment (launch, apology, price change,
   error, routine update). Find that moment's row in the tone matrix.
3. If no Brand card is published and `knowledge/brand.md` is empty, say so on line one, run only the compliance pass
   below, and offer to build the guide.

## The read

1. **Read once as the audience.** Write one line: what the reader will take away, and whether that is what the team
   means. If the takeaway is wrong, say that first. No line edit fixes a wrong point.
2. **Run the cheap checks.** `tico brand check -` on the text catches avoided words, wrong name forms and never-claim
   phrases. It runs no model, so it does not catch a claim that is missing from the allowed list. That is your job in
   step 5.
3. **Go line by line for rules.** For each line that breaks a rule, quote the rule, quote the line, and write the
   smallest change that fixes it. Mark it **rule**.
4. **Mark taste separately.** A line that breaks no rule but could be better is a **suggestion**, and you say it is
   your judgement. A suggestion is never a miss and never blocks.
5. **Check every fact.** Product and plan names, prices, dates, numbers, results, guarantees. Find each in a dated
   source (`tico doc search`, the price sheet, the current release). A claim must be on the allowed list
   (`tico brand show --section claims`). A claim with no source is a question for the piece's owner, not a fix you
   make.
6. **Check the tone against the moment.** Compare with the matrix row: is the right trait leading, is anything dialled
   up that should be down? A joke in an error or an apology is a rule miss.
7. **Check what you can see.** Colours, type and the logo against the visual tokens. Compute contrast for any text and
   background pair you can read (formula below).
8. **Write the verdict on line one:** on brand, on brand with small fixes, or off brand, with the reason in one clause.

## The compliance pass (always, even with no guide)

Run these on every piece. Each hit is a rule-level finding.

- **Unsupported claims.** A number, result, ranking or "only", "first", "best", "fastest" with no source.
- **Risky comparisons.** A competitor named or plainly implied, without a dated, like-for-like source.
- **Testimonials.** A quote with no named source and no recorded permission, a reworded quote, a result most customers
  do not get, a review tied to a reward. Never write a quote for a customer.
- **Guarantees and prices.** A guarantee without its terms in the same sentence. A price without its unit and any
  add-on that changes it.
- **Missing disclosures.** A paid or insider relationship not stated next to the endorsement. An ad not marked as one
  where the channel requires it.
- **Legal words.** "Certified", "approved", "open source", "free", "secure", "compliant" need the licence, terms or
  certificate behind them.
- **Other people's marks and images.** A logo, photo or name the company has no right to use.

Anything with a legal question goes to the legal owner as a sub-task. Do not rewrite legal wording yourself.

## Contrast: compute it, never judge by eye

For each colour, turn each sRGB channel (0 to 255) into a linear value:

1. c = channel / 255
2. linear = c / 12.92 if c is 0.04045 or less, otherwise ((c + 0.055) / 1.055) to the power 2.4
3. Luminance L = 0.2126 x R + 0.7152 x G + 0.0722 x B (using the linear values)

Ratio = (L of the lighter colour + 0.05) / (L of the darker colour + 0.05).

Run the arithmetic with a tool and paste the result. Do not round up: 4.49:1 fails 4.5:1.

| What | Minimum (WCAG 2.2, Level AA) |
|---|---|
| Normal text against its background | 4.5:1 |
| Large text (18 point, or 14 point bold) | 3:1 |
| The parts of a control or graphic needed to understand it | 3:1 against the adjacent colour |
| Decoration, and text inside a logo | exempt |

## What the review looks like

```
On brand with small fixes: one avoided word and one unsourced number.

Takeaway as a reader: "they fix bikes before my commute". That is the point.

1. RULE (words: "leverage" is on the avoid list). Line 3: "Leverage our mobile workshop".
   Fix: "Use our mobile workshop".
2. RULE (claims: no allowed claim covers this). Line 5: "9 in 10 repairs done same day".
   Not fixed: needs a source from ops, or the line is cut. Question filed.
3. SUGGESTION (my judgement). The second paragraph would land better as one question.

Checked: plan names and prices against the price sheet dated 2026-11-02. Contrast of the button text: 5.2:1, pass.
Not checked: the header image, which was not attached.
```

## Checks before you finish

- The verdict is the first line.
- Every change is marked rule or suggestion, and each rule change quotes its rule.
- You changed lines, not paragraphs.
- Names, prices, dates and claims were checked against a source, or the check is listed as not done.
- The compliance pass ran, and legal questions went to the legal owner.
- Anything you could not see is "not checked", never "fine".
