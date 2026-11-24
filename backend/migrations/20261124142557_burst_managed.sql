ALTER TABLE cloud_machines ADD COLUMN burst_managed INTEGER NOT NULL DEFAULT 0 CHECK (burst_managed IN (0,1));
ALTER TABLE cloud_machines ADD COLUMN burst_launch_at REAL;
