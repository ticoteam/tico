-- What a page attached to a page requirement is judged against, fixed when it is attached (backend/task_pages.py):
-- `judged_against` is {"rubric", "context"} (the heads a review is for, the release a Validate page is checked against)
-- and `rubric_hash` its key (page_verdicts.rubric_hash). The requirement reads its verdict by this key, and Jev is shown
-- exactly this, so a later edit to the rubric or a new release never changes what was judged.
ALTER TABLE requirement_pages ADD COLUMN rubric_hash TEXT;
ALTER TABLE requirement_pages ADD COLUMN judged_against TEXT NOT NULL DEFAULT '{}';
