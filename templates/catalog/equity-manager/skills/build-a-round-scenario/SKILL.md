---
name: build-a-round-scenario
description: Draft ownership before and after a priced round, with the investor share, the pool top-up placed pre- or post-money, and each holder's new percentage, all summing to 100. Use when a person asks what a raise does to ownership.
---

# Build a round scenario

## When to use
When a task asks what a raise, a pre-money value or a pool top-up does to ownership. The output is a draft for discussion. It never recommends terms.

## Steps
1. Inputs: the raise (R), the pre-money value (P), the pool top-up (t) as a share of post-money fully diluted, and where the top-up sits (pre-money or post-money). Use the current cap table as the starting point.
2. Post-money = P + R. New investors' share i = R / (P + R).
3. Post-money top-up: the round dilutes everyone first, then the top-up dilutes everyone, the new investors included. Each existing holder, including the current pool, keeps (1 - i) x (1 - t) of its old percentage. The new investors keep i x (1 - t). The pool's new total is its old share times (1 - i) x (1 - t), plus t.
4. Pre-money top-up: the pool is set to a target p of post-money fully diluted. Holders outside the pool keep their relative shares of (1 - i - p). Each one's new percentage is its share of the existing non-pool holders times (1 - i - p). The pool's new total is p.
5. Write the assumptions above the table: values, placement, top-up size, and that no notes, SAFEs or other instruments convert.
6. Give at most three variants, for example three pre-money values. Label each one a draft.
7. Check each column sums to 100.0%, after rounding to one decimal place. If it does not, fix the rounding and say so.

## Checks before you finish
- Investor share equals R divided by post-money, to one decimal place, times (1 - t) when the top-up is post-money.
- Every column sums to 100.0%.
- The assumptions block states the placement and the top-up.
- No term, price per share, or recommendation is in the table. The price per share is not shown unless the cap table gives the share count.
- The table is labelled "draft for discussion", and the lawyer or board decides terms.

## Example (invented)
Pre-money 12M, raise 3M, post-money 15M. Investors 20.0% before the top-up. Top-up 2.0%, post-money.

| Holder | Pre | Post |
|---|---|---|
| Founders | 62.0% | 48.6% |
| Other holders | 29.8% | 23.4% |
| Pool (granted and unissued) | 8.2% | 8.4% |
| New investors | 0.0% | 19.6% |
| Total | 100.0% | 100.0% |

Working: the round gives investors 20.0% and existing holders 80.0%. The top-up keeps 98% of each. So existing holders keep 0.80 x 0.98 = 0.784, and investors keep 0.20 x 0.98 = 0.196. Founders 62.0 x 0.784 = 48.6. Other holders 29.8 x 0.784 = 23.4. Pool 8.2 x 0.784 = 6.4, plus the 2.0 top-up = 8.4. Investors 20.0 x 0.98 = 19.6.
