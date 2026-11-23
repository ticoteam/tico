"""An Antigravity plan in Settings > Providers: Connect only (never pasted), a person's pool, and, when no online
computer has agy, one computer turned on to install it while the page asks again."""

import json

from backend import cloud_machines as CM
from backend import model_login as ML
from backend.tests.helpers.api import get, post, runner
from backend.tests.helpers.cloud_machines import cloud  # noqa: F401 - fixture
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import company, pick

CAPS = ["model_subscriptions_v1", "model_sign_out_v1", "model_connect_v1", "attempt_credentials_v1"]
VALUE = json.dumps(
    {"token": {"access_token": "ya29.synthetic-access-token-0123456789", "expiry": "2099-01-01T00:00:00Z"}}
)
LINK = (
    "https://accounts.google.com/o/oauth2/auth?redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback"
    "&state=s1&response_type=code"
)
START = {"kind": "antigravity_login"}


def beat(api, m, installed=False, state="idle", harness=True):
    """The computer checks in: agy installed or not, and its install's state (`harness` False: a runner too old to
    know Antigravity)."""
    readiness = {
        "schema_version": 1,
        "runtimes": {"antigravity": {"installed": installed, "authenticated": "missing"}},
        "bots": {},
    }
    if harness:
        readiness["harnesses"] = {
            "antigravity": {
                "name": "Antigravity",
                "runtime": "antigravity",
                "installed": installed,
                "version": "1.5.15" if installed else "",
                "managed": installed,
                "source": "tools" if installed else "",
                "pinned": False,
                "pin": "",
                "authenticated": "missing",
                "update_available": False,
                "latest": "",
                "wanted": False,
                "state": state,
                "detail": "",
            }
        }
    body = {"version": "test", "platform": "test", "capabilities": CAPS, "readiness": readiness}
    post(api, "runners/heartbeat", body, token=m["token"])


def computer(api, label, operator="ana", enrolled="2026-11-01T00:00:00Z", **kw):
    m = runner(api, operator=operator, label=label)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET created_at=? WHERE id=?", (enrolled, m["runner_id"]))
    beat(api, m, **kw)
    return m


def chosen(api):
    with api.app.state.store.read() as c:
        return [tuple(r) for r in c.execute("SELECT runner_id,harness,enabled FROM runner_harness_choices")]


def connect(api, who="ana-test", expected=200):
    return post(api, "model-subscriptions/connect", START, who, expected=expected)


def test_an_antigravity_plan_connects_on_a_computer_with_agy_never_by_paste_and_joins_its_owners_pool(api):
    setup(api)
    company(api)
    assert "antigravity" in ML.CONNECT_RUNTIMES
    m = computer(api, "Ana Mac", installed=True)
    listed = get(api, "model-subscriptions")
    [kind] = [k for k in listed["kinds"] if k["kind"] == "antigravity_login"]
    assert (kind["label"], kind["runtime"], kind["connect"], kind["paste"]) == (
        "Antigravity plan",
        "antigravity",
        True,
        False,
    )
    assert all(k["paste"] for k in listed["kinds"] if k["kind"] != "antigravity_login")
    assert listed["connect"] == ["antigravity_login"]
    login = connect(api)
    assert (login["runtime"], login["runner_id"], login["waking"]) == ("antigravity", m["runner_id"], False)
    # Only Google's sign-in page is shown; anything else the computer says the link is, is not.
    report = f"runner-logins/{login['id']}/report"
    post(api, report, {"state": "waiting", "url": "https://accounts.google.example/o/oauth2/auth?x=1"}, m["token"])
    assert get(api, f"computers/{m['runner_id']}/logins/{login['id']}")["url"] == ""
    post(api, report, {"state": "waiting", "url": LINK}, m["token"])
    seen = get(api, f"computers/{m['runner_id']}/logins/{login['id']}")
    assert seen["url"] == LINK and seen["accepts_code"]
    # The page Google ends on may be pasted whole, as agy takes it; nothing else that is not a code.
    code = f"computers/{m['runner_id']}/logins/{login['id']}/code"
    post(api, code, {"code": "https://evil.example/oauth-callback?code=4/x&state=s1"}, expected=422)
    post(api, code, {"code": "https://antigravity.google/oauth-callback?code=4/0Ab-x_y&state=s1"})
    assert post(api, f"runner-logins/{login['id']}/subscription", {"value": VALUE}, m["token"])["state"] == "done"
    [saved] = get(api, "model-subscriptions")["subscriptions"]
    assert (saved["kind"], saved["label"], saved["name"], saved["preview"]) == (
        "antigravity_login",
        "Antigravity plan",
        "Ana's Antigravity plan",
        "Google sign-in",
    )
    with api.app.state.store.read() as c:
        providers = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='providers'").fetchone()[0])
    assert "google" in providers["enabled"]  # an admin's plan turns its provider on
    # A plan's person pool takes Antigravity turns, as for Claude and ChatGPT.
    pick(api, "ops", "antigravity", person="human:ana")
    # A pasted value never makes one from the page's paste path either: it is not a sign-in agy wrote.
    post(api, "model-subscriptions", {"kind": "antigravity_login", "secret": "not json"}, expected=422)


