"""The page audience editor has a restricted example in the existing Files demo."""


def seed(builder):
    def work(c):
        row = c.execute("SELECT f.* FROM pages p JOIN bot_files f ON f.id=p.file_id LIMIT 1").fetchone()
        if row:
            from ..auth import Identity
            from ..pages import checked_audience
            editor = Identity("bot:" + row["bot_id"], "bot")
            builder.api.app.state.pages.set_audience(c, editor, row, checked_audience(c, {"people": ["ana"]}))
    builder.write(work)
