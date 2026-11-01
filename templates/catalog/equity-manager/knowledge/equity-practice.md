# Equity practice

The rules this bot checks grants and the cap table against, and the questions it sends to counsel. These are working rules, not legal or tax advice. Counsel and the
board decide. Figures came from search summaries on 2026-11-01 unless marked; the primary text was not opened.

## Grants
- **Board approval first.** A grant needs a board consent, or the approval the company's documents require, dated on or before the grant date. No approval on file
  puts the grant on the open list. It is not treated as valid.
- **409A age.** Treasury Regulation §1.409A-1(b)(5)(iv)(B) gives an independent appraisal a presumption of reasonableness for 12 months, as long as nothing material changed
  between the valuation date and the grant date. The regulation gives no day count after a material event, so staleness is a facts question. Sources disagree on
  the subparagraph numbering, so cite the regulation by name until the eCFR text is checked.
- **House rule.** A grant is checked against its 409A: valuation within 12 months, and no known material event since the valuation date. Known material events include a
  financing term sheet, a tender offer, or a large contract or change in direction. Any of these sends the grant to counsel before it is recorded as valid.
- **Strike price.** At or above the fair market value on the grant date, which is the 409A value under the house rule. Below it, escalate.
- **ISO post-termination window.** IRC §422(a)(2) requires an incentive stock option holder to be an employee from grant until three months before exercise. A longer
  window after termination generally turns the option into a non-qualified one for tax. Counsel confirms the window in each plan.
- **Term.** Option terms are limited under IRC §422(b). Record each grant's expiry date.

## Vesting
- **Common schedule (house convention, not a requirement).** Four years, a one-year cliff, a quarter vests at the cliff, then monthly. Each grant follows its own letter.
  Where the letter and this convention differ, the letter wins.
- **Vested shares.** Zero before the cliff. From the cliff, granted shares times months elapsed divided by total months, capped at the total.

## Option pool
- **Metric.** Percent of fully diluted shares reserved for future grants (Carta's equity plan benchmark definition, read through a search summary).
- **Seed.** Secondary sources disagree. One table gives a median of 11.8% of fully diluted. Another attributes 12 to 14% to Carta 2025 and 2026 data. A Carta post says most
  seed pools start at 10 to 12%. Use these only as a range, and only as directional.
- **Series A.** A post-money top-up of 15 to 20% appears in a 2025 benchmark table that cites Carta and PitchBook medians. Secondary; the Carta report was not opened.
- **House rule.** A pool left below the figure promised in any offer, or below the pool the round plan assumes, goes to escalation.

## Dilution and scenarios
- Post-money = pre-money + raise. New investors own raise divided by post-money.
- **Placement of the top-up changes who bears it.** A post-money top-up dilutes every holder, including the pool. A pre-money top-up falls on existing holders outside
  the pool alone. Show the placement in every table.

## Sources
- 2026-11-01: Carta, equity plan benchmarks definition, https://releasenotes.carta.com/equity-plan-benchmarks-THezS (read through search summary).
- 2026-11-01: Carta data post, "Remind VCs the Option Pool Does Not Need to Be 20 Percent", https://carta.com/data/linkedin-employee-option-pool-20-percent-push-back/ (read through search summary; the 10 to 12% seed claim is attributed to it by a secondary source; not verified).
- 2026-11-01: LaunchRock, option pool library entry, https://www.launchrock.com/library/expert-advice/option-pool (secondary; cites 2025 Carta and PitchBook medians).
- 2026-11-01: WaveUp, "Startup Dilution per Round: 2026 Benchmarks by Stage", https://waveup.com/blog/startup-dilution-per-round/ (secondary; 11.8% seed median).
- 2026-11-01: Treasury Regulation §1.409A-1(b)(5)(iv)(B), cited through vendor summaries; regulation text not opened. Check eCFR before quoting.
- 2026-11-01: KPMG, reprint of a 2022 Tax Notes article on 409A valuation staleness, https://home.kpmg/kpmg-us/content/dam/kpmg/pdf/2022/tnf-section409a-030722.pdf (2022; argues the 12-month window is not standalone).
- 2026-11-01: IRC §422(a)(2) and §422(b), cited through search summaries; statute text not opened.
