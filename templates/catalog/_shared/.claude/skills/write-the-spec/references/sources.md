# Sources

All text in this skill is original. The ideas below were adapted; no wording was copied. Read 2026-11-18.

## The owner's standard

- `answers.md`, Product (Nov 18, 2026): a great spec uses the fewest words that get the idea across, with visuals over text and clarity above all; it can be long if clarity needs it, with no fluff; it is based on real data, real scenarios and real customer requests, with the use cases thought through. This overrides every source below. Where a public source asks for personas, user stories or priority tiers, this skill follows the owner: real cases, and in or out.

## Adapted (MIT or Apache-2.0)

- **Anthropic, `knowledge-work-plugins`, product-management, `write-spec`** (Apache-2.0). https://github.com/anthropics/knowledge-work-plugins/blob/main/product-management/skills/write-spec/SKILL.md
  Taken: non-goals carry a reason each; open questions name who owes the answer; pull context from the team's own records before asking. Not taken: its P0/P1/P2 tiers, its "as a user, I want" stories and its timeline section.
- **mattpocock/skills, `to-spec`** (MIT). https://github.com/mattpocock/skills/blob/main/skills/engineering/to-spec/SKILL.md
  Taken: write the spec from what is already known instead of interviewing again; use the project's own vocabulary throughout. Not taken: its long list of user stories.
- **mattpocock/skills, `grilling`** (MIT). https://github.com/mattpocock/skills/blob/main/skills/productivity/grilling/SKILL.md
  Taken: word a question so "yes" accepts the recommended answer.

## Ideas only (not adaptable text)

- **RefoundAI, `lenny-skills`, `writing-prds`**. https://github.com/refoundai/lenny-skills/blob/main/skills/writing-prds/SKILL.md
  Ideas only, as summarised in `research/product.md` (the skill itself was not re-read): state the problem without a built-in solution; give the draft a clarity review before anyone else reads it. No wording used.
- **Dean Peters, `prd-development`** (CC BY-NC-SA 4.0). https://github.com/deanpeters/Product-Manager-Skills/blob/main/skills/prd-development/SKILL.md
  Read for comparison only. Its ten-section structure is not used.
- **Ryan Singer, Shape Up** (Basecamp, free online). https://basecamp.com/shapeup
  Ideas: the appetite is set before the solution; no-gos are written down.

## Tico

- `templates/catalog/_shared/.claude/skills/build-the-page/` on `origin/main`, read 2026-11-18: the seven page rules, the three spec templates, their section ids, the screenshot and arrow rules, and `page.py` (`build`, `check`, `render`). The word limits quoted in step 8 are the values in `page.py` on that date.
- `templates/catalog/feature-owner/playbooks/write-a-spec-page.md`: the order of the top and the details. This skill replaces it.
- `docs/dev/pipelines-v3.md`, `docs/software-factory.md`, `docs/tasks.md`, `docs/pages.md`: the Spec stage, what its checker reads (goal, scope, criteria), that a person gives the OK and the bot never asks for it, and the commands.
- Catalog skills called as sub-steps: `write-acceptance-criteria`, `write-how-we-will-know`, `define-a-metric-as-a-query`.

## Defaults that are this skill's own

"Three to five real cases", the list of states in the use-case grid, "about fifteen cells" as the sign a feature should be split, and the "Real cases" section are working defaults, not published standards. A team changes them in `knowledge/product.md` or `knowledge/spec-format.md`.
