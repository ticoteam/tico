# Circuit board DFM

Checks for the bare board and its assembly. **The fab's and assembler's own published limits always win.** Read them
first and write them into the review with a link and date.

The "typical" values below are rules of thumb for a mainstream fab's standard service, written from general
engineering knowledge on 2026-11-18. They were not taken from any one fab's page and they vary by supplier and by
price tier. Use them only to spot things worth checking when no supplier is chosen, and label any finding based on
them "rule of thumb, confirm with fab".

## How to read the design

You are reading exports, not running the CAD tool. Use, in order:

1. The designer's design-rule-check report from the CAD tool, run with the fab's rule file. Ask for it. It is the only
   full check of width and spacing. Without it, item 2.1 of the gate is "not checked".
2. The fabrication notes and drill table.
3. The Gerber, drill and placement files: these are text. You can read apertures, drill sizes, board extents and part
   positions from them, and render them with a viewer if one is available.
4. The BOM and each part's datasheet for footprints.

Say plainly which of these you had. A review from PDFs alone is a partial review.

## Bare board

| Check | Typical standard limit | What to look for |
|-------|------------------------|------------------|
| Trace width and spacing | About 0.15 mm (6 mil); 0.10 mm is common at higher cost | The smallest aperture used for traces; spacing from the designer's report |
| Finished hole size | About 0.3 mm smallest; 0.2 mm at higher cost | The drill table's smallest size |
| Annular ring | About 0.15 mm of copper around a hole | Pad diameter minus hole diameter, halved |
| Aspect ratio | Board thickness to smallest hole about 8:1 | Thickness divided by smallest drill |
| Copper to board edge | About 0.3 mm for routed edges; more beside a V-score | Pours and traces near the outline |
| Hole to hole, hole to copper | Per fab | Close vias and slots |
| Solder mask dam between pads | About 0.1 mm | Fine-pitch parts: is there mask between pads, or one open window? |
| Silkscreen | Line about 0.15 mm, text height about 1 mm | Tiny text, text over pads or holes |
| Board thickness and copper | 1.6 mm and 1 oz are the usual defaults | Anything else: does the fab stock it? |
| Layer count and stack-up | Stated, symmetric | An unbalanced stack-up warps |
| Finish | Stated (for example ENIG, HASL lead-free) | Fine pitch and flat pads want a flat finish; must suit the markets' materials rules |
| Controlled impedance | Stated per line with target and tolerance | If any line needs it and the notes do not say so, that is a major |
| Slots, cut-outs, plated edges | Drawn on the outline layer and called out | Features a fab would miss if only in a note |

Also check:

- **Copper balance.** Large empty areas on one side of a layer and solid copper on the other can warp the board and
  etch unevenly. Ask for copper fill where it is safe.
- **Acid traps and slivers.** Sharp inside angles and thin leftover copper or mask strips. The designer's report shows
  them.
- **Vias.** State whether vias are covered by mask (tented), plugged, or open. Open vias under parts can wick solder.
  A via in a pad needs "filled and capped" in the notes.
- **Outline.** Closed, with no stray lines, and internal corners with a radius the router can cut.

## Footprints

For each part type, at least each integrated circuit, connector and anything not a standard two-pin part:

1. Open the manufacturer's datasheet and find the recommended land pattern.
2. Compare pad size, pitch, row spacing and body outline with the footprint in the files.
3. Check pin 1 and polarity marks on silkscreen match the datasheet's orientation and the pick-and-place rotation.
4. Check the exposed pad under leadless parts has paste openings split into smaller windows if the datasheet asks.
5. Check the part's height fits the enclosure.

A wrong footprint is the most common reason a first board cannot be built. Treat any mismatch as a blocker.

## Assembly

| Check | Typical | What to look for |
|-------|---------|------------------|
| Fiducials | Three on the board or panel, about 1 mm copper dot with a clear area around it; extra beside fine-pitch parts | None present is a major |
| Part to board edge | About 3 mm clear, or add break-off rails | Parts on the edge with no rails |
| Part to part | Per assembler; tall parts shadow short ones | Small parts tucked beside tall connectors |
| Both sides | One side placed is cheapest | Heavy or tall parts on the second side |
| Mixed technology | Through-hole parts add a step | Could the through-hole part be surface-mount? |
| Orientation | Polarised parts of a type facing the same way | Diodes and capacitors facing at random |
| Thermal relief | Spokes on pads tied to planes | Pads flooded into copper will solder poorly |
| Two-pin balance | Equal copper on both pads of small parts | One pad on a pour and one on a thin trace lifts the part on end |
| Paste layer | Present, matches pads | Missing paste layer |
| Panel | Follows the assembler's panel rules | Board too small to convey alone with no panel drawn |

Special handling to list in the package:

- **Moisture-sensitive parts.** Note each part's moisture sensitivity level from its datasheet; the assembler must
  bake or use them within the floor life.
- **Parts that cannot be washed or reflowed** (some microphones, buzzers, batteries, switches).
- **Hand-fitted parts**, with a drawing.
- **Programming** before or after assembly.
- **Coating, underfill or glue.**

## Standards to name, not to quote

These are the industry references. Name the one that applies in a finding; do not quote limits from memory. The team
needs its own licensed copy to rely on one.

- IPC-2221: generic board design. IPC-7351: surface-mount land patterns.
- IPC-6012: qualification and performance of rigid boards.
- IPC-A-600: acceptability of bare boards. IPC-A-610: acceptability of assemblies, in Class 1 (general), Class 2
  (dedicated service) and Class 3 (high reliability). State the class in the fabrication notes; Class 2 is the usual
  choice for consumer and commercial products.
- J-STD-001: soldering requirements. J-STD-020 and J-STD-033: moisture sensitivity classification and handling.

## Writing a board finding

```text
<Severity> · <reference designator or net, layer, coordinates if known>
Measured: <value>   Limit: <value> (<fab page link, date read> or "rule of thumb")
Change: <the specific edit>
```
