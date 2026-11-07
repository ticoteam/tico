CREATE TABLE IF NOT EXISTS support_file_attempts(
 actor TEXT NOT NULL, request_key TEXT NOT NULL, created_at TEXT NOT NULL,
 ticket_id TEXT, PRIMARY KEY(request_key));
