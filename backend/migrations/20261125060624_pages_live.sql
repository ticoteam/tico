-- Existing pages are read in bounded passes to find their live blocks.
ALTER TABLE pages ADD COLUMN live INTEGER;
ALTER TABLE pages ADD COLUMN live_version INTEGER;
