# Segment tagging

The owner's Assistant gives you this as a task, with the company's segment names. A segment tags two things: each customer, and each budget. A budget's `segment_id` decides which segment its spend reports under.

1. `hub segment list` gives the company's segments and their ids (`segment/<slug>`). Use only the ids it prints.
2. `hub finance segment-review` lists the customers no person has set a segment for. Read it a page at a time, with `--cursor` for the next page.
3. For each customer whose segment is clear from its name, its products or the owner's words, run `hub finance contact-segment <contact-id> segment/<slug>`. Clear a wrong guess with `""`.
4. For a budget the task names, set its `segment_id` the way the task says. `hub finance act help` lists the fields.
5. Unsure? Leave it. A guess never overwrites a person's choice. Put the ones you are unsure of in one list in your report, with what would settle each one. The owner decides them on the Finance page.
