#!/usr/bin/env python3
"""A practice login site for scripts/browser-journey.sh: signup with an emailed code, password login, an optional
authenticator step, a settings page that turns it on, /me, and test hooks. Stdlib only; everything is in memory.

Pages:
  /signup, /signup/verify   create an account; the code is emailed through the fake Inbox (PRACTICE_MAIL_URL)
  /login, /login/2fa        password, then a six-digit code when two-step sign-in is on
  /settings                 shows the setup key and its otpauth URI as text until it is switched on
  /settings/2fa             confirms a code and switches two-step sign-in on
  /me                       200 JSON when the request carries a live session, 401 otherwise
  /logout
  /verify-phone, /verify-phone/code  a signed-in user adds a phone; the code is texted to it through the fake Inbox
  /challenge, /challenge/pass        a press-and-hold check for a signed-in user; /challenge/state says whether it passed

Test hooks, on /admin/* only when PRACTICE_ADMIN_TOKEN is set and sent as `Authorization: Bearer`:
  POST /admin/expire        ends every session
  POST /admin/ttl           {"seconds": n}: how long sessions created from now on last
  GET  /admin/state         counts, two-step flags and the last page-script beacon; never a secret
Every page loads a script that reports navigator.webdriver to POST /beacon.

The log holds one line per request: method, path and status. Query strings and bodies are never logged.
"""
import hashlib
import hmac
import html
import http.server
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import cookies

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import totp  # noqa: E402
import sites  # noqa: E402

PORT = int(os.environ.get("PRACTICE_PORT", "8080"))
MAIL_URL = os.environ.get("PRACTICE_MAIL_URL", "").rstrip("/")
ADMIN_TOKEN = os.environ.get("PRACTICE_ADMIN_TOKEN", "")
USERNAME = re.compile(r"^[a-z0-9_]{3,32}$")
CODE_TTL = 600
PRE_TTL = 300
LOCK = threading.Lock()
STATE = {
    "users": {},      # username -> {email, salt, hash, totp (enabled secret or None), pending (setup secret or None)}
    "signups": {},    # email -> {username, salt, hash, code, expires}
    "sessions": {},   # token -> {user, expires}
    "pre": {},        # token -> {user, expires}: a password that still needs its second step
    "ttl": int(os.environ.get("PRACTICE_SESSION_TTL", "1800")),
    "beacon": None,
    "challenge": {},  # username -> time the press-and-hold check passed
    "challenge_last": {},  # username -> the last pass attempt: {"held_ms": ..., "status": ...}, for diagnosis
    "challenge_seen": {},  # username -> the pointer events the check page reported (down, up, hold with its ms)
    "phones": {},     # phone number -> {user, code, expires}: a text code still waiting
}
HOLD_MS = 3000
BEACON_SCRIPT = ("<script>fetch('/beacon',{method:'POST',headers:{'content-type':'application/json'},"
                 "body:JSON.stringify({webdriver:String(navigator.webdriver)})});</script>")


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt, hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


def same(a, b):
    return hmac.compare_digest(a.encode(), b.encode())


def deliver(to, subject, text, kind="email"):
    """Hand a message to the fake Inbox, which keeps it for the company's inbox to fetch. A text goes to a phone."""
    if not MAIL_URL:
        raise RuntimeError("PRACTICE_MAIL_URL is not set")
    body = json.dumps({"to": to, "from": "Practice Login <no-reply@login.practice.test>", "subject": subject,
                       "text": text, "kind": kind}).encode()
    request = urllib.request.Request(MAIL_URL + "/_deliver", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as reply:
        reply.read()


def page(title, body):
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title></head><body>"
            f"<main><h1>{html.escape(title)}</h1>{body}</main>{BEACON_SCRIPT}</body></html>")


