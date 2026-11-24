"""An Antigravity plan in Settings > Providers: Connect only (never pasted), a person's pool, and, when no online
computer has agy, one computer turned on to install it while the page asks again."""

import json

from backend import agent_subscriptions as AS
from backend import cloud_machines as CM
from backend import model_login as ML
from backend.tests.helpers.api import get, post, runner
from backend.tests.helpers.cloud_machines import cloud  # noqa: F401 - fixture
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import company, pick

OLD = ["model_subscriptions_v1", "model_sign_out_v1", "model_connect_v1", "attempt_credentials_v1"]
CAPS = [*OLD, "antigravity_plan_v1"]
VALUE = json.dumps(
    {"token": {"access_token": "ya29.synthetic-access-token-0123456789", "expiry": "2099-01-01T00:00:00Z"}}
)
LINK = (
    "https://accounts.google.com/o/oauth2/auth?redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback"
    "&state=s1&response_type=code&scope=email%20profile%20https://www.googleapis.com/auth/cloud-platform"
)
# Where Google sends the person back, as the address bar shows it.
ENDED = (
    "https://antigravity.google/oauth-callback?state=s1&code=4/0Ab-x_y"
    "&scope=email%20profile%20https://www.googleapis.com/auth/cloud-platform%20openid&authuser=0&prompt=consent"
)
START = {"kind": "antigravity_login"}


def beat(api, m, installed=False, state="idle", harness=True, caps=CAPS, version="1.5.15", managed=True):
    """The computer checks in: agy installed or not, and its install's state (`harness` False: a runner too old to
    know Antigravity; `caps` OLD: one that knows it but not its plans)."""
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
                "version": version if installed else "",
                "managed": installed and managed,
                "source": ("tools" if managed else "path") if installed else "",
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
    body = {"version": "test", "platform": "test", "capabilities": caps, "readiness": readiness}
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
    # Only Google's sign-in page, sending the code back to Antigravity, is shown; anything else the computer says the
    # link is, is not.
    report = f"runner-logins/{login['id']}/report"
    for wrong in (
        "https://accounts.google.example/o/oauth2/auth?x=1",
        LINK.replace("antigravity.google", "evil.example"),
        LINK.replace("response_type=code", "response_type=token"),
        LINK + "&state=s2",
        "https://[accounts.google.com/o/oauth2/auth?state=s1",
    ):
        post(api, report, {"state": "waiting", "url": wrong}, m["token"])
        assert get(api, f"computers/{m['runner_id']}/logins/{login['id']}")["url"] == ""
    post(api, report, {"state": "waiting", "url": LINK}, m["token"])
    seen = get(api, f"computers/{m['runner_id']}/logins/{login['id']}")
    assert seen["url"] == LINK and seen["accepts_code"]
    # The page Google ends on may be pasted whole, as agy takes it, when it answers this sign-in: one code, its state.
    code = f"computers/{m['runner_id']}/logins/{login['id']}/code"
    for wrong in (
        ENDED.replace("antigravity.google", "evil.example"),
        ENDED.replace("state=s1", "state=s2"),
        ENDED.replace("state=s1&", ""),
        ENDED + "&code=4/other",
        ENDED.replace("code=4/0Ab-x_y", "code="),
        "https://[antigravity.google/oauth-callback?state=s1&code=4/x",
    ):
        post(api, code, {"code": wrong}, expected=422)
    # Google saying the person said no is refused, and the sign-in still takes the next try.
    denied = post(
        api, code, {"code": "https://antigravity.google/oauth-callback?error=access_denied&state=s1"}, expected=422
    )
    assert denied["error"]["code"] == "denied"
    post(api, code, {"code": ENDED})
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
    # A value sent any other way than Connect, even a well-formed one, never makes or replaces one: agy did not check it.
    refused = post(api, "model-subscriptions", {"kind": "antigravity_login", "secret": VALUE}, expected=422)
    assert refused["error"]["detail"] == "Use Connect for this plan"
    post(api, f"model-subscriptions/{saved['id']}", {"secret": VALUE}, expected=422)
    assert len(get(api, "model-subscriptions")["subscriptions"]) == 1


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


def test_a_runner_that_knows_antigravity_but_not_its_plans_never_signs_in_installs_or_runs_one(api):
    setup(api)
    company(api)
    old = computer(api, "Old Mac", installed=True, caps=OLD)  # agy there, but too old for a plan
    gone = computer(api, "Old Linux", operator="ana", caps=OLD)  # the owner's, with no agy: never asked to install
    reply = connect(api, expected=409)
    assert reply["error"]["code"] == "offline"
    assert chosen(api) == []
    new = computer(api, "New Mac", installed=True)
    login = connect(api)
    assert login["runner_id"] == new["runner_id"]
    post(api, f"runner-logins/{login['id']}/report", {"state": "waiting", "url": LINK}, new["token"])
    post(api, f"computers/{new['runner_id']}/logins/{login['id']}/code", {"code": ENDED})
    post(api, f"runner-logins/{login['id']}/subscription", {"value": VALUE}, new["token"])
    [plan] = get(api, "model-subscriptions")["subscriptions"]
    pick(api, "ops", "antigravity", person="human:ana")
    config = {"runtime": "antigravity", "model": ""}
    with api.app.state.store.transaction() as c:
        rows = {
            m["runner_id"]: c.execute("SELECT * FROM runners_with_state WHERE id=?", (m["runner_id"],)).fetchone()
            for m in (old, gone, new)
        }
        assert AS.for_runner(c, rows[new["runner_id"]], "ops", config).subscription["id"] == plan["id"]
        for m in (old, gone):
            payment = AS.for_runner(c, rows[m["runner_id"]], "ops", config)
            assert payment.subscription is None and payment.update


def test_an_agy_older_than_tico_runs_neither_signs_a_plan_in_nor_runs_its_turns(api):
    setup(api)
    company(api)
    # The owner's own agy, older than the manifest's minimum: Tico does not replace it, and says so.
    old = computer(api, "Ana Mac", installed=True, version="1.5.0", managed=False)
    asked = connect(api)
    assert (asked["id"], asked["waking"], asked["message"]) == (
        "",
        False,
        "Update Antigravity on Ana Mac to 1.5.15 or later.",
    )
    assert chosen(api) == []
    new = computer(api, "New Mac", installed=True, enrolled="2026-11-02T00:00:00Z")
    login = connect(api)
    assert login["runner_id"] == new["runner_id"]
    post(api, f"runner-logins/{login['id']}/report", {"state": "waiting", "url": LINK}, new["token"])
    post(api, f"computers/{new['runner_id']}/logins/{login['id']}/code", {"code": ENDED})
    post(api, f"runner-logins/{login['id']}/subscription", {"value": VALUE}, new["token"])
    pick(api, "ops", "antigravity", person="human:ana")
    with api.app.state.store.transaction() as c:
        row = c.execute("SELECT * FROM runners_with_state WHERE id=?", (old["runner_id"],)).fetchone()
        payment = AS.for_runner(c, row, "ops", {"runtime": "antigravity", "model": ""})
    assert payment.subscription is None and payment.update
    assert payment.reason == "Update Antigravity on Ana Mac"
