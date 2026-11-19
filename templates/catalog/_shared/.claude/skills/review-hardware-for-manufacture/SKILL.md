---
name: review-hardware-for-manufacture
description: Review a hardware design before it is sent to be made. Checks the release package is complete, runs design-for-manufacture checks on circuit boards against the chosen fab's own published limits and on mechanical parts by process (injection moulding, sheet metal, machining), runs design-for-assembly (can parts be removed, can it be put together wrong), checks the design can be tested, and scores bill-of-materials risk (lifecycle, single source, lead time, stock, alternates). Ends in a fab release gate with one verdict, Release, Release with conditions or Hold, compared with the last run, and for every failed item says exactly what to change. Use when a task says DFM, DFA, design for manufacture, design review, fab release, release to fab, gerbers, PCB, PCBA, board review, BOM review, BOM risk, obsolete or end-of-life part, tooling release, tool kick-off, mould, enclosure, EVT, DVT, PVT, pilot build, or "is this ready to build".
metadata:
  function: hardware
  roles: [engineering-manager, qa-owner, quality-inspections, procurement-manager, product-manager]
  actions: [read, draft, change-records]
  review: professional
---
# Review hardware for manufacture

When to use: before design files go to a board fab, an assembler, a moulder or a machine shop; before money is
committed to tooling; at each build stage (first prototype, design validation, production validation); and whenever a
bill of materials is about to be bought.

Words used here: **DFM** (design for manufacture) asks whether each part can be made reliably and cheaply by the
chosen process. **DFA** (design for assembly) asks whether the parts go together quickly and only the right way. A
**fab** makes bare circuit boards; an **assembler** places and solders the components (the result is a **PCBA**). The
**BOM** is the bill of materials: every part with its manufacturer and manufacturer part number (**MPN**). The
**release package** is the exact set of files a supplier builds from. **Tooling** is the custom equipment a process
needs, such as an injection mould. The **release gate** is the fixed checklist that ends in a verdict. Build stages:
**EVT** (engineering validation: does it work), **DVT** (design validation: does the final design meet every
requirement, made by the real processes), **PVT** (production validation: can the line make it at rate and yield).

On `review: professional`: this skill is a checker, not a sign-off. It finds what the files and the public data show.
It does not replace an engineer's judgment on safety, thermal, signal-integrity or structural questions. A qualified
engineer reviews any product that plugs into mains power, contains a lithium battery, carries a safety function or is
for children, and any release that commits tooling money.

## What great looks like

- **Problems are found on paper.** A finding at release costs a file change. The same finding after tooling costs a
  mould change and weeks. After shipping, it costs a recall.
- **The design is checked against the real process.** Limits come from the named supplier's own published
  capabilities, read and dated, not from memory. Where no supplier is chosen yet, conservative rules of thumb are
  used and labelled as such.
- **Every finding says what to change.** "Trace too close to edge" is half a finding. "Move the trace on net VBUS at
  C14 to at least 0.3 mm from the board edge, or ask the fab for their minimum" is a finding.
- **Fewer parts.** The best assembly review removes parts. Each part that goes is one fewer to buy, stock, place,
  inspect and fail.
- **The BOM can actually be bought.** Every line is a real, active part, with stock or a lead time that fits the
  build, and a second option where it matters.
- **One verdict.** The review ends with Release, Release with conditions, or Hold, and the same gate run next time
  shows what changed.

## First time at a company

Read `knowledge/hardware-build.md`. If it has an "Our settings" section, skip this. If not, ask the owner these in one
numbered message, save the answers there with the date, and work on the defaults until they reply.

1. **What is the product and what stage is it at?** Default: read the design files and the build history; with no
   earlier build on record, treat it as EVT.
2. **How many per build and per year?** Default: the quantity in the task. With none given, review for 100 units and
   flag every finding whose answer changes above 1,000.
3. **Who makes it?** The fab, the assembler, the moulder or shop, each with a link to its capabilities or design-guide
   page. Default: none chosen. Then use the rules of thumb in the references, label each as a rule of thumb, and add
   the finding "supplier not chosen: confirm limits before release".
4. **Where are the design files, and in what form?** Default: in the product's repo or team docs, as exports you can
   read: Gerber and drill files or ODB++/IPC-2581, the BOM as CSV, the pick-and-place file, the schematic as PDF,
   mechanical drawings as PDF and models as STEP. If only native CAD files exist that you cannot open, ask for
   exports. Do not guess at what a file contains.
