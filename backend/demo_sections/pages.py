"""A restricted holiday plan linked to the ad-budget task, through the page and pin APIs."""


def seed(b):
    b.at(hours=2)
    page = b.call("GET", "pages?bot=marketing-lead")["pages"][0]
    b.call("PATCH", "pages/" + page["file_id"], {"audience": {"people": ["ana"]}})
    b.call("POST", "pins", {"place": "task:" + b.tasks["paid-budget"], "item": "page:" + page["file_id"]})
