-- Default read (backend/default_read.py): every bot reads a company tool whose Read can be held to reads, unless the
-- tool's "Everyone can read" switch is off. On for every tool, existing ones included; a tool where Read cannot be
-- enforced, an Only listed one or a website tool never default-reads whatever the switch says.
ALTER TABLE tools ADD COLUMN everyone_reads INTEGER NOT NULL DEFAULT 1 CHECK (everyone_reads IN (0, 1));