5. **Where will it be sold, and what is inside?** Radios, a battery, mains power, parts that touch skin or food.
   Default: United States and European Union; contents read from the BOM. This sets the material and safety checks.
6. **Who is the engineer who signs a release?** Default: the owner names one. Until then, a release that commits
   tooling or exceeds the budget can be no better than "Release with conditions: engineer sign-off".

## Steps

1. **Collect the release package and pin it.** List every file with its name, revision and date. The package for each
   kind of part is in `references/fab-release-gate.md`. All files must come from the same design revision; a BOM at
   revision C beside Gerbers at revision B is a Hold on its own. Record the list on the task.
2. **Read the requirements.** What the product must do, the environment it lives in, the target cost and quantity,
   and the markets. If none are written, write the five lines you can infer and mark them "assumed, confirm".
3. **Read the supplier's limits.** Open each chosen supplier's capability or design-guide page. Write the limits you
   will check against into the review, with the link and the date read. If the build uses a paid-for capability class
   (finer traces, more layers, tighter tolerance), say which.
4. **Circuit board DFM.** Work through `references/pcb-dfm.md`: board outline and stack-up; traces, spaces, holes and
   rings against the fab's limits; solder mask and silkscreen; copper balance; panel and edges; footprints against
   each part's datasheet land pattern; assembly needs (fiducials, part spacing, orientation marks, one-sided where
   possible); thermal relief; parts that need special handling. Record each check as pass, fail, not checked or not
   applicable, with the evidence.
5. **Mechanical DFM.** For each custom part, name its process and work through that process's section of
   `references/mechanical-dfm.md`: draft, wall thickness, ribs and bosses, undercuts and gate marks for moulding; bend
   radius, hole-to-bend distance and flange length for sheet metal; internal corner radii, pocket depth, thin walls
   and number of set-ups for machining. Check the tolerances asked for are ones the process holds without extra cost,
   and that tight ones are only on features that need them.
6. **Design for assembly.** Follow `references/dfa.md`. List every part in the top-level assembly, in order of
   assembly. For each, ask the three minimum-part questions; a part that answers "no" to all three is a candidate to
   remove or merge. Then check how it goes together: one direction of assembly, parts that locate themselves, parts
   that cannot be fitted the wrong way round, few fastener types, no adjustments, cables that only fit one connector.
7. **Design for test.** Can each unit be proven good on the line? Check for test points on power rails and key
   signals, a way to program and identify each unit, a written test plan with pass limits, and a way to record
   results by serial number. A board that cannot be tested is a major finding at DVT and a blocker at PVT.
8. **BOM risk.** Follow `references/bom-risk.md`. For every line: confirm the MPN is real and complete; read its
   lifecycle status from the manufacturer or an authorised distributor; read stock and lead time today; count
   independent sources; check for an approved alternate; compare the pack quantity with the build quantity. Score
   each line green, yellow or red. Read live data through the team's parts Tool if one is connected, otherwise from
   the distributor's own page, and date every reading.
9. **Flag compliance-by-design items.** Not a certification review; that is the `source-and-certify-hardware` skill.
   Here, only flag what the files show: a radio module used outside its datasheet's antenna and layout conditions;
   mains or battery circuits with no stated spacing rule; no ESD protection on outside-facing connectors; no place on
   the enclosure for required marks.
10. **Run the fab release gate.** Fill `references/fab-release-gate.md`, every item, in order. Each finding gets a
    severity: **blocker** (it will not build, will not work, or is unsafe), **major** (it will build with scrap, cost,
    delay or risk), **minor** (worth fixing at the next revision). Then the verdict:
    - **Hold**: any blocker, or any gate item that is blocker-class and "not checked".
    - **Release with conditions**: no blockers; each major has an owner and a condition that is true before parts
      ship (a fab confirmation, a part swap, a waiver by the engineer).
    - **Release**: no blockers and no majors.
11. **Compare with the last run.** Read the last gate for this product in `reports/hardware/`. List what was fixed,
    what is new, and what is still open and for how many runs. A finding open for three runs goes to the top.
12. **Write what to change.** For every blocker and major: the exact location (reference designator, net, feature,
    drawing dimension), what is wrong with its measured value and the limit, and the specific change. Where there are
    two ways to fix it, say which is cheaper at this stage.
