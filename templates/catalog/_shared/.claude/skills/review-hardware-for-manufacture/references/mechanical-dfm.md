# Mechanical DFM by process

Checks for custom mechanical parts. **The supplier's own design guide and their DFM report on your part always win.**
A moulder or shop will usually return a DFM report before cutting anything: ask for it, and answer every point.

The values below are common rules of thumb, written from general engineering knowledge on 2026-11-18. They depend on
material, part size and supplier. Use them to find things worth raising, and label any finding based on them "rule of
thumb, confirm with supplier".

## First, for any part

1. **Is the process right for the quantity?** Roughly: 3D printing and machining for ones to hundreds; sheet metal for
   tens to thousands; injection moulding once the tooling cost is spread over thousands. Get real quotes at the build
   quantity before deciding; do not assume the crossover.
2. **Is the material a named grade the supplier stocks?** "ABS" is not a specification. A grade, a colour reference
   and any flame rating the product needs are.
3. **Are tight tolerances only where needed?** List every dimension tighter than the process's standard tolerance and
   ask what it is for. Each one adds cost and scrap.
4. **Are critical dimensions marked?** The few dimensions that decide fit or function are flagged on the drawing so
   they are measured on every first article.
5. **Does the drawing agree with the model?** Spot-check five dimensions.

## Injection moulding

| Rule | Rule of thumb | Why |
|------|---------------|-----|
| Draft | At least 1 degree per side on walls in the direction of pull; more on deep or textured walls | The part must release from the tool without drag marks |
| Wall thickness | Uniform; commonly 1.5 to 3 mm for general plastics, per the resin maker's guide | Thick sections sink, warp and cycle slowly |
| Thickness changes | Gradual, not stepped | Abrupt changes cause stress and sink |
| Ribs | About half to 60% of the wall they join; height up to about three times the wall | Thicker ribs leave sink marks on the far face |
| Bosses | Walls about 60% of the nominal wall; tied to a wall with ribs, not merged into it | Thick bosses sink |
| Inside corners | A radius of at least half the wall | Sharp corners concentrate stress and hinder flow |
| Undercuts | Avoid, or accept slides and lifters in the tool | Each one adds tool cost and a failure point |
| Holes | Through holes over blind ones; core pins need support | Long thin pins bend |
| Text and logos | Raised on the part (cut into the tool) | Cheaper to tool, easier to fill |
| Snap fits | Root radius; strain within the resin maker's limit | They break at a sharp root |

Ask of every moulded part:

- Where is the parting line, where are the gate and ejector marks, and are they off cosmetic faces?
- Which faces are cosmetic, and what texture or polish do they get?
- Will it sink opposite ribs and bosses on a visible face?
- How many cavities, and what tool material and life is being quoted?
- Who owns the tool, and where is it kept? (This goes into the purchase terms.)

Tooling is a one-way door. Before releasing a tool: the moulder's DFM report is answered, the part has been printed or
machined and fitted, and the engineer has signed.

## Sheet metal

| Rule | Rule of thumb | Why |
|------|---------------|-----|
| Inside bend radius | About the material thickness or more | Tighter bends crack, especially in hard alloys |
| Hole or slot to bend | At least about 2.5 times thickness plus the bend radius | Closer features distort when bent |
| Minimum flange | About four times thickness | The brake needs something to hold |
| Hole diameter | At least the material thickness | Smaller holes break punches |
| Hole to edge | About twice thickness | Closer holes bulge the edge |
| Bend relief | A notch at least as wide as the thickness where a bend meets an edge | Without it the corner tears |
| Bend radii | The same radius throughout | One tool set-up |
| Grain | Bends across the grain where it matters | Bends along the grain crack |

Also check: one material thickness across the part; welds reachable and on non-cosmetic faces; hardware such as
press-in nuts specified by part number; finish (plating, powder coat) thickness allowed for in fits and in threaded
holes; sharp edges broken.

## Machining

| Rule | Rule of thumb | Why |
|------|---------------|-----|
| Internal corners | A radius, sized so a standard cutter fits; larger for deep pockets | Square inside corners cannot be milled |
| Pocket depth | Up to about four times the width | Long thin cutters deflect and chatter |
| Thin walls | Avoid under about 0.8 mm in metal, 1.5 mm in plastic | They vibrate and deflect |
| Hole depth | Up to about four times diameter is easy; much deeper needs special tools | Chip removal and drill wander |
| Thread depth | About three times diameter is enough | Deeper adds cost and no strength |
| Standard sizes | Drill and thread sizes from standard tables | Odd sizes need special tools |
| Set-ups | Features reachable from as few sides as possible | Each re-fixture adds cost and error |
| Tolerance | The shop's standard unless a feature needs tighter | Tight tolerances multiply cost |

Also check: text engraved rather than raised; no features that need a tool to reach around a corner; stock size close
to the part's envelope; surface finish called out only where it matters.

## Printed and prototype parts

Printed parts are for fit and early function. Do not approve a moulded design from a print alone: draft, sink and
warp do not show up. Check the print service's own design guide for minimum wall and feature size for the process and
material used, and note that printed parts are weaker across layers.

## Die casting, extrusion and others

Each has its own rules (draft, wall, porosity, die lines). If a part uses a process not covered here, mark the
mechanical items "not checked" and ask the supplier for their design guide and DFM report. Do not apply another
process's numbers.

## Writing a mechanical finding

```text
<Severity> · <part number, feature, drawing sheet and dimension or model face>
Measured: <value>   Limit: <value> (<supplier guide link, date read> or "rule of thumb")
Change: <the specific edit, and what it costs to change now versus after tooling>
```
