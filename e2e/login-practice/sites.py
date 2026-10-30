"""Login pages in the shapes a bot meets, for scripts/browser-sites.sh.

server.py mounts them under /sites/ (the pages and their posts) and /admin/sites/ (the test's own setup, sent with the
admin token). The accounts are the server's (STATE["users"]); the state of each variant is kept here. Every variant
checks a password with the same test /login uses, so a password that works on /login works on every variant.

The hosts are the ones the browser scenarios set up: the practice site answers as login.practice.test (the pages),
app.practice.test and accounts.practice.test (the single sign-on pair).
"""
import base64
import hashlib
import hmac
import html
import json
import secrets
import struct
import threading
import time
import urllib.parse

APP = "http://app.practice.test:8080"
ACCOUNTS = "http://accounts.practice.test:8080"
LOGIN = "http://login.practice.test:8080"
REPORT_NAME = "practice-report.txt"
REPORT = "practice report\n"
TOTP_DIGITS, TOTP_PERIOD = 8, 60   # the one shape of code this site asks for: 8 digits, SHA256, 60 seconds

_lock = threading.Lock()
_state = {"tokens": {}, "sso": {}, "app": {}, "magic": {}, "totp": {}, "used": {}}
_deps = {}


def bind(state, lock, hash_password, same, deliver, page, field):
    """server.py hands over its accounts, its lock and its helpers, so the variants share one account store."""
    _deps.update(state=state, lock=lock, hash_password=hash_password, same=same, deliver=deliver, page=page, field=field)


def user_ok(username, password):
    with _deps["lock"]:
        record = _deps["state"]["users"].get(username)
        return bool(record) and _deps["same"](_deps["hash_password"](password, record["salt"])[1], record["hash"])


def ensure_account(username, email="", password=None):
    """An account with the given password, or a random one for a passwordless sign-in (as a magic link creates it)."""
    salt, digest = _deps["hash_password"](password or secrets.token_urlsafe(24))
    with _deps["lock"]:
        record = _deps["state"]["users"].get(username)
        if record is None:
            _deps["state"]["users"][username] = {"email": email, "salt": salt, "hash": digest, "totp": None,
                                                 "pending": None}
        elif password is not None:
            record["salt"], record["hash"] = salt, digest


def totp_code(secret_b32, step):
    """The code for one step of the site's shape: HMAC-SHA256 with 8 digits, as RFC 6238 gives it."""
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha256).digest()
    offset = mac[-1] & 0x0F
    number = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** TOTP_DIGITS).zfill(TOTP_DIGITS)


def query_of(h):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(h.path).query))


def sign_in(h, username):
    return [("Set-Cookie", h.start_session(username))]


SUBMIT = "<button type=submit>Log in</button>"


def login_form(action, extra=""):
    return ("<form method=post action=" + action + ">" + _deps["field"]("username", "Username")
            + _deps["field"]("password", "Password", "password") + extra + SUBMIT + "</form>")


REACT_PAGE = """<form id=f>
<label>Username <input id=u name=username autocomplete=username></label>
<label>Password <input id=p name=password type=password autocomplete=current-password></label>
<button type=submit>Log in</button></form><p id=msg></p>
<script>
// The page keeps what was typed in its own state and sends that, as a controlled input does: a value set on the
// element alone (no input event) never reaches the state, so the post sends an empty password and is refused.
const state = {username: '', password: ''};
document.getElementById('u').addEventListener('input', e => { state.username = e.target.value; });
document.getElementById('p').addEventListener('input', e => { state.password = e.target.value; });
document.getElementById('f').addEventListener('submit', async e => {
  e.preventDefault();
  const r = await fetch('/sites/react/login', {method: 'POST', headers: {'content-type': 'application/json'},
                                              body: JSON.stringify(state), credentials: 'include'});
  if (r.ok) location.href = '/me';
  else document.getElementById('msg').textContent = 'Wrong username or password.';
});
</script>"""

SHADOW_PAGE = """<login-box></login-box>
<script>
class LoginBox extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({mode: 'open'});
    root.innerHTML = '<form method=post action=/login>'
      + '<label>Username <input name=username autocomplete=username></label>'
      + '<label>Password <input name=password type=password autocomplete=current-password></label>'
      + '<button type=submit>Log in</button></form>';
  }
}
customElements.define('login-box', LoginBox);
</script>"""

POPUP_PAGE = """<button id=open type=button onclick="window.open('/sites/popup/login', 'signin', 'width=420,height=520')">
Sign in</button>"""

