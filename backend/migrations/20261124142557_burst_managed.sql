CREATE TABLE burst_launches (
    bot TEXT PRIMARY KEY REFERENCES cloud_machines(bot) ON DELETE CASCADE,
    managed INTEGER NOT NULL DEFAULT 0 CHECK (managed IN (0,1)),
    launch_at REAL
);
