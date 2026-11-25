-- The page kit a page version is pinned to (backend/visuals.py KIT): its kit tags are drawn by that kit's file for
-- good, so a later kit never changes a page already published. NULL for every other file and for pages from before kits.
ALTER TABLE bot_file_versions ADD COLUMN page_kit INTEGER;
