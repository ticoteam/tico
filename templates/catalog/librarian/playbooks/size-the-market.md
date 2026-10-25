# Size the market

A task titled "Size the market", or the last line of "Set up the market map". The owner wants to know what the
market is worth: defined, cut into segments, and each segment sized by more than one method. You file estimates
people check. You are not asked for one true number.

The outcome is a market definition, a tree of segments, and for each segment two or more cited estimates with
a range, plus the main players' dated figures. Nothing else counts as good.

**Budget.** About 25 web searches and 40 `hub doc fetch` calls. Write as you go: the Spend view shows what is
filed.

**Never invent a number.** Every figure comes from a source you read, with a row of evidence. A share you cannot
source is a judgement: say so (step 4).

---

## 1. Define the market first

    hub market define --description "..." --root-name "..." --include "..." --exclude "..." --rule "..." --year 2026

- `description`: what the market is, in one or two sentences. Start from the task's words.
- `includes` and `excludes`: what counts and what does not. Say why for each exclusion.
- `counting_rules`: one vendor in one segment (the one that earns most from it), how a bundle is split, what
  counts as spend (paid software, not services), the currency and the year.
- Do not start sizing until the definition is written. `hub market spend` shows it.

## 2. Cut it into segments

    hub market segment add "Name" --summary "one sentence"
    hub market segment add "Sub-segment" --parent segment/name

- 5 to 8 segments under the root, and 2 to 4 sub-segments only where a segment is too big to size as one.
- Segments do not overlap by design. Where they do overlap, write it down on the estimate (step 6).

## 3. Size each segment two ways

For each segment, file at least two estimates by different methods, using web search and the sources catalog
(`hub market sources`: what each covers, whether it is free or needs the team's key, how far to trust it).

- `top_down`: from a total someone published, narrowed by shares.
- `bottom_up`: buyers times what each pays: counts from government or directory sources, prices from the
  vendors' pricing pages.
- `player_sum`: the sum of what the players earn, from their reported or estimated figures.

      hub market size add --segment segment/name --method bottom_up --formula "buyers * adoption * price" \
        --param buyers=4000000 --param price=300 --low 600000000 --high 2400000000 \
        --evidence-url https://... --quote "what the source said" --arithmetic "the working, in words" \
        --confidence medium

- `--formula` is arithmetic over numbers, your own `--param` names and shared `$names` from the definition. The
  server works `spend_usd` out; do not do it by hand. Give `--low` and `--high` the range the evidence supports.
- Every estimate cites evidence: `--evidence-url` with `--quote`, or `--evidence` with an id you already wrote.
  One row per source.
- Set `--confidence low` when the estimate rests on one source or a judgement.
- If two methods differ by more than 2x, keep both and say why in `--arithmetic`. Do not average them.

## 4. Shares are named parameters

A share (the small-business part of a total, the part that buys software) is a parameter, not a number buried in
a formula.

    hub market define --param smb_share=0.35

- Put it on the definition when more than one estimate uses it, and read it as `$smb_share`. One change then
  moves every estimate that uses it.
- Give it a source, or mark it a judgement. Do not leave a guess looking like a fact.

## 5. Analyst headlines go in `spread`

A headline figure from an analyst firm or a report vendor is not the size. Record it as text on the estimate, with
its source and year: `--spread "Firm A says $X (2025); Firm B says $Y (2024)"`. It tells the owner how much the
published numbers disagree. The accepted size is one you worked out.

## 6. Write the overlaps down

If a segment's spend is partly counted in another (a platform's bundle, a product sold in two segments), write
which and roughly how much on the estimate: `--overlaps "..."`. Do not net it out silently.

## 7. Player figures

For the main players in each segment, dated and labelled:

    hub market figure add --entity company/name --segment segment/name --metric arr --value 12000000 \
      --kind claimed --as-of 2026-03 --evidence-url https://... --quote "..."

- `kind`: `reported` (a filing or the company's own statement of results), `estimated` (a third party's
  estimate), `claimed` (a marketing claim). Pick the weakest that fits.
- `as-of` is the month the figure describes, not the day you read it.
- A player must already be in the graph. If not, report it (`hub market report`) and move on.

## 8. File as proposed

Estimates are filed `proposed`. Accept one (`hub market size accept <id>`) only when the task says the owner
allowed it. Otherwise the owner decides on the Spend view. Never reject another person's estimate.

## 9. End with a note

Close the task with a short note: the segments you sized, the weakest numbers and why (one source, a judgement,
two methods far apart), and which sources from the catalog would help most if the team added a key.
