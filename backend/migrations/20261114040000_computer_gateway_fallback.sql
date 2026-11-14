-- A computer's own "Fall back to Tico Gateway" (Settings > Computers, backend/models_preset.py computer_falls_back):
-- whether its turns run on the Gateway when its plans run out. Off for every computer, existing ones included; an
-- admin turns it on. The company's Gateway switch keeps only its other job, running turns until a computer is signed in.
ALTER TABLE runners ADD COLUMN gateway_fallback INTEGER NOT NULL DEFAULT 0 CHECK (gateway_fallback IN (0, 1));
