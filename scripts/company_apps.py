#!/usr/bin/env python3
"""Private CI company builds. Public matrix/output contains only hashes of slugs.

TICO_COMPANY_APPS is supplied through a private Actions secret, never a source file.
--dry-run validates and reports hashes without network access or printing private values.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request


class Invalid(ValueError):
    pass


def url(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in
                ("localhost", "127.0.0.1", "::1"))) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise Invalid("URLs require HTTPS (HTTP is allowed only for localhost)")
        if port is not None and not 1 <= port <= 65535:
            raise Invalid("Invalid URL port")
    except (TypeError, ValueError) as exc:
        raise Invalid("URLs require HTTPS (HTTP is allowed only for localhost)") from exc
    return value.rstrip("/")


def entries_from_json(raw):
    try:
        entries = json.loads(raw or "[]")
    except (ValueError, TypeError) as exc:
        raise Invalid("TICO_COMPANY_APPS must be a JSON list") from exc
    if not isinstance(entries, list):
        raise Invalid("TICO_COMPANY_APPS must be a JSON list")
    return entries


def parse(raw):
    entries = entries_from_json(raw)
    seen, ids = set(), set()
    for entry in entries:
        if not isinstance(entry, dict) or any(not isinstance(entry.get(k), str) or not entry[k].strip()
                for k in ("slug", "id", "app_name", "url", "icon_url", "deploy_role_arn", "bucket")):
            raise Invalid("Each company needs slug, id, app_name, url, icon_url, deploy_role_arn and bucket")
        if any(not isinstance(v, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in v) for v in entry.values()):
            raise Invalid("Company values must be strings without control characters")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", entry["slug"]):
            raise Invalid("Invalid company slug")
        try:
            if str(uuid.UUID(entry["id"])) != entry["id"]:
                raise ValueError()
        except ValueError as exc:
            raise Invalid("Company id must be a canonical stable UUID") from exc
        if not re.fullmatch(r"[\w][\w. +()-]{0,79}", entry["app_name"]) or entry["app_name"].endswith((".", " ")):
            raise Invalid("Invalid company app name")
        if not re.fullmatch(r"[A-Za-z0-9]{0,4}", entry.get("tray_label", "")):
            raise Invalid("Company tray label must be empty or 1-4 ASCII letters/digits")
        url(entry["url"])
        if entry.get("runner_url"):
            url(entry["runner_url"])
        icon = entry["icon_url"]
        if icon.startswith("/") and not icon.startswith("//"):
            url(entry["url"].rstrip("/") + icon)
        else:
            url(icon)
        if not re.fullmatch(r"arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9_+=,.@/-]+", entry["deploy_role_arn"]):
            raise Invalid("Invalid company publish role")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", entry["bucket"]):
            raise Invalid("Invalid company bucket")
        prefix = entry.get("prefix", "").strip("/")
        if prefix and (not re.fullmatch(r"[A-Za-z0-9_/-]+", prefix) or any(p in ("", ".", "..") for p in prefix.split("/"))):
            raise Invalid("Invalid company prefix")
        if entry["slug"] in seen or entry["id"] in ids:
            raise Invalid("Duplicate company slug or id")
        seen.add(entry["slug"])
        ids.add(entry["id"])
    return entries


def key(entry):
    return hashlib.sha256(entry["slug"].encode()).hexdigest()


def mask(value):
    if value and os.environ.get("GITHUB_ACTIONS") == "true":
        escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::add-mask::" + escaped, flush=True)


def matrix(entries):
    return {"include": [{"company": key(e)} for e in entries]}


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def configure(entry, publishing=False):
    # No company identity is passed in process arguments or artifact names.
    base = url(entry.get("runner_url") or entry["url"])
    for value in (*entry.values(), base, base + "/download/latest.json",
                  "team.tico.env." + entry["id"], entry["deploy_role_arn"].split(":")[4]):
        mask(value)
    values = {"TICO_HUB_URL": entry["url"], "TICO_APP_NAME": entry["app_name"],
              "TICO_TRAY_LABEL": entry.get("tray_label", ""),
              "TICO_ENV_SLUG": entry["slug"], "COMPANY_ROLE": entry["deploy_role_arn"]}
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
        for name, value in values.items():
            stream.write(f"{name}={value}\n")
    if publishing:
        return
    icon_url = entry["icon_url"]
    if icon_url.startswith("/"):
        icon_url = entry["url"].rstrip("/") + icon_url
    with build_opener(SafeRedirect()).open(Request(icon_url), timeout=30) as response:
        icon = response.read(1024 * 1024 + 1)
    if len(icon) > 1024 * 1024 or not icon.startswith(b"\x89PNG\r\n\x1a\n"):
        raise Invalid("Company icon must be a PNG up to 1 MB")
    Path("app/company-icon.png").write_bytes(icon)
    version = os.environ["GITHUB_REF_NAME"].removeprefix("v")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?", version):
        raise Invalid("Company builds require a version tag")
    updater = {"endpoints": [base + "/download/latest.json"]}
    if base.startswith("http:"):
        updater["dangerousInsecureTransportProtocol"] = True
    conf = {"version": version, "productName": entry["app_name"],
            "identifier": "team.tico.env." + entry["id"], "mainBinaryName": "tico-" + key(entry)[:16],
            "bundle": {"icon": ["company-icons/32x32.png", "company-icons/128x128.png",
                                    "company-icons/128x128@2x.png", "company-icons/icon.icns", "company-icons/icon.ico"]},
            "plugins": {"updater": updater}}
    Path("app/company-config.json").write_text(json.dumps(conf), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("matrix", "configure", "build", "pack", "unpack", "publish"))
    parser.add_argument("--company")
    parser.add_argument("--target")
    parser.add_argument("--bundles")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--publishing", action="store_true")
    args = parser.parse_args(argv)
    try:
        entries = entries_from_json(os.environ.get("TICO_COMPANY_APPS", ""))
        if args.action == "matrix":
            if args.dry_run:
                parse(json.dumps(entries))
            # Validate each company in its own job: one malformed entry must not stop
            # the other companies. Even invalid identities stay out of public output.
            usable = [e for e in entries if isinstance(e, dict) and isinstance(e.get("slug"), str) and e["slug"]]
            if len(usable) != len(entries):
                print("::warning::Company entries without a slug cannot be built", file=sys.stderr)
                if not usable:
                    raise Invalid("Each company needs a slug")
            value = json.dumps({"include": [{"company": k} for k in dict.fromkeys(key(e) for e in usable)]}, separators=(",", ":"))
            print(value)
            if not args.dry_run and os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                    stream.write("matrix=" + value + "\n")
                    stream.write("enabled=" + str(bool(usable)).lower() + "\n")
            return 0
        matches = [e for e in entries if isinstance(e, dict) and isinstance(e.get("slug"), str) and key(e) == args.company]
        entry = matches[0] if len(matches) == 1 else None
        if entry is None:
            raise Invalid("Company hash was not found in the private configuration")
        parse(json.dumps([entry]))
        if sum(isinstance(e, dict) and e.get("id") == entry["id"] for e in entries) != 1:
            raise Invalid("Duplicate company id")
        if args.dry_run:
            print(f"Validated {args.action} for {key(entry)}")
            return 0
        if args.action == "configure":
            configure(entry, args.publishing)
        elif args.action in ("pack", "unpack"):
            if __package__:
                from . import company_app_artifacts as artifacts
            else:
                import company_app_artifacts as artifacts
            if args.action == "pack":
                artifacts.pack(key(entry), args.target)
            else:
                artifacts.unpack(key(entry))
        elif args.action == "build":
            # Build tools can print derived private paths; suppress their output entirely.
            with open(os.devnull, "w") as log:
                subprocess.run(["cargo", "tauri", "icon", "company-icon.png", "-o", "company-icons"],
                               cwd="app", stdout=log, stderr=log, check=True)
                subprocess.run(["cargo", "tauri", "build", "--target", args.target, "--bundles", args.bundles,
                                "--config", "company-config.json"], cwd="app", stdout=log, stderr=log, check=True)
        else:
            if __package__:
                from .app_release import main as release
            else:
                from app_release import main as release
            flags = ["--version", os.environ["GITHUB_REF_NAME"].removeprefix("v"), "--bucket", entry["bucket"],
                     "--base", entry.get("runner_url") or entry["url"], "--prefix", entry.get("prefix", ""),
                     "--quiet", "--complete", "bundles"]
            if os.environ.get("APPLE_SIGNING_IDENTITY"):
                flags.append("--signed")
            if os.environ.get("APPLE_ID"):
                flags.append("--notarized")
            release(flags)
        print(f"Completed {args.action} for {key(entry)}")
        return 0
    except Invalid as exc:
        print("::error::" + str(exc), file=sys.stderr)
        return 1
    except Exception:
        # Neither remote errors nor compiler exceptions may echo company identifiers.
        print("::error::Company app operation failed; verify private configuration, icon access, signing and publish permissions", file=sys.stderr)
        return 1
    except SystemExit as exc:
        if exc.code:
            print("::error::Company app publication failed; verify signed bundles and storage", file=sys.stderr)
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
