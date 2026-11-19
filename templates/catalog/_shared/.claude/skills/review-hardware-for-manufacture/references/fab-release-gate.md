# The fab release gate

A fixed list, run the same way every time, ending in one verdict. Copy this file into
`reports/hardware/<product>-<revision>-gate.md` and fill every line.

Mark each item **pass**, **fail**, **not checked** or **n/a**, with evidence (the file, the measured value, the link).
Items marked **[B]** are blocker-class: a fail is a blocker, and "not checked" means the verdict is Hold.

## Header

```text
Product:            Revision:            Build stage: EVT / DVT / PVT
Quantity this build:                     Suppliers: fab / assembler / moulder / shop
Reviewed by:        Date:                Compared with gate of: <revision, date>
Verdict: Release / Release with conditions / Hold
```

## 1. Package

| # | Item | Class |
|---|------|-------|
| 1.1 | Every file is from the same design revision, and the revision is marked in the files (silkscreen, title block, model property) | [B] |
| 1.2 | Board: Gerber layers for every copper, mask, paste and silkscreen layer plus the outline, or one ODB++/IPC-2581 file | [B] |
| 1.3 | Board: drill files, plated and non-plated separate, with a drill table | [B] |
| 1.4 | Board: fabrication notes: material, thickness, copper weight, finish, mask and silk colour, layer order, controlled-impedance lines if any, acceptance class | [B] |
| 1.5 | Assembly: BOM with manufacturer and full MPN for every line, quantity, reference designators, and "do not fit" parts marked | [B] |
| 1.6 | Assembly: pick-and-place file with position, rotation and side for every placed part | [B] |
| 1.7 | Assembly: assembly drawing showing polarity, pin 1 and any hand-fitted parts | |
| 1.8 | Schematic as PDF at the same revision | |
| 1.9 | Mechanical: a 2D drawing for every custom part with dimensions, tolerances, material, finish, colour and critical-to-function dimensions marked | [B] |
| 1.10 | Mechanical: a 3D model (STEP) matching the drawing | [B] |
| 1.11 | Top-level assembly drawing or exploded view with a parts list | |
| 1.12 | Firmware image and version for programming, and the programming method | DVT, PVT: [B] |
| 1.13 | Test plan with pass limits for each test | DVT, PVT: [B] |
| 1.14 | Packaging and label artwork | PVT: [B] |

## 2. Circuit board (detail in `pcb-dfm.md`)

| # | Item | Class |
|---|------|-------|
| 2.1 | The CAD tool's design-rule check was run with the chosen fab's rules and reports zero errors (ask the designer for the report) | [B] |
| 2.2 | Minimum trace width and spacing are within the fab's limits for the chosen service | [B] |
| 2.3 | Minimum drill, annular ring and hole-to-copper distances are within limits | [B] |
| 2.4 | Copper, parts and holes keep the fab's distance from the board edge and from any V-score or tab | |
| 2.5 | Solder mask openings and the slivers between them are within limits | |
| 2.6 | Silkscreen is legible, off pads, and marks polarity and pin 1 | |
| 2.7 | Stack-up, thickness and copper weight are stated and are ones the fab offers | [B] |
| 2.8 | Each footprint matches its part's datasheet (pad size, pitch, body outline, pin 1) | [B] |
| 2.9 | Fiducials: three on the board or panel; local ones for fine-pitch parts | |
| 2.10 | Part-to-part and part-to-edge spacing meets the assembler's limits | |
| 2.11 | Thermal relief on pads tied to large copper; small two-pin parts have balanced copper | |
| 2.12 | No open via in a pad unless filled and capped is specified | |
| 2.13 | Panel drawing or the assembler's panel rules followed; rails and tooling holes where required | |
| 2.14 | Parts needing special process are listed: moisture-sensitive, hand-solder, press-fit, selective solder, underfill, coating | |

## 3. Mechanical parts (detail in `mechanical-dfm.md`)

| # | Item | Class |
|---|------|-------|
| 3.1 | Each custom part names its process and material grade | [B] |
| 3.2 | The process's main rules are met (draft and wall for moulding; bend radius and hole-to-bend for sheet metal; corner radii and reach for machining) | |
| 3.3 | Tolerances are within what the process holds as standard; tighter ones are marked critical and justified | |
| 3.4 | Critical fits were checked with a tolerance stack-up (ask the designer; otherwise "not checked") | DVT, PVT: [B] |
| 3.5 | Cosmetic surfaces are marked, with finish and colour reference; gate, ejector and parting-line marks are placed off them | |
| 3.6 | The moulder's or shop's own DFM report has been received and its points answered | Tooling release: [B] |

## 4. Assembly (detail in `dfa.md`)

| # | Item | Class |
|---|------|-------|
| 4.1 | Minimum-part questions asked for every part; candidates to remove are listed | |
| 4.2 | No part can be fitted the wrong way round, in the wrong place, or left out without it being obvious | |
| 4.3 | Fasteners: few types, one driver, reachable with the tool | |
| 4.4 | Cables and connectors are keyed and reach without strain | |
| 4.5 | The assembly order is written and has no step that needs three hands or a part removed again | |
| 4.6 | The product can be opened for rework or repair without destroying it, if the requirements say so | |

## 5. Test

| # | Item | Class |
|---|------|-------|
| 5.1 | Test points on every power rail and key signal, reachable from one side | |
| 5.2 | A programming and debug connection the line can reach | DVT, PVT: [B] |
| 5.3 | Each unit carries a serial number that test results are recorded against | PVT: [B] |
| 5.4 | Results from the previous build are attached: yield, top failures, and what changed because of them | DVT, PVT: [B] |

## 6. Bill of materials (detail in `bom-risk.md`)

| # | Item | Class |
|---|------|-------|
| 6.1 | Every line has a real, complete MPN that resolves at the manufacturer or an authorised distributor | [B] |
| 6.2 | No line is obsolete or past its last-buy date | [B] |
| 6.3 | No line is "not recommended for new designs" without a written plan | |
| 6.4 | Every line has stock or a lead time that fits the build date, read today | [B] |
| 6.5 | Every single-source line has an alternate or a written plan | |
| 6.6 | Materials compliance for the markets is on file or requested for each part (for the EU, RoHS and REACH) | DVT, PVT: [B] |
| 6.7 | Country of origin recorded for the lines that drive cost | |

## 7. Compliance by design (flags only)

| # | Item | Class |
|---|------|-------|
| 7.1 | Radio modules are used within their datasheet and grant conditions: antenna type, layout, keep-out | |
| 7.2 | Mains and battery circuits state the spacing rule they were laid out to; fusing and protection are present | Engineer: [B] |
| 7.3 | Outside-facing connectors and buttons have ESD protection | |
| 7.4 | The enclosure and label have room for the marks the markets need | |
| 7.5 | A certification plan exists or has been asked for (`source-and-certify-hardware`) | DVT: [B] |

## Verdict rules

- **Hold**: any blocker; or any [B] item "not checked".
- **Release with conditions**: no blockers; every major has an owner and a condition that will be true before parts
  ship. List the conditions under the verdict.
- **Release**: no blockers, no majors.

How strict by stage: at EVT the gate asks "will it build and can we learn from it"; test and compliance items are
majors at most. At DVT every item applies. At PVT nothing new should appear; any blocker means the design changed
after validation, which is itself a finding.

## Diff against the last run

```text
Fixed since <revision>:   <items>
New this run:             <items>
Still open:               <item> (open for <n> runs)
```
