---
name: keep-numbers-fresh
description: Keep Growth's numbers current without forms. Use in the weekly numbers run and when a person source goes stale - re-ask the person who gave a number once a month closes, and after two months from the same person offer once to read it from a system instead.
---
# Keep numbers fresh

## Steps
1. `tico growth source list` names the sources (reach kind `person` ones name who gives the number)
   and which are stale or failing; `tico growth cohort list --month <YYYY-MM>` for the last full month
   shows which channels have no number yet.
2. For each, ask that person one question in chat (`tico chat ask --to <person id> "..."`), naming
   last month's figure: "Google Ads in October was $4.2k and 31 new. What about November?" Save the
   answer with `skills/numbers-from-chat/SKILL.md`.
3. The second month the same person gives the same number, offer once: "Want me to read this from
   <system> instead?" On yes, follow the system's connection steps and add it as a source
   (`tico growth source add --truth-for "<what>" --system "<system>" --reach-kind mcp`; a number it should
   fetch by itself also needs a sync kind and mapping, see `docs/growth.md`). On no, record it in
   `memory/decisions.md` and do not offer again.
4. A source that has failed twice: ask its owner one question about it, not the whole team.
5. Never ask anyone more than one fresh-number question a week.