CONSENT_OVERLAY = ("<div id=consent style='position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;"
                   "align-items:center;justify-content:center'><div style='background:#fff;padding:20px;max-width:360px'>"
                   "<p>We use cookies on this site.</p>"
                   "<button id=accept type=button onclick=\"document.getElementById('consent').remove()\">Accept all"
                   "</button></div></div>")


def get(h, path):
    page, field = _deps["page"], _deps["field"]
    if path == "/sites/two-page/user":
        return h.send(200, page("Sign in", "<form method=post action=/sites/two-page/user>"
                                + field("username", "Username") + SUBMIT + "</form>"))
    if path == "/sites/two-page/password":
        with _lock:
            user = _state["tokens"].get(h.cookie("tp"))
        if not user:
            return h.redirect("/sites/two-page/user")
        return h.send(200, page("Password", "<form method=post action=/sites/two-page/password>"
                                + field("password", "Password", "password") + SUBMIT + "</form>"))
    if path == "/sites/react":
        return h.send(200, page("Sign in", REACT_PAGE))
    if path == "/sites/frame":
        return h.send(200, page("Sign in", "<iframe id=frame src='/sites/frame/inner' title='Sign in' width=420 "
                                "height=320></iframe>"))
    if path == "/sites/frame/inner":
        return h.send(200, page("Sign in", login_form("/login")))
    if path == "/sites/shadow":
        return h.send(200, page("Sign in", SHADOW_PAGE))
    if path == "/sites/sso/start":
        return h.redirect(ACCOUNTS + "/sites/sso/login?" + urllib.parse.urlencode({"back": APP + "/sites/sso/back"}))
    if path == "/sites/sso/login":
        back = query_of(h).get("back", "")
        return h.send(200, page("Accounts", "<form method=post action=/sites/sso/login>"
                                + '<input type=hidden name=back value="' + html.escape(back, quote=True) + '">'
                                + field("username", "Username") + field("password", "Password", "password")
                                + SUBMIT + "</form>"))
    if path == "/sites/sso/back":
        code = query_of(h).get("code", "")
        with _lock:
            grant = _state["sso"].pop(code, None)
        if not grant or grant["expires"] < time.time():
            return h.redirect("/sites/sso/start")
        token = secrets.token_urlsafe(24)
        with _lock:
            _state["app"][token] = grant["user"]
        return h.redirect("/sites/sso/app", [("Set-Cookie", f"appsid={token}; Path=/; HttpOnly; SameSite=Lax")])
    if path == "/sites/sso/app":
        with _lock:
            user = _state["app"].get(h.cookie("appsid"))
        if not user:
            return h.redirect("/sites/sso/start")
        return h.send(200, page("App", "<p>Signed in to the app.</p>"))
    if path == "/sites/popup":
        return h.send(200, page("Sign in", POPUP_PAGE))
    if path == "/sites/popup/login":
        return h.send(200, page("Sign in", login_form("/login")))
    if path == "/sites/magic":
        return h.send(200, page("Sign in", "<form method=post action=/sites/magic/request>"
                                + field("email", "Email", "email") + SUBMIT.replace("Log in", "Email me a link")
                                + "</form>"))
    if path == "/sites/magic/verify":
        token = query_of(h).get("token", "")
        with _lock:
            grant = _state["magic"].pop(token, None)
        if not grant or grant["expires"] < time.time():
            return h.send(401, page("Link", "<p>This link has expired.</p>"))
        ensure_account(grant["email"], grant["email"])
        return h.redirect("/me", sign_in(h, grant["email"]))
    if path == "/sites/totp/login":
        return h.send(200, page("Sign in", login_form("/sites/totp/login")))
    if path == "/sites/totp/code":
        with _lock:
            user = _state["tokens"].get(h.cookie("totp_pre"))
        if not user:
            return h.redirect("/sites/totp/login")
        return h.send(200, page("Code", "<form method=post action=/sites/totp/code>"
                                + field("code", "Authenticator code") + SUBMIT + "</form>"))
    if path == "/sites/consent":
        return h.send(200, page("Sign in", CONSENT_OVERLAY + login_form("/login")))
    if path == "/sites/files":
        return h.send(200, page("Files", "<input id=up type=file name=upload>"
                                "<a id=dl href='/sites/files/" + REPORT_NAME + "' download>Download the report</a>"))
    if path == "/sites/files/" + REPORT_NAME:
        return h.send(200, REPORT, "text/plain; charset=utf-8",
                      headers=[("Content-Disposition", f'attachment; filename="{REPORT_NAME}"')])
    return h.send(404, page("Not found", "<p>No such page.</p>"))


