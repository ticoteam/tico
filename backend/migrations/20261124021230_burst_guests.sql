CREATE TABLE burst_guests (
    bot TEXT PRIMARY KEY, runner_id TEXT NOT NULL, generation INTEGER NOT NULL, base_runner TEXT NOT NULL,
    return_pending TEXT NOT NULL DEFAULT ''
);
CREATE INDEX burst_guests_runner ON burst_guests(runner_id);
CREATE TABLE assignment_pins (
    bot TEXT PRIMARY KEY REFERENCES assignments(bot) ON DELETE CASCADE
);
