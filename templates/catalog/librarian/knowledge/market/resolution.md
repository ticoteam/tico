# Entity resolution

Before a new entity, `hub market find` the name, then the domain.

- Match on the entity name, any alias, and `external_ids.domain`.
- If one entity matches, this is an update or an alias, not a discovery. Add the reporter's wording as an alias so the next mention is a lookup.
- If two entities match, do not pick. Resolve the insight `needs-human` and say which two.
- A create whose alias or domain already resolves is refused unless you pass force. Force is for a real second entity that shares a word, not for a near duplicate.
- Ids look like `company/example-co`, `person/jane-doe`, `segment/independent-shop-owners`, `channel/r-smallbusiness`.
- Merged entities stay. New mentions resolve to `merged_into`.

Record the alias you matched in the event note so the next run can see why.
