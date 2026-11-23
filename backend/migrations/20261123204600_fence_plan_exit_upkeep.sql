-- Legacy upkeep has no exit binding. A routed job keeps the selection it was issued under.
CREATE TABLE plan_exit_bindings(
 kind TEXT NOT NULL CHECK(kind IN ('lease','attempt','connect')),
 ref_id TEXT NOT NULL,
 exit_generation INTEGER NOT NULL,
 PRIMARY KEY(kind, ref_id));
