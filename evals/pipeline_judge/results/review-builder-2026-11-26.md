# Review check with the builder-maker rule, on real pages, Nov 18 to 26 (task #3970)

Each Engineering Review page version judged in the window (2026-11-18 to 2026-11-26 21:38 UTC), asked again through
`tico decision ask` (rebuild.py `ask`) as Tico's Review bar with the builder rule (`REVIEW_RUBRIC`): the five parts in
backend/pipeline_judges.py CHECKS["two_reviews"], `maker` in place of `models`. The bar is unchanged (yes 0.5, no 0.2;
`two`, `maker` and `commit` no at 0.4). Each PR's head when the page was judged was rebuilt from the heads Tico had
recorded on the task's PR link and each commit's committer date on GitHub (the newest at or before the verdict): the
server's own `judged_against` is not readable to bots. 272 of 274 pages had a head.

Pages before the rule (judged before 2026-11-26 02:30 UTC) were written for the bar without it: most do not name the
builder, so they are not met on `maker`, as the spec expected (its Risks).

## At the bars the server uses, final wording

| pages | n | met | unsure | not met | unsure % |
|---|---|---|---|---|---|
| all | 274 | 72 | 36 | 166 | 13.1% |
| written before the rule | 182 | 38 | 23 | 121 | 12.6% |
| written after the rule | 92 | 34 | 13 | 45 | 14.1% |
| today (the bar asked whole) | 274 | 1 | 213 | 60 | 77.7% |

Not met on `maker`: 149 pages. Unsure: 13 on `maker`, 13 on `findings`, 9 on `two`, 3 on `checked` (a page may be both).

## Traps (each must not be met)

Cut from the 72 pages met above. A cut page keeps no model or maker name of the builder anywhere (a page where one
remains is no trap and is left out).

| trap | not met | pages |
|---|---|---|
| no builder named (every sentence naming the builder removed) | 24 | 24 |
| second review by the builder's maker (the builder made an OpenAI model, lines saying the makers differ removed) | 22 | 22 |

cases.json: `dev-two-reviews-builders-maker` and `dev-two-reviews-no-builder` not met on every run (maker 0.04 to
0.17), `dev-two-reviews-notes-only` met (maker 0.98).

## How the `maker` question was settled

| wording | unsure % all / after the rule | traps met or unsure |
|---|---|---|
| "names a different maker", with maker examples | 15.0% / 14.1% | 3 of 63 (one met); the met one scored 0.48 to 0.68 over nine runs |
| compare makers, not model names (every GPT and Codex model is OpenAI's) | 14.2% / 19.6% | 0 of 3 re-asked (0.04 to 0.29) |
| and: of more than two reviews, the second is the one the page gives as independent | 13.1% / 14.1% | 0 of 46 |

No yes or no line on `maker` alone separated the first wording's traps from good pages (a same-maker page reached 0.68),
so the question was made clearer instead of the bar moved. Pages with an earlier review by the builder's maker that
they set aside were unsure on `maker` (0.41 to 0.42) until the last line.

Met then sent back was not measured: the window's outcomes were not labelled.
