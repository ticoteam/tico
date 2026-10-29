#!/usr/bin/env python3
"""The person's and the checker's side of scripts/browser-journey.sh, stdlib only.

It makes the calls a person would make (sign up and sign in to the practice site, read its setup key, switch two-step
sign-in on, save a login card, end a site reconnect) and the leak check. Values stay in files in the journey's own work
folder, which the journey removes at the end. Nothing here prints a password, a secret, a code or a message body: the
output is ids, counts and PASS or FAIL lines.

  password OUT                                  a random practice password, into OUT (mode 0600)
  api METHOD PATH [--body-file F]               an owner API call on the install; prints the JSON answer
  inbox-create --name N                         an owner email inbox; prints its inbox_id and address
  report FILE                                   a bot phase's result file as PASS/FAIL lines; exit 1 on any FAIL
  field FILE KEY                                one value from a bot phase's result data (ids, never secrets)
  person-signup --practice URL --inbox-id ID --email E --user U --password-file P
                                                signs up on the practice site; the code comes from the inbox
  person-2fa --practice URL --user U --password-file P --secret-out S
                                                reads the setup key, switches two-step sign-in on, checks the second step
  setup-key --practice URL --user U --password-file P --out OUT
                                                signs in as the person and writes the setup key to OUT
  save-card REQUEST_ID --username U --password-file P [--totp-file T]
                                                saves a login card for a bot's request
  reconnect-id HOST                             the open site reconnect for HOST in Needs you, as JSON {id, task, key}
                                                ('' when none)
  import-file OUT                               a Bitwarden export with two logins (one with 2FA), into OUT (mode 0600)
  import-logins FILE                            imports FILE as the owner; prints the counts (never the values)
  code-refused NAME                             the owner's API token asks for the 2FA code of credential NAME: prints
                                                the HTTP status (403 when a person must open it, signed in)
  needles --secret-file S... --password-file P... --start EPOCH --mail FILE [--import-file F] --out OUT
                                                the values the leak check looks for
  leakscan --needles FILE --source LABEL=PATH [...]
                                                search each source; exit 1 when a needle is found, 2 when there are none

Environment: TICO_PORT (the install's loopback port), TOKEN_FILE (the owner token), PRACTICE_ADMIN_TOKEN.
"""
import argparse
import http.cookiejar
import json
import os
import re
import secrets
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "login-practice"))
import totp  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def install(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{os.environ['TICO_PORT']}{path}", data=data, method=method,
                                     headers={"Authorization": "Bearer " + read(os.environ["TOKEN_FILE"]),
                                              "Content-Type": "application/json",
                                              "Idempotency-Key": "j-" + secrets.token_hex(8)})
    try:
        with urllib.request.urlopen(request, timeout=60) as reply:
            return json.loads(reply.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        raise SystemExit(f"journey: {method} {path} answered {error.code}: {problem_text(error)}")


def problem_text(error):
    # What the server says is wrong, never what was sent back: a validation error can repeat its input, and the input
    # may be a password.
    try:
        data = json.loads(error.read().decode() or "{}")
    except (ValueError, OSError):
        return ""
    if not isinstance(data, dict):
        return ""
    problem = data.get("error")
    if isinstance(problem, dict):   # the install's refusals: a code and a sentence
        return f"{problem.get('code', '')}: {str(problem.get('detail', ''))[:160]}"
    if isinstance(data.get("message"), str):
        return data["message"][:160]
    detail = data.get("detail")
    if isinstance(detail, str):
        return detail[:160]
    if isinstance(detail, list):
        parts = [".".join(str(p) for p in item.get("loc", [])) + ": " + str(item.get("msg", ""))
                 for item in detail if isinstance(item, dict)]
        return "; ".join(parts)[:160]
    return ""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def practice_session():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)
    return opener


