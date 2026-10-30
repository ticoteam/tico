#!/usr/bin/env python3
"""Setup for scripts/browser-sites.sh: the practice accounts, the login cards and the card changes the login shapes need.

Passwords and 2FA secrets go to files and are read from files, never given on a command line (a process list shows
arguments). Nothing here prints a secret. The subcommands:

  secret OUT KIND                      a password (KIND plain, unicode or long: 128 characters) or a base32 2FA key
  item OUT NAME USERNAME PASSWORD      a bitwarden export with one login: its name, username, password file, sites after
        [--totp-secret FILE]           the 2FA key file (the code is 8 digits, SHA256, 60 seconds) and the sites
  admin-user USERNAME PASSWORD [EMAIL] an account on the practice site (the admin token comes from the environment)
  admin-password USERNAME PASSWORD     sets an account's password on the practice site (a rotation)
  admin-totp USERNAME PASSWORD SECRET  an account whose code the 8-digit SHA256 page checks
  card-body OUT NAME USERNAME PASSWORD the body that changes a login card's password through the owner's API
  card-id NAME                         reads the credentials listing on stdin and prints the card's id
  bot-grant CARD BOT                   reads the credentials listing on stdin and prints "card grant" for the bot's grant
"""
import base64
import json
import os
import secrets
import sys
import urllib.request


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def password_for(kind):
    if kind == "plain":
        return secrets.token_urlsafe(18)
    if kind == "unicode":
        # Unicode letters, a double quote, a single quote, a backslash, an emoji and a space, then random.
        return "Pä\"ß'\\\U0001F511 " + secrets.token_urlsafe(9)
    if kind == "long":
        return secrets.token_urlsafe(96)[:128]   # 96 bytes encode to exactly 128 characters
    raise SystemExit(f"unknown kind {kind}")


def admin(method, path, body):
    port = os.environ["PRACTICE_PORT"]
    token = os.environ["PRACTICE_ADMIN_TOKEN"]
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method=method,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as reply:
        return reply.status


def main(argv):
    command, args = argv[0], argv[1:]
    if command == "secret":
        out, kind = args
        if kind == "base32":
            write_private(out, base64.b32encode(os.urandom(20)).decode().rstrip("="))
        else:
            write_private(out, password_for(kind))
        return 0
    if command == "item":
        out, name, username, password_file = args[:4]
        rest = args[4:]
        totp = None
        if rest[:1] == ["--totp-secret"]:
            secret = read(rest[1]).strip()
            totp = (f"otpauth://totp/Practice:{username}?secret={secret}&issuer=Practice"
                    "&algorithm=SHA256&digits=8&period=60")
            rest = rest[2:]
        login = {"username": username, "password": read(password_file), "uris": [{"uri": f"https://{s}/"} for s in rest]}
        if totp:
            login["totp"] = totp
        write_private(out, json.dumps({"encrypted": False, "items": [{"type": 1, "name": name, "login": login}]}))
        print(json.dumps({"items": 1}))
        return 0
    if command == "admin-user":
        username, password_file = args[:2]
        body = {"username": username, "password": read(password_file)}
        if len(args) > 2:
            body["email"] = args[2]
        admin("POST", "/admin/sites/user", body)
        return 0
    if command == "admin-password":
        username, password_file = args
        admin("POST", "/admin/sites/password", {"username": username, "password": read(password_file)})
        return 0
    if command == "admin-totp":
        username, password_file, secret_file = args
        admin("POST", "/admin/sites/totp-user", {"username": username, "password": read(password_file),
                                                "secret": read(secret_file).strip()})
        return 0
    if command == "card-body":
        out, name, username, password_file = args
        write_private(out, json.dumps({"name": name, "kind": "login", "username": username,
                                       "password": read(password_file)}))
        return 0
    if command == "card-id":
        listing = json.load(sys.stdin)
        found = [row for row in listing.get("credentials", []) if row.get("name") == args[0]]
        if len(found) != 1:
            return 1
        print(found[0]["id"])
        return 0
    if command == "bot-grant":
        listing = json.load(sys.stdin)
        subject = f"bot:{args[1]}"
        for row in listing.get("credentials", []):
            if row.get("name") != args[0]:
                continue
            for grant in row.get("grants", []):
                if grant.get("subject") == subject:
                    print(f"{row['id']} {grant['id']}")
                    return 0
        return 1
    raise SystemExit(f"unknown command {command}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
