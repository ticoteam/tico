# Listening

> **Beta**: Listening works today and may still change. See [Beta features](support.md#beta-features).

Listening saves posts a bot finds, asks the Decision provider to classify them, and routes useful posts to a receiving bot's
inbox. A receiving bot accepts, rejects or marks each post as a duplicate. A score alone does not contact anyone.

[Use Tico](using-tico.md) · [API](api.md) · [Glossary](glossary.md)

## Start with one topic

1. Open **Settings > Bots > Add from template**, choose Listening, and press **Set up** on its page.
   Tell it one topic to watch, the sources to read and what a useful result looks like. Connect any requested Tools and Credentials.
2. Ask your owner or computer operator to configure one destination in the server's `registry/listening.yaml`, as below.
   Choose a receiving bot that already exists, such as Content. Without destinations, posts can be saved and scored but route nowhere.
3. Configure a server Decision provider ([Decision setup](../questions/README.md)). Ask Listening for one sweep, then review the
   receiver's result before changing its Routine. Use `hub listening stats` to distinguish an empty sweep from a blocked source.

```yaml
destinations:
  content:
    category: content
    threshold: 0.75
    receiver: bot:content
    what: a question a useful article could answer
```

Categories are question ids in `questions/listening-item.json`. Optional `readers` can read a destination; only its receiving bot
or the Team owner can resolve it. An optional `unless: {category: vendor_pitch, threshold: 0.70}` excludes posts that clear
that second threshold. Changes affect future decisions; existing inbox rows remain.

To use your company's categories, put a validated question set in the server's
`registry/questions/listening-item.json` (`TICO_REGISTRY_DIR` selects the registry). It takes precedence over the shipped
copy. Both destination and `unless` categories must name `noul` questions; missing or differently typed categories appear
in Health and the server log. An invalid custom set is refused. Increase its `version` when changing questions: the
`id@version` label makes recent saved posts eligible for a new decision without duplicating existing inbox rows.

Registry overrides must be regular JSON files in a real `questions` directory under the configured registry; symlinks
for the registry, question directory or question file are refused. A dangling link, nonfile, unreadable or invalid override
produces a fixed configuration error rather than falling back to shipped questions. Only an absent override falls back.
On platforms without safe no-follow directory opens, existing registry overrides fail closed; absent overrides and
shipped or explicitly selected question directories continue to work.
Unchanged configuration warnings are logged once per process while retained in a bounded cache; changing destinations
or questions rearms them. Health continues showing the current warning to owners and admins on every read.

Health also catches a registry that lost its files, as a server move can. When Listening has sent posts to an inbox
before and saved posts in the last 14 days, but `listening.yaml` is missing, Health shows **Listening** as bad with the
date it last routed a post and how many it has saved since; a file that is present with no valid destinations is a
warning, since a company may empty it on purpose. When the registry has no
`questions/listening-item.json` but posts were scored in the last 30 days with a later version of the set than the
shipped one, Health names both versions so the company's own questions can be restored.

## Save, decide and resolve

These are internal v2 APIs and may change between releases. Only the Listening bot (`bot:listening`) and the Team owner can save
runs and make decisions. Receivers and configured readers can read their own destinations. Use a bot's run credential or the owner's
personal token; every POST has an `Idempotency-Key` for safe retries ([API](api.md)). The bodies below contain fictional data.

1. Save a sweep with `POST /api/v2/listening/runs` (`hub_listening_save`):

   ```json
   {"source":"news","query":"example app onboarding","status":"ok","pages_read":1,"items_seen":1,"items":[{"native_id":"example-1","url":"https://example.com/posts/1","content":"How can a small team write a helpful welcome guide?"}]}
   ```

   Keep `items[0].id` from the response as `<item-id>`. `status` is `ok`, `blocked`, `rate_limited` or `error`; a non-`ok`
   sweep needs a `note`. An `ok` sweep with no items means no results. A saved post is deduplicated by `(source, native_id)`;
   saving it again leaves its original content intact. Each sweep accepts at most 200 posts.
2. Ask for a decision with `POST /api/v2/listening/decide` (`hub_listening_decide`):

   ```json
   {"item_ids":["<item-id>"],"limit":1}
   ```

   `/api/v2/listening/judge` is the older compatible route. This sends saved post text to the configured Decision provider.
   Check `results` and `errors`; a successful HTTP response can contain per-post errors. A per-post error is only a
   code (`auth`, `http`, `invalid`, `not_found`, `shape`, `spend_cap`, `unavailable`, `unconfigured`, `budget`, else
   `unreadable`), never the provider's error text, which can repeat the post. Every destination whose category
   clears its threshold gets one intake row. If none clears, the post remains saved and goes nowhere.
3. As the receiver or owner, read `GET /api/v2/intake?destination=content&status=new` (`hub_listening_item_list`).
   Keep the intake row's `id` as `<intake-id>`; this differs from the saved post id. Resolve with
   `POST /api/v2/intake/<intake-id>/resolve` (`hub_listening_item_resolve`):

   ```json
   {"status":"rejected","reason":"The post has no detail our team can use."}
   ```

   `accepted` and `duplicate` require `receiver_ref`, the id of the record created or already present. `rejected` requires a
   reason. Repeating the same verdict is safe; changing a resolved verdict returns `409 conflict`. Receiving bots poll new rows;
   Tico does not push them.

Trace a post with `GET /api/v2/listening/items/<item-id>`, read sweep history with `GET /api/v2/listening/runs`, and inspect
coverage and receiver verdicts with `GET /api/v2/listening/stats`. Do not treat blocked or rate-limited sweeps as evidence that
no relevant posts exist. See `templates/environment-registry/listening.yaml` for the full routing shape.
