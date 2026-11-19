# Should-cost and landed cost

Two calculations. **Should-cost** is your own estimate of what a part ought to cost to make, built before quotes
arrive. **Landed cost** is what one good unit actually costs you at the door, built from a real quote. The first tells
you which quote lines to question. The second is what you compare suppliers on.

No number here may be invented. Each input is read from a named source with a date, or written as an assumption with
a range. If an input cannot be found, the should-cost for that line is "not estimated" and you rely on comparing
quotes with each other.

## Should-cost

Build it per part, at each quantity tier.

```text
  Material            = net weight or area × (1 + scrap allowance) × material price
+ Purchased parts     = sum of BOM lines at the tier quantity            (assemblies)
+ Conversion          = cycle or process time per unit × machine-and-labour rate
+ Set-up              = set-up time × rate ÷ lot size
+ Overhead            = an assumed percentage of conversion
+ Yield loss          = (cost so far) × (1 ÷ expected yield − 1)
+ Selling, admin, profit = an assumed percentage of the total so far
= Ex-works unit should-cost
+ Tooling share       = tooling cost ÷ units the tooling cost is spread over   (shown separately)
```

Where each input comes from:

| Input | Source | If you cannot get it |
|-------|--------|----------------------|
| Net weight, area, volume | The 3D model or drawing; the board outline | Ask the designer |
| Material price | A resin, metal or laminate distributor's current price for the named grade, dated | Mark "assumption" with a range from two sources |
| BOM line prices | Authorised distributors' price at the tier quantity, dated | "Not read" |
| Cycle or process time | The supplier's quote table if split; a comparable part's record; the designer's estimate | A range, marked "assumption" |
| Machine-and-labour rate | The supplier's stated rate; a published regional rate with its source and date | A range, marked "assumption" |
| Overhead, margin | The supplier's quote table if split | A range, marked "assumption"; say plainly that it is one |
| Yield | The supplier's history on similar parts; your lot records | Assume none better than your records show |
| Tooling | Quotes | Do not estimate tooling; get quotes |

Rules:

- **Give a range.** Carry a low and a high for every assumed input and report the should-cost as a range.
- **Show the split.** Material, purchased parts, conversion and the rest, as shares. The split is what you use in the
  conversation with the supplier.
- **Assemblies.** For a populated board, purchased parts are usually the largest share; build that from the BOM first.
  Then placement and soldering by the number of placements and sides, then test time.
- **Recompute when inputs move.** A material price or exchange rate older than 30 days is re-read before the estimate
  is used again.
- **It is an estimate, not a price.** Never tell a supplier "this costs X". Ask "your material line is higher than the
  current price for this grade and weight; what am I missing?"

## Reading a quote against it

| What you see | What it may mean | What to ask |
|--------------|------------------|-------------|
| Quote inside the range | Fair; compare on other factors | Nothing on price |
| Material line high | Different grade, more scrap, an old price | Grade, part weight and runner or offcut weight, price date |
| Conversion high | Slower process, more set-ups, a manual step | Cycle time, number of operations |
| Unit price low, tooling high (or the reverse) | Cost moved between lines | Compare total cost over the expected life quantity |
| Whole quote far below the range | A different material or process, missed requirement, or a price to win that will rise | "What did you assume for material, tolerance and inspection?" Check the exceptions list |
| Whole quote far above | They do not want the job at this quantity, or misread the package | Whether a different quantity or process would suit them |
| Will not split the price | Normal for some suppliers | Use quote-to-quote comparison; weigh it in the communication score |

## Landed cost per good unit

Compute at each tier, for each supplier, on the same basis.

```text
  Unit price × quantity
+ Tooling and one-time charges              (spread over the quantity you choose; say which)
+ Packaging, if not in the price
+ Freight and insurance to your door        (a forwarder's quote, dated; depends on delivery terms)
+ Duty and import fees                      (rate read today for the tariff code; brokerage)
+ Inspection                                (third-party fee per lot)
+ Cost of money                             (deposit paid early × days × your cost of cash)
= Total cost of the order
÷ Good units                                (quantity × expected acceptance yield)
= Landed cost per good unit
```

Rules:

- **Same delivery terms for all.** Convert every quote to the same point (for example, delivered to your address).
- **Same currency, dated rate.** State the exchange rate, its source and date. Show the price in the quote's currency
  as well.
- **Spread tooling over a stated quantity.** The first order, or the expected life quantity. Show both if they change
  the ranking.
- **MOQ above need.** If a supplier's minimum is above what you will use, cost the whole minimum and count only the
  units you need as good units, or mark the quote "not comparable at this quantity".
- **Not in quote.** Any missing element is written "not in quote", asked for, and never assumed to be zero.
- The catalog skill `price-the-whole-term` applies the same thinking to multi-period contracts.

## Duty and tariffs

Rates and rules for imports into the United States changed many times in 2025 and 2026. Checked 2026-11-18 from
secondary sources: the Supreme Court struck down tariffs imposed under the International Emergency Economic Powers Act
on 20 February 2026; a temporary 10% surcharge under Section 122 of the Trade Act ran from 24 February to 24 July 2026;
tariffs under Section 232 and Section 301 remained and were extended to more products; and the suspension of duty-free
treatment for low-value shipments stayed in effect. Any of this may have changed again.

So, every time:

1. Find the product's tariff code from its description. Classification is a legal judgment: a customs broker confirms
   it before a price depends on it.
2. Read the general rate and every additional duty that applies to that code and country of origin on the official
   schedule (for the US, the Harmonized Tariff Schedule at hts.usitc.gov, and current notices from Customs and Border
   Protection and the US Trade Representative) on the day you quote.
3. Record code, country of origin, each rate, the source and the date read.
4. Never reuse a rate more than 30 days old, and never use a rate from this file.
5. Country of origin follows where the product was last substantially transformed, not where it shipped from. Ask the
   supplier in the RFQ and confirm with the broker.
6. Show landed cost with and without duty, so a rate change shows its effect at once.

For other markets, use that market's official tariff database (for the EU, TARIC) the same way, and remember import
VAT.