def post(h, path, form):
    page, field = _deps["page"], _deps["field"]
    if path == "/sites/two-page/user":
        username = str(form.get("username", "")).strip()
        with _deps["lock"]:
            known = username in _deps["state"]["users"]
        if not known:
            return h.redirect("/sites/two-page/user")
        token = secrets.token_urlsafe(24)
        with _lock:
            _state["tokens"][token] = username
        return h.redirect("/sites/two-page/password", [("Set-Cookie", f"tp={token}; Path=/; HttpOnly; SameSite=Lax")])
    if path == "/sites/two-page/password":
        with _lock:
            user = _state["tokens"].pop(h.cookie("tp"), None)
        if user and user_ok(user, str(form.get("password", ""))):
            return h.redirect("/me", sign_in(h, user))
        return h.send(401, page("Password", "<p>Wrong password.</p>"))
    if path == "/sites/react/login":
        username, password = str(form.get("username", "")), str(form.get("password", ""))
        if not username or not password:
            return h.send(422, json.dumps({"error": "empty"}), "application/json")
        if not user_ok(username, password):
            return h.send(401, json.dumps({"error": "wrong"}), "application/json")
        return h.send(200, json.dumps({"next": "/me"}), "application/json", headers=sign_in(h, username))
    if path == "/sites/sso/login":
        back = str(form.get("back", ""))
        if not back.startswith(APP + "/sites/sso/back"):
            return h.send(422, page("Accounts", "<p>That return address is not one of this site's.</p>"))
        username, password = str(form.get("username", "")), str(form.get("password", ""))
        if not user_ok(username, password):
            return h.send(401, page("Accounts", "<p>Wrong username or password.</p>"))
        code = secrets.token_urlsafe(24)
        with _lock:
            _state["sso"][code] = {"user": username, "expires": time.time() + 60}
        return h.redirect(back + ("&" if "?" in back else "?") + urllib.parse.urlencode({"code": code}))
    if path == "/sites/magic/request":
        email = str(form.get("email", "")).strip().lower()
        if "@" in email:
            token = secrets.token_urlsafe(24)
            with _lock:
                _state["magic"][token] = {"email": email, "expires": time.time() + 900}
            link = LOGIN + "/sites/magic/verify?" + urllib.parse.urlencode({"token": token})
            _deps["deliver"](email, "Your sign-in link", "Sign in with this link: " + link)
        return h.send(200, page("Check your email", "<p>If that address has an account, a sign-in link is on its way.</p>"))
    if path == "/sites/totp/login":
        username, password = str(form.get("username", "")), str(form.get("password", ""))
        if not user_ok(username, password):
            return h.send(401, page("Sign in", "<p>Wrong username or password.</p>"))
        token = secrets.token_urlsafe(24)
        with _lock:
            _state["tokens"][token] = username
        return h.redirect("/sites/totp/code", [("Set-Cookie", f"totp_pre={token}; Path=/; HttpOnly; SameSite=Lax")])
    if path == "/sites/totp/code":
        with _lock:
            user = _state["tokens"].get(h.cookie("totp_pre"))
            secret = _state["totp"].get(user)
        if not user or not secret:
            return h.redirect("/sites/totp/login")
        given = str(form.get("code", "")).strip()
        now_step = int(time.time()) // TOTP_PERIOD
        # The code of this step or the step before or after it: a code typed a little late is still good.
        match = any(hmac.compare_digest(given, totp_code(secret, step)) for step in (now_step - 1, now_step, now_step + 1))
        with _lock:
            used = _state["used"].setdefault(user, set())
            if match and given in used:
                return h.send(422, page("Code", "<p>That code was already used. Wait for the next one.</p>"))
            if not match:
                return h.send(401, page("Code", "<p>That code is wrong.</p>"))
            used.add(given)
        return h.redirect("/me", sign_in(h, user))
    if path == "/admin/sites/user" and h.admin_ok():
        ensure_account(str(form["username"]), str(form.get("email", "")), str(form["password"]))
        return h.send(200, json.dumps({"ok": True}), "application/json")
    if path == "/admin/sites/password" and h.admin_ok():
        ensure_account(str(form["username"]), password=str(form["password"]))
        return h.send(200, json.dumps({"ok": True}), "application/json")
    if path == "/admin/sites/totp-user" and h.admin_ok():
        username = str(form["username"])
        ensure_account(username, password=str(form["password"]))
        with _lock:
            _state["totp"][username] = str(form["secret"])
        return h.send(200, json.dumps({"ok": True}), "application/json")
    return h.send(404, page("Not found", "<p>No such page.</p>"))
