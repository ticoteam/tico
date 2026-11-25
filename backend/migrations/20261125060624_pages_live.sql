-- Existing pages are read in bounded passes to find their live blocks.
ALTER TABLE pages ADD COLUMN live INTEGER;
CREATE INDEX pages_live_unknown ON pages(file_id) WHERE live IS NULL;
