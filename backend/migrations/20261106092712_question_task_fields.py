"""Question fields on tasks (backend/question_tasks.py): a bot's question to a person is a task the person owns.

`questions` (JSON list: each question's id, wording, why, options with their recommended pick, multi), `if_no_answer`
(JSON: what the bot does, its kind and `by`), `review` (JSON: the file versions under review, pick, their chips),
`spend` (JSON), `answer` (JSON: the picks, note, who and when), `question_topic` (the fold key) and `asked_at` (the
question's clock, restarted when a new wording folds in). A task with `questions` set is a question task."""

COLUMNS = ("questions", "question_topic", "if_no_answer", "review", "spend", "answer", "asked_at")


def up(conn):
    have = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    for column in COLUMNS:
        if column not in have:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT")
    # Open question tasks are few: the fold lookup and the deadline sweeps read only them.
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_open_questions ON tasks(owner_id, requester_id) "
                 "WHERE questions IS NOT NULL AND answer IS NULL")
