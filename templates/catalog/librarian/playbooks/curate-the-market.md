# Curate the market

Daily, and on an urgent insight (`playbooks/urgent-market-insight.md`). Read new insights oldest first,
write the graph, then sweep what you could not verify. With no new insight and no listening item, and on a
day that is not Monday, stop at once.

0. Drain the Social Media Manager's inbox first. `hub listening item list --destination market` lists posts the Social Media Manager saved
   and the decision model scored as market facts. For each one, file it as an insight with its listening item id, so a
   second pass never files it twice: `hub market report --kind <kind> --about "<name>" --claim
   "<one sentence>" --source <post url> --quote "<the post's words>" --source-ref <intake id>`.
   A post with no concrete market fact is resolved at once:
   `hub listening item resolve <intake id> --status rejected --reason "<one sentence>"`.
1. Read `market_insights` with status `new`, oldest first (`hub market` queue, or the insights route).
2. Resolve each name: `hub market find` over names, aliases and domains, then judgment. Record the alias you matched so the next mention is a lookup.
3. Decide the shape. An insight may become a new entity, an edge, a property change, an `until` on an edge it contradicts, an alias, or nothing.
4. Write the evidence row first, then the entity or edge that cites it. `hub market apply` does that and closes the insight as applied.
5. Otherwise close it: `merged` into another insight, `rejected` with one sentence, or `needs-human` when two credible sources disagree and it matters.
6. Close the listening items you filed: an insight you applied or merged resolves its listening item
   `accepted` with `--ref <insight id>`; one you rejected resolves it `rejected` with the same
   sentence; one still `new` or `needs-human` leaves it `new`. The insight's `source_ref` is the
   listening item id, which traces back to the post and the sweep that saw it (`hub listening show`).
7. File the run with `hub market sweep`. Every `needs-human` insight from this run becomes one task on the team owner, never one task each.
8. On Mondays the sweep's date is a Monday and the weekly delta page is refreshed from `market_events`.
9. Staleness: an entity past `last_verified` plus 60 days (core tier: 30) that you tried to check and could not verify is named in that same sweep, with what to look for. The server files a task on the Social Media Manager (`listening`) only for those. A date being old is not itself a task.

The sixteen relations, the evidence standard, entity resolution and the page templates are in `knowledge/market/`.
Reporters do not carry a copy.
