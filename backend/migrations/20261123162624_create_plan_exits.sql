CREATE TABLE plan_exits (
 person_id TEXT PRIMARY KEY NOT NULL REFERENCES humans(id),
 runner_id TEXT REFERENCES runners(id),
 generation INTEGER NOT NULL CHECK (generation > 0)
);
