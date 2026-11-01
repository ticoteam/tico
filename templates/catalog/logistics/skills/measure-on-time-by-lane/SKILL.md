---
name: measure-on-time-by-lane
description: Measure on-time and in-full delivery for the last week by carrier and by lane from a shipment export, using the promised date and the full-order definition, group small lanes, and flag any lane below the agreed level so that a carrier's average cannot hide it.
---
# Measure on time by lane

When to use: every weekly delivery report, and whenever someone asks how a carrier or a route is doing.

Steps:
1. Take the shipment export for the week. Confirm its columns: order, carrier, service, origin, destination, ship date,
   promised date, delivered date, status, and quantities shipped and received if the export has them.
2. Define the population: shipments delivered in the week. Shipments still in transit are not scored, so they count as neither on time nor late. List them apart, and flag each one past its promised date as overdue for the exceptions queue.
3. Score on time: delivered on or before the promised date. A shipment with no promised date is not scored, and it is
   named.
4. Score in full: every item in the order received, with no missing item reported. If the export has no quantities, say
   that in-full could not be measured.
5. Group by lane: origin region to destination region. A lane with fewer than 10 shipments goes into "other", and the
   count is shown.
6. Report by carrier and by lane, each with its count, on-time rate and in-full rate. For each carrier, show its worst
   lane with that lane's count.
7. Compare each lane with the agreed level in `knowledge/carriers.md`. A lane below the level for two weeks running goes
   to the Operations Manager. One bad week is noted, not escalated.

Checks before you finish:
- Every rate states its count, and the counts add up to the total.
- Lanes under 10 shipments are grouped as "other" and labelled as such.
- Shipments in transit are in no rate; any past its promised date is flagged apart as overdue.
- Each carrier's average sits next to its worst lane.

Example (invented, for illustration): "Parcelway, 301 shipments delivered: 265 on time (88%) and 298 in full (99%). Worst
lane: West to Northeast, 58 shipments, 71% on time. Swiftline, 152 shipments: 147 on time (97%) and 152 in full (100%).
Other lanes, each under 10 shipments: 12 shipments in all, 10 on time."
