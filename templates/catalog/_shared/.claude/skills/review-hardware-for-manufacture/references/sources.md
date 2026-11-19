# Sources

What this skill drew from. Ideas only; all wording is original. Checked 2026-11-18.

## Skills and tools

- aklofas/kicad-happy (MIT), fab release gate and BOM skills: a fixed gate that ends in a verdict and is diffed
  against the last run; BOM sourcing checks. Ideas adapted: the gate, the verdict, the diff.
- earthtojake/text-to-cad (MIT): checking a part against a named process and reporting the specific change needed.
  Idea adapted: every finding carries the change.
- lenny-skills and borghei/Claude-Skills: nothing used for this skill.

## Design for assembly

- Geoffrey Boothroyd, Peter Dewhurst and Winston Knight, *Product Design for Manufacture and Assembly* (third edition,
  2010): the three criteria for the theoretical minimum number of parts, and the assembly efficiency index based on
  three seconds per ideal part. Ideas used: both. The handling and insertion time tables are not reproduced.
- Shigeo Shingo's mistake-proofing (poka-yoke): design so the wrong assembly is impossible. Idea used: the
  mistake-proofing checks.

## Circuit boards

- IPC standards, named only: IPC-2221, IPC-7351, IPC-6012, IPC-A-600, IPC-A-610, J-STD-001, J-STD-020, J-STD-033. No
  limits are quoted from them; they are paid documents and were not read for this skill.
- "Typical" board and assembly values come from general knowledge of mainstream fabs' published standard
  capabilities. They were not taken from one supplier and were not re-verified on 2026-11-18. The skill tells the bot
  to read the chosen fab's page instead.

## Mechanical parts

- Injection moulding, sheet metal and machining rules of thumb are the commonly published guidelines of on-demand
  manufacturers and resin makers (for example the public design guides of Protolabs, Hubs and Xometry). Written from
  general knowledge; not re-verified on 2026-11-18; labelled as rules of thumb throughout.

## Build stages

- EVT, DVT, PVT: the stage names in common use across consumer-electronics manufacturing. No single source.

## Law and dates

- Regulation (EU) 2023/1542 on batteries, Article 11: portable batteries removable and replaceable by the end user
  from 18 February 2027. Checked 2026-11-18 against secondary summaries.
- RoHS (Directive 2011/65/EU) and REACH (Regulation (EC) No 1907/2006): named as the EU materials rules to hold
  declarations for. Details are in the `source-and-certify-hardware` skill.

## Not verified

- Any numeric limit in `pcb-dfm.md` and `mechanical-dfm.md`. Each is a starting point for a question to the supplier.
