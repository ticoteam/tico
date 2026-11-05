"""A restricted holiday plan and an Assistant-made page on the ad-budget task."""
from ..store import H, digest


def seed(b):
    b.at(hours=2)
    task = b.tasks["paid-budget"]
    pages = b.call("GET", "pages?bot=marketing-lead")["pages"]
    b.call("PATCH", "pages/" + pages[0]["file_id"], {"audience": {"people": ["ana"]}})
    b.call("POST", "pins", {"place": "task:" + task, "item": "page:" + pages[0]["file_id"]})
    sent = b.call("POST", f"tasks/{task}/make-page", {"text": "Make a page for the holiday ad-budget review."})["message"]

    def lease(c):
        job = c.execute("SELECT id FROM jobs WHERE message_id=?", (sent["id"],)).fetchone()[0]
        assignment = c.execute("SELECT runner_id,generation FROM assignments WHERE bot='coo'").fetchone()
        aid = H.new_id()
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
                  "VALUES(?,?,'coo',?,?,?,'running',?,?)",
                  (aid, job, assignment["runner_id"], assignment["generation"], digest(aid),
                   H.shift(H.now(), seconds=300), H.now()))
        c.execute("UPDATE jobs SET state='running',attempt_id=? WHERE id=?", (aid, job))
        return aid, job, assignment["runner_id"]

    aid, job, runner = b.write(lease)
    token = "demo-make-page"
    settings = b.api.app.state.auth.settings
    with b.store.read() as c:
        settings.test_identities[token] = b.api.app.state.auth.assistant_principal(
            c, c.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone())
    try:
        b.call("POST", "pages", {"name": "holiday-review.html", "title": "Holiday budget review", "task": task,
                                 "text": "<h1>Holiday budget review</h1><p>Review the campaign plan, budget and results.</p>"},
               token=token)
    finally:
        settings.test_identities.pop(token, None)
    def finish(c):
        c.execute("UPDATE attempts SET state='completed',finished_at=?,settled_at=? WHERE id=?", (H.now(), H.now(), aid))
        c.execute("UPDATE jobs SET state='completed' WHERE id=?", (job,))
    b.write(finish)
