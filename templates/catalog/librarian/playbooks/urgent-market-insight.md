# Urgent market insight

Someone reported an insight with `--urgent` (`hub market report --urgent`). Curate that one insight now, ahead of the
daily pass (`playbooks/curate-the-market.md`).

**Inputs.** The insight id from the task or event that started the run. Read it with
`hub sql "SELECT * FROM market_insights WHERE id = :id" --param id=<insight id>`, then each entity it names with
`hub market find "<name>"`.

Steps:
1. Resolve each name against the graph before you invent an entity (`curate-the-market.md` step 2).
2. Decide its shape: a new entity, an edge, a property change, an alias, or nothing (step 3).
3. Write the evidence row first, then the entity or edge that cites it, with `hub market apply <insight id> ...`. The flags
   are in `playbooks/market-setup.md` step 4. `apply` closes the insight as applied.
4. If it is a duplicate of another insight, or it is not a market fact, close it: `hub market resolve <insight id> --status merged --resolution "<one sentence: the original insight's id and what this one repeats>"`
   or `--status rejected --resolution "<one sentence>"`. The command refuses either without a resolution.
5. If two credible sources disagree and it matters, resolve it `--status needs-human --resolution "<one sentence on the
   disagreement>"` and file the run, so the owner gets one task and not one per insight.
6. If it came from a listening item, resolve that item as `curate-the-market.md` step 6 says, then file the run with
   `hub market sweep`.

Done when the insight is applied, merged, rejected or needs-human, its listening item is resolved, and the sweep is filed.
Urgent is no reason to skip a source: no evidence row, no change to the graph.
