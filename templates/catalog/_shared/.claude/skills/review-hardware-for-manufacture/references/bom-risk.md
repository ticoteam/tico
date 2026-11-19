# BOM risk

A design that cannot be bought cannot be built. Check every line of the bill of materials before release, and again
before each purchase, because stock and status change weekly.

## Where the data comes from

In order of trust:

1. The part manufacturer's own product page and any product change or end-of-life notice.
2. Authorised distributors' pages for that exact MPN: stock, lead time, price breaks, minimum order, lifecycle flag.
   An authorised distributor is one the manufacturer lists as such.
3. A parts-data Tool, if the team has one connected, as a faster way to read 1 and 2.

Never use a broker's listing as evidence that a part is available. Write the source and the date beside every value.
A part you could not look up is "not read", not "fine".

## The table

One row per BOM line:

| Column | What to record |
|--------|----------------|
| Line, references, quantity per unit | From the BOM |
| Manufacturer and full MPN | Including package and packing suffixes. "10k 0402" is not an MPN |
| Lifecycle | Active / not recommended for new designs / last-time-buy (with the date) / obsolete |
| Sources | How many independent manufacturers make a drop-in equivalent; how many authorised distributors stock this one |
| Stock | Units in stock across authorised distributors, today |
| Lead time | The manufacturer's or distributor's quoted lead time when not in stock |
| Minimum and pack | Minimum order and reel or tray size, against the build quantity plus an allowance for loss |
| Alternate | An approved second MPN, or "none" |
| Unit price | At the build quantity, with date |
| Cost share | This line's share of the BOM cost |
| Compliance | Materials declarations on file for the markets (for the EU, RoHS and REACH) |
| Origin | Country of origin, for the lines that drive cost |
| Risk | Green / yellow / red, with the reason |

## Scoring

| Colour | Any of these |
|--------|--------------|
| **Red** | Obsolete or past last-buy. MPN does not resolve. No stock and lead time later than the build date. Sole source (one manufacturer, no equivalent) with no plan. Only available from brokers |
| **Yellow** | Not recommended for new designs. Last-time-buy announced. One authorised distributor has stock. Lead time uses more than half the time to the build. Single source but in stock with a plan. Pack size forces buying far more than needed. No materials declaration yet |
| **Green** | Active, in stock at two or more authorised distributors or with a lead time that fits, and either multi-source or not critical |

A red line blocks release until it has a fix or a written plan the owner has accepted. Count reds and yellows at the
top of the table.

## What to do about a risk

| Risk | Options, cheapest first |
|------|-------------------------|
| Obsolete or last-buy | Use the manufacturer's named replacement; find a pin-compatible equivalent; redesign that block; make a last-time buy sized to a stated number of builds (needs an OK: it is spend, and the stock can be stranded) |
| Not recommended for new designs | Change now if at EVT; plan the change for the next revision if later |
| Single source | Qualify a second part on the same footprint; where none exists, hold buffer stock and say how many weeks |
| Long lead time | Order long-lead parts ahead of the rest (needs an OK above budget); ask about a dual footprint |
| No stock | Check the manufacturer's own store and all authorised distributors; ask the assembler; never substitute without the designer |
| Pack size too large | Ask for cut tape; pick a different package size the design can take |
| Generic passives with no MPN | Let the assembler choose within a written specification: value, tolerance, voltage, dielectric, size |

For each alternate, the designer confirms fit, form and function from both datasheets: footprint, pinout, electrical
limits, firmware differences. A bot can line the two datasheets up side by side; it does not approve the swap.

## Counterfeit and grey-market parts

Buy from the manufacturer or its authorised distributors. If the only supply is a broker, that is a red line and the
decision goes to the owner, with the cost of testing the parts included. Never mix broker stock into a build silently.

## Custom and long-pole items

Beyond catalogue parts, list anything with its own tooling or programming: custom cables, batteries, displays,
antennas, moulded parts, labels, packaging, pre-programmed chips. For each: the supplier, the lead time, the minimum
order, who owns the tooling, and whether there is a second supplier. These are often the real schedule drivers.

## Cost concentration

List the ten lines with the largest cost share. They are where a price change, a duty change or a redesign matters
most, and they go to the `source-and-certify-hardware` skill for quotes and should-cost.

## Keeping it current

- Re-read stock, lead time and lifecycle on the day of the release gate and again on the day of purchase.
- For a product in production, re-check the BOM monthly with a routine, and immediately on any change notice from a
  manufacturer. A status change opens an issue for the design's owner.
- Keep the table in `reports/hardware/<product>-bom-risk.md` with the date of each run, so the next run shows what
  moved.
