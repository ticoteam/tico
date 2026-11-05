CREATE TABLE alert_preferences (
 person_id TEXT PRIMARY KEY REFERENCES humans(id),
 phone TEXT UNIQUE,
 phone_version TEXT NOT NULL DEFAULT '',
 verified_at TEXT,
 revocation_pending INTEGER NOT NULL DEFAULT 0,
 stall_after TEXT NOT NULL DEFAULT '',
 call_consent INTEGER NOT NULL DEFAULT 0,
 urgent_needs INTEGER NOT NULL DEFAULT 0,
 legal_mail INTEGER NOT NULL DEFAULT 0,
 bot_stuck INTEGER NOT NULL DEFAULT 0,
 urgent_calls INTEGER NOT NULL DEFAULT 0,
 timezone TEXT NOT NULL DEFAULT 'UTC',
 quiet_start TEXT NOT NULL DEFAULT '22:00',
 quiet_end TEXT NOT NULL DEFAULT '07:00',
 emergency_exception INTEGER NOT NULL DEFAULT 0,
 monthly_cap INTEGER NOT NULL DEFAULT 10 CHECK(monthly_cap BETWEEN 0 AND 10000),
 updated_at TEXT NOT NULL
);
CREATE TABLE alert_company_settings (
 id INTEGER PRIMARY KEY CHECK(id=1),
 monthly_cap INTEGER NOT NULL DEFAULT 50 CHECK(monthly_cap BETWEEN 0 AND 100000)
);
INSERT INTO alert_company_settings VALUES(1,50);
CREATE TABLE personal_alerts (
 id TEXT PRIMARY KEY,
 person_id TEXT NOT NULL REFERENCES humans(id),
 incident_key TEXT NOT NULL,
 kind TEXT NOT NULL,
 subject_id TEXT NOT NULL,
 emergency INTEGER NOT NULL DEFAULT 0,
 phone_version TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending',
 month TEXT,
 reserved INTEGER NOT NULL DEFAULT 0,
 send_id TEXT,
 channel TEXT,
 error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 attempted_at TEXT,
 next_attempt_at TEXT,
 UNIQUE(person_id,incident_key)
);
CREATE INDEX personal_alerts_pending ON personal_alerts(state,created_at);
CREATE INDEX personal_alerts_spend ON personal_alerts(month,reserved,person_id);
