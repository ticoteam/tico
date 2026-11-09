# The book's file format

`reports/brand-book.json` is one JSON object. `audit.py` reads it (`check`) and turns it into the readable book
(`markdown`). Sections follow `template.yaml`: one per `id`, in that order, with the `block` it names.

## Envelope

```json
{
  "lens": "brand-book",
  "artifact": "Brand book",
  "company": "Acme Lights",
  "by": "Ana Ruiz with the Brand Owner",
  "date": "2026-11-05",
  "status": "done",                       // done | in_progress
  "headline": "One sentence: what this artifact says.",
  "summary": "Two or three sentences of context.",
  "sections": [ { "id": "factors", "title": "Buying factors", "block": "list", "data": {}, "note": "optional one line" } ],
  "decisions": ["What changed in our thinking, one line each"],
  "assumptions": ["Beliefs the artifact rests on that nobody has checked"],
  "next_steps": [ { "text": "Call 5 storefront owners about price", "owner": "Eli" } ],
  "proposed_changes": [ { "kind": "bet", "text": "..." } ],   // kind: bet | positioning | segment | goal | summary
  "resume": null                          // or "Next: score rivals on setup time. Open: is 'do nothing' a rival?"
}
```

A section that isn't done yet adds `"open": "why it's open"` and may have empty data; the page shows it as open.

## Blocks used by the book

Text values are plain strings. A table cell or list item may be `{ "text": "...", "tag": "assumption" }`; tags are
`evidence`, `assumption`, `risk`, `gap`, `ok`, `no`.

| block | data |
|---|---|
| `fields` | `{ "items": [ { "label": "Purpose", "value": "..." } ] }` |
| `table` | `{ "columns": ["Value", "What we do"], "rows": [["...", "..."]] }` |
| `list` | `{ "items": ["...", { "text": "...", "tag": "assumption" }] }` |
| `meters` | `{ "max": 10, "items": [ { "label": "Formal ←→ Casual", "score": 7, "text": "the founder's reason" } ] }` |

Columns the audit reads by name: claims `Type` ("Allowed (A1)" or "Never"), `Catch`; words `Kind`, `Word`, `Level`;
naming `Right form` (second column) and wrong forms seen (last column); customer words `Source`; messages `Proof`;
examples `Text` and a verdict cell "on" or "off"; Look rows `Colour` (name and hex) and `Contrast` ("Night on snow",
"15.91:1 pass").
