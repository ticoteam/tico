-- A Word or Excel file's preview (backend/office_preview.py): its first pages or rows as HTML, a derived blob the
-- preview worker makes and the chat reads at /api/v2/files/<id>/preview. It is released with its source as a poster or
-- thumbnail is, and the partial indexes keep the derived-blob lookups (release_blobs, task_privacy) off full scans.
ALTER TABLE blob_media ADD COLUMN preview_html_blob_id TEXT REFERENCES blobs(id);
ALTER TABLE bot_file_versions ADD COLUMN preview_html_blob_id TEXT REFERENCES blobs(id);
CREATE INDEX blob_media_preview_html ON blob_media(preview_html_blob_id) WHERE preview_html_blob_id IS NOT NULL;
CREATE INDEX bot_file_versions_preview_html ON bot_file_versions(preview_html_blob_id)
  WHERE preview_html_blob_id IS NOT NULL;