13. **Publish and hand on.** Publish the gate page. File one task per blocker and major to the design's owner
    (`tico task create --owner <owner> --title "<what to change>"`, with the finding in the body). On
    Release, hand the pinned package to the `source-and-certify-hardware` skill for quotes and orders. Save the gate
    as `reports/hardware/<product>-<revision>-gate.md`.

## Gates

- **One revision.** Every file in the package is from the same design revision, and the review names it.
- **Limits are read, dated and linked.** A limit from memory is a rule of thumb and is labelled so. No finding says
  "the fab can't" without the fab's page or reply.
- **Not checked is not passed.** If a file could not be opened or a check needs a tool you do not have (a design-rule
  check inside the CAD tool, a tolerance stack-up, a thermal or signal simulation), the item is "not checked" and says
  who can check it.
- **No part data is invented.** Lifecycle, stock, lead time and price come from a named source read today. A part you
  could not look up is "not read".
- **No release with an obsolete or unbuyable part** on the BOM, and no release with a single-source red line that has
  no written plan.
- **Tooling is a one-way door.** No tooling release without an OK and the engineer's sign-off, whatever the verdict.
- **You do not edit the design files.** You say what to change. The design's owner changes it and the gate runs again.
- **Safety is never waived by a bot.** A finding on mains, batteries, heat, or a product for children can only be
  closed by the engineer.

## Autonomy

Do on your own: gather and pin the package, read suppliers' pages, run every check, ask a supplier a factual question
about their limits, score the BOM, give the verdict, publish the page, file the fix tasks, and re-run the gate when a
fix lands. For a prototype board order inside your budget with a Release verdict, hand straight to sourcing.

Ask for an OK, once, with the gate page attached:

- any release that commits tooling money, or spend above the budget;
- a Release with conditions where a condition is a waiver;
- sending design files to a supplier that has no confidentiality agreement with the team;
- anything safety-related that the engineer has not signed.

## Output

- **The gate page.** The verdict first, in one line. Then a table of findings sorted by severity (location, what is
  wrong, measured value against limit, what to change, owner). Then the BOM risk table with colour and the count of
  red lines. Then the diff against the last run. Then the full gate checklist. Add a marked-up image of the board or
  part when a finding is about a place: a picture with a circle beats a paragraph. Publish with
  `tico page publish reports/hardware/<product>-<revision>-gate.html --title "<Product> rev <X>: fab release gate"
  --task <task id>`.
- **Tasks**, one per blocker and major.
- **`reports/hardware/<product>-<revision>-gate.md`**, the record the next run is compared with.
- **`knowledge/hardware-build.md`**, updated with supplier limits and the dates they were read.

## Checks before you finish

- The package list names one revision, and every file in it was opened.
- Every limit used has a link and a date, or the words "rule of thumb".
- Every gate item is pass, fail, not checked or not applicable. None is blank.
- Every blocker and major has a location, a measured value, a limit and a specific change.
- Every BOM line has a lifecycle status and a stock or lead-time reading dated today, or "not read".
- The verdict follows the rules: no Release with a major open, no Release with conditions with a blocker open.
- The diff against the last run is there, or the page says this is the first run.
- Nothing was sent outside the team that needed an OK.

## Example

Invented for illustration. The product, parts and numbers are made up.

**Trailhead sensor, revision C: Hold.** One blocker, two majors, three minors. Last run (revision B): four blockers.

| Sev | Where | What is wrong | Change |
|-----|-------|---------------|--------|
| Blocker | U3 (battery charger), BOM line 14 | Manufacturer page lists the part as "not recommended for new designs"; no stock at three authorised distributors, read today | Swap to the pin-compatible alternate the manufacturer names on the same page; confirm footprint |
| Major | Board edge near J2 | Copper 0.15 mm from the edge; fab's page gives 0.3 mm minimum for routed edges (link, read today) | Pull the ground pour back to 0.3 mm |
| Major | Enclosure lid, four M2 screws | Screws answer "no" to all three minimum-part questions | Replace with two snap hooks and one screw; the base already has the wall for hooks |
| Minor | Silkscreen, R12 to R19 | Text height 0.6 mm, under the fab's 0.8 mm minimum | Enlarge or remove |

Fixed since revision B: fiducials added; programming header added; antenna keep-out restored. Not checked: thermal rise
of U3 at full charge current, needs the engineer. Tasks 3301 to 3303 filed to the design's owner.