def field(name, label, kind="text", extra=""):
    return (f"<p><label>{label} <input name={name} type={kind} autocomplete="
            f"{'one-time-code' if name == 'code' else 'off'} {extra}></label></p>")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "PracticeLogin"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # paths and statuses only: see log_request
        pass

    def log_request(self, code="-", size="-"):
        print(f"{self.command} {urllib.parse.urlsplit(self.path).path} {code}", file=sys.stderr, flush=True)

    def body(self):
        length = min(int(self.headers.get("Content-Length") or 0), 65536)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        if "json" in (self.headers.get("Content-Type") or ""):
            return json.loads(raw or "{}")
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def send(self, status, content, ctype="text/html; charset=utf-8", headers=()):
        data = content.encode() if isinstance(content, str) else content
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def redirect(self, location, headers=()):
        self.send(303, "", headers=[("Location", location), *headers])

    def cookie(self, name):
        jar = cookies.SimpleCookie(self.headers.get("Cookie") or "")
        return jar[name].value if name in jar else ""

    def session_user(self):
        token = self.cookie("sid")
        with LOCK:
            found = STATE["sessions"].get(token)
            if found and found["expires"] > time.time():
                return found["user"]
            STATE["sessions"].pop(token, None)
        return None

    def start_session(self, user, expires_in=None):
        token = secrets.token_urlsafe(24)
        with LOCK:
            STATE["sessions"][token] = {"user": user, "expires": time.time() + (expires_in or STATE["ttl"])}
        # A lifetime on the cookie keeps the session across a browser restart, as a remembered sign-in does.
        return f"sid={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={int(expires_in or STATE['ttl'])}"

    def admin_ok(self):
        return bool(ADMIN_TOKEN) and same(self.headers.get("Authorization") or "", "Bearer " + ADMIN_TOKEN)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/sites/"):
            return sites.get(self, path)
        if path == "/":
            return self.send(200, page("Practice Login", "<p><a href='/login'>Log in</a> · <a href='/signup'>Sign up</a></p>"))
        if path == "/signup":
            return self.send(200, page("Sign up", "<form method=post action=/signup>" + field("username", "Username")
                                       + field("email", "Email", "email") + field("password", "Password", "password")
                                       + "<button type=submit>Sign up</button></form>"))
        if path == "/login":
            return self.send(200, page("Log in", "<form method=post action=/login>" + field("username", "Username")
                                       + field("password", "Password", "password")
                                       + "<button type=submit>Log in</button></form>"))
        if path == "/login/2fa":
            if not self.pre_user():
                return self.redirect("/login")
            return self.send(200, page("Authenticator code", "<form method=post action=/login/2fa>"
                                       + field("code", "Six-digit code", "text", "inputmode=numeric")
                                       + "<button type=submit>Continue</button></form>"))
        if path == "/me":
            user = self.session_user()
            if not user:
                return self.send(401, json.dumps({"error": "signed_out"}), "application/json")
            with LOCK:
                record = STATE["users"][user]
                two, phone = bool(record["totp"]), bool(record.get("phone"))
            return self.send(200, json.dumps({"user": user, "two_step": two, "phone_verified": phone}),
                             "application/json")
        if path == "/verify-phone":
            if not self.session_user():
                return self.redirect("/login")
            return self.send(200, page("Verify your phone", "<form method=post action=/verify-phone>"
                                       + field("phone", "Phone number", "tel", "placeholder=+15550000000")
                                       + "<button type=submit>Send code</button></form>"))
        if path == "/challenge":
            user = self.session_user()
            if not user:
                return self.redirect("/login")
            with LOCK:
                passed = user in STATE["challenge"]
            body = ("<p>Verified.</p>" if passed else
                    "<div id=hold style='position:fixed;inset:0;display:flex;align-items:center;justify-content:center;"
                    "background:#eef'>Press and hold anywhere for three seconds</div>"
                    "<script>let t=0;const h=document.getElementById('hold');"
                    "const seen=(event,ms)=>fetch('/challenge/seen',{method:'POST',headers:{'content-type':'application/json'},"
                    "body:JSON.stringify({event,ms}),credentials:'include',keepalive:true}).catch(()=>{});"
                    "document.addEventListener('pointerdown',()=>seen('down',0));"
                    "document.addEventListener('pointerup',()=>seen('up',0));"
                    "h.onpointerdown=()=>{t=Date.now()};"
                    "h.onpointerup=()=>{const ms=t?Date.now()-t:0;t=0;seen('hold',ms);if(ms>=3000)fetch('/challenge/pass',{method:'POST',"
                    "headers:{'content-type':'application/json'},body:JSON.stringify({held_ms:ms}),credentials:'include'})"
                    ".then(r=>{h.textContent=r.ok?'Verified':'Try again'})};</script>")
            return self.send(200, page("Check", body))
        if path == "/challenge/state":
            user = self.session_user()
            if not user:
                return self.send(401, json.dumps({"error": "signed_out"}), "application/json")
            with LOCK:
                passed = user in STATE["challenge"]
                last = STATE["challenge_last"].get(user)
                seen = list(STATE["challenge_seen"].get(user, []))
            return self.send(200, json.dumps({"passed": passed, "last": last, "seen": seen[-20:],
                                              "seen_count": len(seen)}), "application/json")
        if path == "/settings":
            return self.settings()
        if path == "/logout":
            return self.redirect("/login")
        if path == "/admin/state" and self.admin_ok():
            with LOCK:
                state = {"users": len(STATE["users"]), "sessions": len(STATE["sessions"]), "ttl": STATE["ttl"],
                         "beacon": STATE["beacon"],
                         "two_step": {name: bool(user["totp"]) for name, user in STATE["users"].items()}}
            return self.send(200, json.dumps(state), "application/json")
        return self.send(404, page("Not found", "<p>No such page.</p>"))

    def pre_user(self):
        token = self.cookie("pre")
        with LOCK:
            found = STATE["pre"].get(token)
            if found and found["expires"] > time.time():
                return found["user"]
        return None

    def settings(self):
        user = self.session_user()
        if not user:
            return self.redirect("/login")
        with LOCK:
            record = STATE["users"][user]
            if record["totp"]:
                body = "<p>Two-step sign-in is on.</p>"
            else:
                if not record["pending"]:
                    record["pending"] = totp.new_secret()
                key = record["pending"]
                body = (f"<p>Setup key: <code>{key}</code></p>"
                        f"<p>Authenticator link: <code>{html.escape(totp.otpauth_uri(key, user))}</code></p>"
                        "<form method=post action=/settings/2fa>" + field("code", "Code from your app", "text",
                                                                          "inputmode=numeric")
                        + "<button type=submit>Turn on two-step sign-in</button></form>")
        return self.send(200, page("Settings", body))

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        form = self.body()
        if path.startswith(("/sites/", "/admin/sites/")):
            return sites.post(self, path, form)
        if path == "/beacon":
            with LOCK:
                STATE["beacon"] = str(form.get("webdriver"))[:20]
            return self.send(204, "")
        if path == "/signup":
            return self.signup(form)
        if path == "/signup/verify":
            return self.signup_verify(form)
        if path == "/login":
            return self.login(form)
        if path == "/login/2fa":
            return self.login_2fa(form)
        if path == "/settings/2fa":
            return self.settings_2fa(form)
        if path == "/verify-phone":
            return self.verify_phone(form)
        if path == "/verify-phone/code":
            return self.verify_phone_code(form)
        if path == "/challenge/pass":
            return self.challenge_pass(form)
        if path == "/challenge/seen":
            user = self.session_user()
            if user:
                event, ms = str(form.get("event", ""))[:10], int(form.get("ms") or 0)
                with LOCK:
                    STATE["challenge_seen"].setdefault(user, []).append({"event": event, "ms": ms})
                print(f"challenge seen event={event} ms={ms}", file=sys.stderr, flush=True)
            return self.send(204, "")
        if path == "/logout":
            with LOCK:
                STATE["sessions"].pop(self.cookie("sid"), None)
            return self.redirect("/login", [("Set-Cookie", "sid=; Path=/; Max-Age=0")])
        if path == "/admin/expire" and self.admin_ok():
            with LOCK:
                count = len(STATE["sessions"])
                STATE["sessions"].clear()
            return self.send(200, json.dumps({"expired": count}), "application/json")
        if path == "/admin/ttl" and self.admin_ok():
            with LOCK:
                STATE["ttl"] = max(1, min(int(form.get("seconds", 1800)), 86400))
            return self.send(200, json.dumps({"ttl": STATE["ttl"]}), "application/json")
        return self.send(404, page("Not found", "<p>No such page.</p>"))

    def signup(self, form):
        username, email, password = (form.get(k, "").strip() for k in ("username", "email", "password"))
        if not USERNAME.match(username) or "@" not in email or len(password) < 8:
            return self.send(422, page("Sign up", "<p>Use a username of 3 to 32 letters, digits or underscores, "
                                                 "an email address and a password of at least 8 characters.</p>"))
        with LOCK:
            if username in STATE["users"]:
                return self.send(409, page("Sign up", "<p>That username is taken.</p>"))
            salt, digest = hash_password(password)
            code = f"{secrets.randbelow(10 ** 6):06d}"
            STATE["signups"][email] = {"username": username, "salt": salt, "hash": digest, "code": code,
                                       "expires": time.time() + CODE_TTL}
        try:
            deliver(email, "Confirm your Practice Login account",
                    f"Your verification code is {code}. It expires in ten minutes.")
        except (OSError, urllib.error.URLError, RuntimeError):
            return self.send(502, page("Sign up", "<p>We could not send the email. Try again.</p>"))
        return self.send(200, page("Check your email", "<form method=post action=/signup/verify>"
                                   + field("email", "Email", "email", f"value={html.escape(email, quote=True)}")
                                   + field("code", "Verification code", "text", "inputmode=numeric")
                                   + "<button type=submit>Verify</button></form>"))

    def signup_verify(self, form):
        email, code = form.get("email", "").strip(), form.get("code", "").strip()
        with LOCK:
            pending = STATE["signups"].get(email)
            if not pending or pending["expires"] < time.time() or not same(code, pending["code"]):
                return self.send(422, page("Check your email", "<p>That code is wrong or has expired.</p>"))
            if pending["username"] in STATE["users"]:
                return self.send(409, page("Sign up", "<p>That username is taken.</p>"))
            STATE["users"][pending["username"]] = {"email": email, "salt": pending["salt"], "hash": pending["hash"],
                                                   "totp": None, "pending": None}
            del STATE["signups"][email]
        return self.redirect("/login?created=1")

    def login(self, form):
        username, password = form.get("username", "").strip(), form.get("password", "")
        with LOCK:
            record = STATE["users"].get(username)
            ok = bool(record) and same(hash_password(password, record["salt"])[1], record["hash"])
            two = bool(ok and record["totp"])
        if not ok:
            return self.send(401, page("Log in", "<p>Wrong username or password.</p>"))
        if two:
            token = secrets.token_urlsafe(24)
            with LOCK:
                STATE["pre"][token] = {"user": username, "expires": time.time() + PRE_TTL}
            return self.redirect("/login/2fa", [("Set-Cookie", f"pre={token}; Path=/; HttpOnly; SameSite=Lax")])
        return self.redirect("/me", [("Set-Cookie", self.start_session(username))])

    def login_2fa(self, form):
        user = self.pre_user()
        if not user:
            return self.redirect("/login")
        with LOCK:
            secret = STATE["users"][user]["totp"]
        if not totp.verify(secret, form.get("code", "")):
            return self.send(401, page("Authenticator code", "<p>That code is wrong. Codes change every 30 seconds.</p>"))
        with LOCK:
            STATE["pre"].pop(self.cookie("pre"), None)
        return self.redirect("/me", [("Set-Cookie", self.start_session(user)),
                                     ("Set-Cookie", "pre=; Path=/; Max-Age=0")])

    def verify_phone(self, form):
        user = self.session_user()
        if not user:
            return self.redirect("/login")
        phone = form.get("phone", "").strip().replace(" ", "")
        if not re.fullmatch(r"\+1555\d{7}", phone):
            return self.send(422, page("Verify your phone", "<p>Use a US number from a company inbox.</p>"))
        code = f"{secrets.randbelow(10 ** 6):06d}"
        with LOCK:
            STATE["phones"][phone] = {"user": user, "code": code, "expires": time.time() + CODE_TTL}
        try:
            deliver(phone, "", f"Your verification code is {code}. It expires in ten minutes.", kind="sms")
        except (OSError, urllib.error.URLError, RuntimeError):
            return self.send(502, page("Verify your phone", "<p>We could not send the text. Try again.</p>"))
        return self.send(200, page("Enter the code", "<form method=post action=/verify-phone/code>"
                                   + field("phone", "Phone number", "tel", f"value={phone}")
                                   + field("code", "Code from the text", "text", "inputmode=numeric")
                                   + "<button type=submit>Verify</button></form>"))

    def verify_phone_code(self, form):
        user = self.session_user()
        if not user:
            return self.redirect("/login")
        phone, code = form.get("phone", "").strip(), form.get("code", "").strip()
        with LOCK:
            pending = STATE["phones"].get(phone)
            if not pending or pending["user"] != user or pending["expires"] < time.time() or not same(code, pending["code"]):
                return self.send(422, page("Enter the code", "<p>That code is wrong or has expired.</p>"))
            STATE["users"][user]["phone"] = phone
            del STATE["phones"][phone]
        return self.send(200, page("Phone verified", "<p>Your phone is verified.</p>"))

    def challenge_pass(self, form):
        user = self.session_user()
        if not user:
            return self.send(401, json.dumps({"error": "signed_out"}), "application/json")
        held = int(form.get("held_ms") or 0)
        if held < HOLD_MS:
            with LOCK:
                STATE["challenge_last"][user] = {"held_ms": held, "status": "too_short"}
            print(f"challenge held_ms={held} status=too_short", file=sys.stderr, flush=True)
            return self.send(422, json.dumps({"error": "too_short"}), "application/json")
        with LOCK:
            STATE["challenge"][user] = time.time()
            STATE["challenge_last"][user] = {"held_ms": held, "status": "passed"}
        print(f"challenge held_ms={held} status=passed", file=sys.stderr, flush=True)
        return self.send(200, json.dumps({"passed": True}), "application/json")

    def settings_2fa(self, form):
        user = self.session_user()
        if not user:
            return self.redirect("/login")
        with LOCK:
            record = STATE["users"][user]
            pending = record["pending"]
            if record["totp"] or not pending:
                return self.redirect("/settings")
            if not totp.verify(pending, form.get("code", "")):
                return self.send(401, page("Settings", "<p>That code is wrong. Codes change every 30 seconds.</p>"))
            record["totp"], record["pending"] = pending, None
        return self.redirect("/settings")


sites.bind(STATE, LOCK, hash_password, same, deliver, page, field)


def main():
    if not MAIL_URL:
        print("practice: PRACTICE_MAIL_URL must point at the fake Inbox", file=sys.stderr)
        return 2
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"practice: listening on {PORT}", file=sys.stderr, flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