def post_form(opener, url, fields):
    body = urllib.parse.urlencode(fields).encode()
    try:
        with opener.open(urllib.request.Request(url, data=body, method="POST"), timeout=30) as reply:
            return reply.status, reply.headers.get("Location", ""), reply.read().decode(errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Location", ""), ""


def get_page(opener, url):
    # A signed-out page redirects to the login page, which the no-redirect opener reports as an error: that is an answer.
    try:
        with opener.open(url, timeout=30) as reply:
            return reply.status, reply.read().decode(errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, ""


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    return ok


def cmd_password(args):
    alphabet = string.ascii_letters + string.digits + "-_"
    write_private(args.out, "".join(secrets.choice(alphabet) for _ in range(20)))


def cmd_api(args):
    body = json.loads(read(args.body_file)) if args.body_file else None
    print(json.dumps(install(args.method, args.path, body)))


def wait_for_code(inbox_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        found = install("POST", "/api/v2/inbound/wait", {"inbox_ids": [inbox_id], "after": 0, "has_code": True,
                                                         "timeout": 20, "limit": 5}).get("messages", [])
        if found:
            return install("GET", f"/api/v2/inbound/messages/{found[0]['message_id']}/code")["code"]
    return ""


def cmd_person_signup(args):
    opener = practice_session()
    status, location, _ = post_form(opener, args.practice + "/signup",
                                    {"username": args.user, "email": args.email, "password": read(args.password_file)})
    ok = check("person: the practice site sent the signup code", status == 200)
    code = wait_for_code(args.inbox_id) if ok else ""
    ok = check("person: the code reached the inbox", bool(code)) and ok
    if ok:
        status, location, _ = post_form(opener, args.practice + "/signup/verify", {"email": args.email, "code": code})
        ok = check("person: the account was created", status == 303 and location.endswith("/login?created=1"))
    return 0 if ok else 1


def cmd_person_2fa(args):
    password = read(args.password_file)
    opener = practice_session()
    status, location, _ = post_form(opener, args.practice + "/login", {"username": args.user, "password": password})
    settings_status, page = get_page(opener, args.practice + "/settings")
    match = re.search(r"Setup key: <code>([A-Z2-7]{16,})</code>", page)
    results = [check("person: the setup key is shown on the settings page", bool(match))]
    if not match:
        return 1
    secret = match.group(1)
    write_private(args.secret_out, secret)
    status, _, _ = post_form(opener, args.practice + "/settings/2fa", {"code": totp.code(secret)})
    results.append(check("person: two-step sign-in is switched on", status == 303))
    fresh = practice_session()
    status, location, _ = post_form(fresh, args.practice + "/login", {"username": args.user, "password": password})
    results.append(check("person: the password alone leads to the second step", location.endswith("/login/2fa")))
    status, _, _ = post_form(fresh, args.practice + "/login/2fa", {"code": "000000" if totp.code(secret) != "000000" else "111111"})
    results.append(check("person: a wrong code is refused", status == 401))
    status, location, _ = post_form(fresh, args.practice + "/login/2fa", {"code": totp.code(secret)})
    results.append(check("person: the right code signs in", location.endswith("/me")))
    me = get_page(fresh, args.practice + "/me")[0]
    results.append(check("person: /me is 200 once signed in", me == 200))
    return 0 if all(results) else 1


def cmd_setup_key(args):
    opener = practice_session()
    post_form(opener, args.practice + "/login", {"username": args.user, "password": read(args.password_file)})
    _, page = get_page(opener, args.practice + "/settings")
    match = re.search(r"Setup key: <code>([A-Z2-7]{16,})</code>", page)
    if not match:
        raise SystemExit("journey: the practice settings page shows no setup key")
    write_private(args.out, match.group(1))
    print("setup key read")


def cmd_save_card(args):
    body = {"username": args.username, "password": read(args.password_file)}
    if args.totp_file:
        body["totp"] = read(args.totp_file)
    reply = install("POST", f"/api/v2/credential-requests/{args.request_id}/save", body)
    print(json.dumps({"status": reply.get("status", "saved")}))


def find_reconnect(node, host):
    if isinstance(node, dict):
        if node.get("host") == host and node.get("id"):
            return node
        for value in node.values():
            found = find_reconnect(value, host)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_reconnect(value, host)
            if found:
                return found
    return None


def cmd_reconnect_id(args):
    card = find_reconnect(install("GET", "/api/v2/needs-you"), args.host)
    print(json.dumps({"id": card.get("id", ""), "task": card.get("task", ""), "key": card.get("key", "")}) if card else "")


IMPORT_NAMES = ("Journey import one", "Journey import two")


def cmd_import_file(args):
    # The two logins' passwords, and the 2FA key of the second, are what the leak check looks for in every other source.
    items = [
        {"type": 1, "name": IMPORT_NAMES[0], "login": {"username": "import_one", "password": secrets.token_urlsafe(18),
                                                      "uris": [{"uri": "https://login.practice.test/"}]}},
        {"type": 1, "name": IMPORT_NAMES[1], "login": {"username": "import_two", "password": secrets.token_urlsafe(18),
                                                      "totp": totp.new_secret(),
                                                      "uris": [{"uri": "https://other.practice.test/"}]}},
    ]
    write_private(args.out, json.dumps({"encrypted": False, "items": items}))
    print(json.dumps({"items": len(items)}))


def cmd_import_logins(args):
    reply = install("POST", "/api/v2/credentials/import", {"format": "bitwarden", "content": read(args.file)})
    print(json.dumps({"imported": reply.get("imported"), "skipped": reply.get("skipped"),
                      "errors": len(reply.get("errors") or [])}))


def cmd_code_refused(args):
    listed = install("GET", "/api/v2/credentials")
    found = [item["id"] for item in listed.get("credentials", []) if item.get("name") == args.name]
    if not found:
        raise SystemExit(f"journey: no credential named {args.name}")
    request = urllib.request.Request(f"http://127.0.0.1:{os.environ['TICO_PORT']}/api/v2/credentials/{found[0]}/code",
                                     headers={"Authorization": "Bearer " + read(os.environ["TOKEN_FILE"])})
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:
            status = reply.status
    except urllib.error.HTTPError as error:
        status = error.code
    print(json.dumps({"status": status}))


def cmd_inbox_create(args):
    made = install("POST", "/api/v2/inbound/inboxes", {"kind": "email", "name": args.name})
    print(json.dumps({"inbox_id": made["inbox_id"], "address": made["address"]}))


def cmd_report(args):
    # One line per check: its name, and for a failure the short reason the phase gave.
    data = json.loads(read(args.file))
    checks = data.get("checks") or []
    for item in checks:
        ok = item.get("ok") is True
        # A failed check says why in a few words; the detail is written by the phase and never holds a secret.
        note = "" if ok or not item.get("detail") else f" ({item['detail']})"
        check(str(item.get("name", "")) + note, ok)
    if not checks:
        print("FAIL the phase reported no checks")
        return 1
    return 0 if data.get("ok") is True else 1


def cmd_field(args):
    print(json.loads(read(args.file)).get("data", {}).get(args.key, ""))


def cmd_needles(args):
    needles = []
    if args.import_file and os.path.exists(args.import_file):
        # An export's values are expected in the export file itself (its source is exempt), nowhere else.
        for item in json.loads(read(args.import_file)).get("items", []):
            login = item.get("login") or {}
            if login.get("password"):
                needles.append({"label": "import-password", "value": login["password"], "not_in": ["import"]})
            if login.get("totp"):
                needles.append({"label": "import-totp", "value": login["totp"], "not_in": ["import"]})
    for path in args.secret_file:
        if not os.path.exists(path):   # a step that never got a key left no file: nothing to look for
            continue
        secret = read(path)
        needles.append({"label": "2fa-secret", "value": secret})
        # Every code the authenticator showed since the run began, a few periods either side.
        for step in range(int(args.start) // 30 - 2, int(time.time()) // 30 + 3):
            needles.append({"label": "2fa-code", "value": totp.code(secret, at=step * 30 + 1), "digits": True})
    for path in args.password_file:
        needles.append({"label": "password", "value": read(path)})
    # The codes the practice site emailed: the company's inbox keeps them as mail, by design, so the database is not
    # searched for them; every other place is.
    if args.mail and os.path.exists(args.mail):
        for text in json.loads(read(args.mail)).get("texts", []):
            for code in re.findall(r"code is (\d{6})", text):
                needles.append({"label": "email-code", "value": code, "digits": True, "not_in": ["db"]})
    write_private(args.out, json.dumps(needles))
    print(f"needles: {len(needles)}")


def scan_for(needle, data):
    value = needle["value"].encode()
    if needle.get("digits"):
        return re.search(rb"(?<![0-9])" + re.escape(value) + rb"(?![0-9])", data) is not None
    return value in data


def cmd_leakscan(args):
    needles = json.loads(read(args.needles))
    if not any(n["label"] == "password" for n in needles):
        print("leakscan: no password needle, so the check did not run")
        return 2
    leaked = set()
    for spec in args.source:
        label, _, path = spec.partition("=")
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            print(f"leakscan: {label}: unreadable")
            continue
        found = sorted({n["label"] for n in needles if label not in n.get("not_in", []) and scan_for(n, data)})
        leaked.update(found)
        print(f"leakscan: {label}: {'found ' + ', '.join(found) if found else 'clean'} ({len(data)} bytes)")
    return 1 if leaked else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    item = sub.add_parser("password")
    item.add_argument("out")
    item.set_defaults(fn=cmd_password)
    item = sub.add_parser("api")
    item.add_argument("method")
    item.add_argument("path")
    item.add_argument("--body-file")
    item.set_defaults(fn=cmd_api)
    item = sub.add_parser("person-signup")
    item.add_argument("--practice", required=True)
    item.add_argument("--inbox-id", required=True)
    item.add_argument("--email", required=True)
    item.add_argument("--user", required=True)
    item.add_argument("--password-file", required=True)
    item.set_defaults(fn=cmd_person_signup)
    item = sub.add_parser("person-2fa")
    item.add_argument("--practice", required=True)
    item.add_argument("--user", required=True)
    item.add_argument("--password-file", required=True)
    item.add_argument("--secret-out", required=True)
    item.set_defaults(fn=cmd_person_2fa)
    item = sub.add_parser("setup-key")
    item.add_argument("--practice", required=True)
    item.add_argument("--user", required=True)
    item.add_argument("--password-file", required=True)
    item.add_argument("--out", required=True)
    item.set_defaults(fn=cmd_setup_key)
    item = sub.add_parser("save-card")
    item.add_argument("request_id")
    item.add_argument("--username", required=True)
    item.add_argument("--password-file", required=True)
    item.add_argument("--totp-file")
    item.set_defaults(fn=cmd_save_card)
    item = sub.add_parser("reconnect-id")
    item.add_argument("host")
    item.set_defaults(fn=cmd_reconnect_id)
    item = sub.add_parser("import-file")
    item.add_argument("out")
    item.set_defaults(fn=cmd_import_file)
    item = sub.add_parser("import-logins")
    item.add_argument("file")
    item.set_defaults(fn=cmd_import_logins)
    item = sub.add_parser("code-refused")
    item.add_argument("name")
    item.set_defaults(fn=cmd_code_refused)
    item = sub.add_parser("inbox-create")
    item.add_argument("--name", required=True)
    item.set_defaults(fn=cmd_inbox_create)
    item = sub.add_parser("report")
    item.add_argument("file")
    item.set_defaults(fn=cmd_report)
    item = sub.add_parser("field")
    item.add_argument("file")
    item.add_argument("key")
    item.set_defaults(fn=cmd_field)
    item = sub.add_parser("needles")
    item.add_argument("--secret-file", action="append", default=[])
    item.add_argument("--password-file", action="append", default=[])
    item.add_argument("--start", required=True)
    item.add_argument("--mail")
    item.add_argument("--import-file")
    item.add_argument("--out", required=True)
    item.set_defaults(fn=cmd_needles)
    item = sub.add_parser("leakscan")
    item.add_argument("--needles", required=True)
    item.add_argument("--source", action="append", default=[])
    item.set_defaults(fn=cmd_leakscan)
    args = parser.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
