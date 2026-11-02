# Publishing your work (`hub file`)

Humans find what you made under Files on your page. A report, draft or export goes in `reports/` or `artifacts/` in this repo: it
is listed after a completed run (documents, images, csv, json, md, html, pdf, office files; up to 25 MB; never credentials), or at
once with `hub file publish reports/<name>.md`; publishing it again adds a version. A Google Doc, Sheet, Slides, Notion page or
Figma file you created or edited is listed with `hub file link <url> --title "..."`, and again with `hub file touch <url>` after
each edit (Tico keeps the address, never the document). An S3 object is copied on this computer with
`hub file import s3://bucket/key`. Files humans send you are inputs, not yours to list.

Inputs: the finished file in `reports/` or `artifacts/`, or the Google, Notion or Figma link.

**Done when:** the file is published or linked, the task note names its path or link, and no credential is in it.
