---
name: Pandoc
category: operations
summary: "Convert documents between Markdown, Word, HTML and PDF"
commands: [pandoc]
---
## What it is for
Turning a Markdown draft into a Word file or PDF someone asked for, or a Word file into Markdown you can edit.

## How to use it

    pandoc report.md -o report.docx
    pandoc contract.docx -t gfm -o contract.md
    pandoc notes.md -s -o notes.html

## Good to know
- A PDF needs a PDF engine on the computer; HTML or Word needs nothing more.
