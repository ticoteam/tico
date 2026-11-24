-- Make the schema change here; the migration runner owns the transaction.
ALTER TABLE cloud_machines ADD COLUMN burst_launch_at REAL;