def test_with_no_computer_that_has_agy_the_owner_turns_one_on_once_and_everyone_waits_for_it(api):
    setup(api)
    company(api)
    mac = computer(api, "Ana Mac")
    computer(api, "Ben Mac", operator="ben")  # not the company owner's: never asked to install
    old = computer(api, "Old Mac", harness=False)  # too old to know Antigravity
    # A teammate before any install is told who can start it, and nothing is written.
    asked = connect(api, "ben-test")
    assert (asked["id"], asked["waking"], asked["message"]) == (
        "",
        False,
        "Ask the owner to press Connect for Antigravity once, to install it.",
    )
    assert chosen(api) == []
    # The owner's first press turns agy on for their computer; asking again writes nothing more.
    for _ in range(3):
        asked = connect(api)
        assert (asked["id"], asked["waking"], asked["message"]) == ("", True, "Installing Antigravity on Ana Mac.")
    assert chosen(api) == [(mac["runner_id"], "antigravity", 1)]
    assert old["runner_id"] not in [r for r, _, _ in chosen(api)]
    # A teammate during that install waits too.
    assert connect(api, "ben-test")["waking"] is True
    # Once agy is there, the next ask queues the sign-in as usual.
    beat(api, mac, installed=True)
    login = connect(api)
    assert login["id"] and login["runner_id"] == mac["runner_id"]
    # A Claude plan with no computer for its CLI installs nothing: today's answer, Paste offered.
    reply = post(api, "model-subscriptions/connect", {"kind": "claude_token"}, expected=409)
    assert reply["error"]["code"] == "offline" and "Paste instead" in reply["error"]["detail"]
    assert chosen(api) == [(mac["runner_id"], "antigravity", 1)]


def test_the_install_target_stays_put_whatever_the_heartbeats_and_a_failed_one_gives_way(api):
    setup(api)
    company(api)
    a = computer(api, "A", enrolled="2026-11-01T00:00:00Z")
    b = computer(api, "B", enrolled="2026-11-02T00:00:00Z")
    assert connect(api)["message"] == "Installing Antigravity on A."
    for m in (b, a, b):  # heartbeat order flips between asks
        beat(api, m)
        assert connect(api)["message"] == "Installing Antigravity on A."
    assert chosen(api) == [(a["runner_id"], "antigravity", 1)]
    # A's install failed: B, healthy, is turned on and becomes the target.
    beat(api, a, state="failed")
    assert connect(api)["message"] == "Installing Antigravity on B."
    assert sorted(chosen(api)) == sorted([(a["runner_id"], "antigravity", 1), (b["runner_id"], "antigravity", 1)])
    # B installing, A's backoff over (On and idle): B, installing, stays the target.
    beat(api, b, state="installing")
    beat(api, a)
    assert connect(api)["message"] == "Installing Antigravity on B."
    # Both On and idle: the one enrolled first.
    beat(api, b)
    assert connect(api)["message"] == "Installing Antigravity on A."


def test_a_failed_install_says_so_and_writes_nothing(api):
    setup(api)
    company(api)
    computer(api, "Ana Mac", state="failed")
    before = chosen(api)
    asked = connect(api)
    assert (asked["waking"], asked["message"]) == (False, "Antigravity couldn't install on Ana Mac; Tico will retry.")
    assert chosen(api) == before == []


def test_an_install_a_bot_started_is_waited_for_by_anyone_with_no_choice_written(api):
    setup(api)
    company(api)
    computer(api, "Ana Mac", state="installing")
    for who in ("ben-test", "ana-test"):
        asked = connect(api, who)
        assert (asked["waking"], asked["message"]) == (True, "Installing Antigravity on Ana Mac.")
    assert chosen(api) == []


def test_an_online_owner_mac_installs_and_the_sleeping_cloud_computer_stays_asleep(cloud):  # noqa: F811
    setup(cloud.api)
    company(cloud.api)
    computer(cloud.api, "Ana Mac")
    assert connect(cloud.api)["message"] == "Installing Antigravity on Ana Mac."
    assert cloud.tick() == []  # no machine made or started for it
    with cloud.store.read() as c:
        assert CM._signin_asked(c, CM.now()) is None


def test_the_shared_cloud_computer_installing_stays_wanted_while_the_page_asks(cloud):  # noqa: F811
    setup(cloud.api)
    company(cloud.api)
    with cloud.store.transaction() as c:
        CM.ensure_shared(c)
    vm = computer(cloud.api, "boat-vm", operator="ben")
    with cloud.store.transaction() as c:
        CM.save(c, CM.SHARED, runner_id=vm["runner_id"])
    first = cloud.at[0]
    for _ in range(4):  # longer than SIGNIN_ASK in all, asked every 30 s
        assert connect(cloud.api)["message"] == "Installing Antigravity on boat-vm."
        with cloud.store.read() as c:
            want = CM._signin_asked(c, CM.now())
        assert want == {"since": first, "seen": cloud.at[0]}
        cloud.at[0] += 30
    # The page closed: SIGNIN_ASK later the want is over.
    cloud.at[0] += CM.SIGNIN_ASK
    with cloud.store.read() as c:
        assert CM._signin_asked(c, CM.now()) is None


def test_no_install_target_offers_no_paste_for_antigravity(api):
    setup(api)
    company(api)
    computer(api, "Old Mac", harness=False)
    reply = post(api, "model-subscriptions/connect", START, expected=409)
    assert reply["error"]["code"] == "offline" and "Paste" not in reply["error"]["detail"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM model_logins").fetchone()[0] == 0
