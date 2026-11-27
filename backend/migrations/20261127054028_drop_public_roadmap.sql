-- The public roadmap is removed (task #4095, backend/public_roadmaps.py deleted): its visitors' daily action counts,
-- the secret that hashed their addresses, its settings and its old one-time markers go with it.
DROP TABLE IF EXISTS roadmap_actions;
DELETE FROM registry_metadata
WHERE key IN ('roadmaps', 'roadmap_ip_secret', 'roadmaps_explicit_public_text', 'roadmaps_copies_cleared_by_field');
