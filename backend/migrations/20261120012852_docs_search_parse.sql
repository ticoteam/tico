-- Each doc's parse as docs search ranks it (backend/docs.py search_parse), written with every write of the doc's title,
-- path or body. NULL until then: the start fills it for live docs (docs.ensure_schema).
ALTER TABLE docs ADD COLUMN search_parse TEXT;
