"""The holiday plan linked to the ad-budget task, through the same pin API as Link page."""


def seed(b):
    b.at(hours=2)
    pages = b.call("GET", "pages?bot=marketing-lead")["pages"]
    b.call("POST", "pins", {"place": "task:" + b.tasks["paid-budget"], "item": "page:" + pages[0]["file_id"]})
