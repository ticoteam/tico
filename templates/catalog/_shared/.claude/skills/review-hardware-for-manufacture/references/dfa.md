# Design for assembly

Two questions, in this order: can a part be removed, and can what is left be put together fast and only one way.

## 1. List the assembly

From the top-level drawing or exploded view, write every part in the order it is assembled, including fasteners,
labels, cables, tape and glue. Count each fastener. This list is the base for everything below.

| # | Part | Qty | Moves? | Different material? | Must come apart? | Keep / candidate |
|---|------|-----|--------|---------------------|------------------|------------------|

## 2. The three minimum-part questions

For each part, ask (after Boothroyd and Dewhurst):

1. **Does it move** relative to the parts already assembled, when the product is in normal use?
2. **Must it be a different material**, or isolated from them, for a real reason (electrical insulation, heat, wear,
   transparency)?
3. **Must it be separate** so that other parts can be assembled, or so it can be taken out for service?

A part with "no" to all three is a **candidate**: in theory it could be merged into a neighbour or removed. Fasteners
almost always come out as candidates.

A candidate is a question for the designer, not an order. For each, write the specific alternative:

| Candidate | Alternative |
|-----------|-------------|
| Screws holding a lid | Snap hooks moulded into the lid; one screw if it must resist tampering |
| A separate bracket | A boss or rib moulded into the housing |
| A label | Text moulded or laser-marked into the part |
| A cable between two boards | A board-to-board connector, or one board |
| A separate light pipe, gasket or button | A two-material moulded part, where volume justifies the tool |
| Washers and nuts | Screws with captive washers; threaded inserts or thread-forming screws |

Check each alternative against cost at the build quantity and against repair needs before proposing it. Merging parts
usually raises tooling cost and lowers assembly cost; at low volume the trade can go the other way.

## 3. A rough efficiency number

Use it to compare revisions of the same product, not to compare products.

```text
Theoretical minimum parts  = parts that answered "yes" to any of the three questions
Assembly efficiency        = (theoretical minimum parts × 3 seconds) ÷ estimated total assembly time
```

Three seconds is Boothroyd and Dewhurst's figure for an ideal part that is easy to handle and insert. The total time
should come from a timed build if one exists. If it does not, do not invent it: report the part counts only (total
parts, theoretical minimum, fastener count and fastener types) and ask for a timed build of five units.

## 4. How it goes together

Check each, and record a finding for each "no":

**Handling**

- Can each part be picked up with one hand, without tools?
- Is each part either clearly symmetric (fits either way) or clearly asymmetric (obviously fits one way)? The bad case
  is nearly symmetric.
- Do parts tangle, nest or stick together in a bin (springs, clips, washers)?
- Are there flexible parts (cables, gaskets, films) that need to be held in place while something else is fitted?

**Insertion**

- Is everything assembled from one direction, ideally from above, onto one base part?
- Do parts locate themselves with chamfers, pins or ribs before they are fastened?
- Does each part stay put once placed, or must it be held while the next goes on?
- Can the assembler see and reach what they are doing?

**Mistake-proofing**

- Can any part be fitted the wrong way round, upside down, or in another part's place? If yes, add a key, an offset
  hole or a different connector.
- Do connectors of the same type sit side by side? Use different pin counts or keying.
- If a part is left out, is it obvious before the product is closed?
- Are left-hand and right-hand parts identical where they can be, and unmistakable where they cannot?

**Fastening**

- How many fastener types and drivers? Aim for one screw and one driver.
- Is every fastener reachable with the tool straight on?
- Is torque specified where it matters?
- Is there any glue, tape or cure time? Each adds process control and waiting.
- Is there any adjustment or calibration during assembly? Design it out or make it a test step.

**Service**

- If the requirements call for repair or battery replacement, can the product be opened without breaking it? (In the
  European Union, portable batteries in products must be removable and replaceable by the end user from 18 February
  2027 under Regulation (EU) 2023/1542, with exceptions. Checked 2026-11-18; read the current text.)

## 5. Write the assembly order

Number the steps as the line would do them, each with the part, the action, the tool and what "right" looks like. If a
step needs a part removed again, three hands, or a look inside a closed housing, that is a finding. This draft becomes
the assembler's work instruction and, later, an SOP.

## Writing an assembly finding

```text
<Severity> · <part and step number>
What can go wrong: <the wrong assembly, the extra part, or the slow step>
Change: <the specific design change>   Saves: <parts removed, fastener types removed, or a step removed>
```
