-- Company skills directory (Library › Skills): skills, versions, proposals, screening results, installs, staging
-- receipts, usage, daily rollups and the skills helper's devices. Spec: emp-tico-open-source
-- reports/2026-11-05-company-skills/spec.md. Actors are polymorphic: *_type 'Human' | 'Bot', *_id the row's id.

CREATE TABLE IF NOT EXISTS skills (
  id TEXT PRIMARY KEY,                       -- 'sk_' + random
  name TEXT NOT NULL UNIQUE,                 -- ^[a-z0-9][a-z0-9._-]{0,79}$
  description TEXT NOT NULL DEFAULT '',
  latest_version_id TEXT,                    -- skill_versions.id; NULL until the first approval
  created_by_type TEXT NOT NULL,
  created_by_id TEXT NOT NULL,
  deleted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_versions (
  id TEXT PRIMARY KEY,                       -- 'skv_' + random
  skill_id TEXT NOT NULL REFERENCES skills(id),
  number INTEGER NOT NULL,                   -- 1, 2, 3 ...
  sha256 TEXT NOT NULL,                      -- of the canonical bundle (sorted [{path, content}] JSON)
  files TEXT NOT NULL,                       -- JSON [{path, content}], immutable
  description TEXT NOT NULL DEFAULT '',
  proposal_id TEXT NOT NULL,
  approved_by_id TEXT NOT NULL REFERENCES humans(id),
  approved_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(skill_id, number),
  UNIQUE(skill_id, sha256)
);

CREATE TABLE IF NOT EXISTS skill_proposals (
  id TEXT PRIMARY KEY,                       -- 'skp_' + random
  name TEXT NOT NULL,
  skill_id TEXT REFERENCES skills(id),       -- NULL: a new skill
  base_version_id TEXT REFERENCES skill_versions(id),   -- the version an update was made from
  sha256 TEXT NOT NULL,
  files TEXT NOT NULL,                       -- JSON [{path, content}]
  description TEXT NOT NULL DEFAULT '',      -- from SKILL.md frontmatter
  source TEXT NOT NULL DEFAULT '{}',         -- JSON {kind: bot|person|url, bot_id?, repo_path?, url?}
  note TEXT NOT NULL DEFAULT '',             -- the proposer's one line
  status TEXT NOT NULL DEFAULT 'pending',    -- pending | approved | declined | withdrawn
  proposed_by_type TEXT NOT NULL,
  proposed_by_id TEXT NOT NULL,
  task_id TEXT,                              -- where the proposer asked from, for the reply line
  decided_by_id TEXT REFERENCES humans(id),
  decided_at TEXT,
  decline_reason TEXT,
  version_id TEXT REFERENCES skill_versions(id),        -- set on approval
  screen_status TEXT NOT NULL DEFAULT 'pending',        -- pending | screened | not_screened
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS index_skill_proposals_on_status ON skill_proposals(status, created_at);
CREATE INDEX IF NOT EXISTS index_skill_proposals_on_name ON skill_proposals(name);

CREATE TABLE IF NOT EXISTS skill_screenings (
  id TEXT PRIMARY KEY,                       -- 'sks_' + random
  proposal_id TEXT NOT NULL REFERENCES skill_proposals(id),
  sha256 TEXT NOT NULL,                      -- the bundle that was screened
  base_version_id TEXT,                      -- the diff base it was screened against
  question_set TEXT NOT NULL,                -- e.g. 'skill-screen@1'
  status TEXT NOT NULL,                      -- screened | not_screened
  reason TEXT,                               -- not_screened: error | cap | timeout | oversize | unavailable
  verdict TEXT,                              -- safe | review | unsafe
  risk TEXT,                                 -- low | medium | high
  summary TEXT,
  findings TEXT NOT NULL DEFAULT '[]',       -- JSON [{file, line, issue, severity}] (model)
  patterns TEXT NOT NULL DEFAULT '[]',       -- JSON [{file, line, pattern}] (code checks)
  judge_call_id TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS index_skill_screenings_on_proposal_id ON skill_screenings(proposal_id, created_at);

CREATE TABLE IF NOT EXISTS skill_installs (
  id TEXT PRIMARY KEY,                       -- 'ski_' + random
  skill_id TEXT NOT NULL REFERENCES skills(id),
  target_type TEXT NOT NULL,                 -- 'Bot' | 'Human'
  target_id TEXT NOT NULL,
  version_policy TEXT NOT NULL DEFAULT 'latest',        -- latest | pinned
  pinned_version_id TEXT REFERENCES skill_versions(id),
  installed_by_type TEXT NOT NULL,
  installed_by_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(skill_id, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS index_skill_installs_on_target ON skill_installs(target_type, target_id);

CREATE TABLE IF NOT EXISTS skill_receipts (
  target_type TEXT NOT NULL,                 -- 'Bot' | 'Human'
  target_id TEXT NOT NULL,
  skill_id TEXT NOT NULL REFERENCES skills(id),
  sha256 TEXT NOT NULL,                      -- what the runner or helper actually staged
  source TEXT NOT NULL,                      -- runner | helper
  staged_at TEXT NOT NULL,
  PRIMARY KEY(target_type, target_id, skill_id)
);

CREATE TABLE IF NOT EXISTS skill_usages (
  id INTEGER PRIMARY KEY,
  skill_id TEXT NOT NULL REFERENCES skills(id),
  sha256 TEXT,
  actor_type TEXT NOT NULL,                  -- 'Bot' | 'Human': who ran it
  actor_id TEXT NOT NULL,
  requested_by_id TEXT REFERENCES humans(id),          -- the person who typed /name for a bot, if any
  trigger TEXT NOT NULL,                     -- model | person | nested
  source TEXT NOT NULL,                      -- runner | helper | thread
  session_key TEXT NOT NULL,                 -- attempt id, or the helper's hashed session id
  tool_use_id TEXT NOT NULL,                 -- tool_use id, or a message id for a typed /name
  task_id TEXT,
  thread_id TEXT,
  used_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(session_key, tool_use_id)
);
CREATE INDEX IF NOT EXISTS index_skill_usages_on_skill_id ON skill_usages(skill_id, used_at);
CREATE INDEX IF NOT EXISTS index_skill_usages_on_used_at ON skill_usages(used_at);

CREATE TABLE IF NOT EXISTS skill_usage_dailies (
  used_on TEXT NOT NULL,                     -- YYYY-MM-DD, UTC
  skill_id TEXT NOT NULL REFERENCES skills(id),
  actor_type TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  trigger TEXT NOT NULL,
  uses_count INTEGER NOT NULL,
  PRIMARY KEY(used_on, skill_id, actor_type, actor_id, trigger)
);

CREATE TABLE IF NOT EXISTS skill_devices (
  id TEXT PRIMARY KEY,                       -- 'skd_' + random
  person_id TEXT NOT NULL REFERENCES humans(id) ON DELETE CASCADE,
  label TEXT NOT NULL DEFAULT '',
  token_hash TEXT NOT NULL UNIQUE,           -- sha256 of the 'tico_sd_' token, shown once
  reporting_enabled INTEGER NOT NULL DEFAULT 0,          -- the person's opt-in to usage reporting
  claude_version TEXT,
  last_seen_at TEXT,
  revoked_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS index_skill_devices_on_person_id ON skill_devices(person_id);

CREATE TABLE IF NOT EXISTS skill_device_codes (
  device_code_hash TEXT PRIMARY KEY,         -- sha256 of the helper's secret device code
  user_code TEXT NOT NULL UNIQUE,            -- short code the person confirms in the browser
  label TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',    -- pending | approved | denied | used | expired
  person_id TEXT REFERENCES humans(id),
  reporting_enabled INTEGER NOT NULL DEFAULT 0,
  device_id TEXT REFERENCES skill_devices(id),
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
