#!/usr/bin/env python3
"""Publish desktop app bundles: upload them to `releases/app/<version>/` in the storage bucket
and rewrite `releases/app/latest.json`, the manifest the app's updater and the hub's download
routes read (backend/downloads.py).

    app_release.py --version 2.1.0 --bucket <bucket> --base https://runner.acme.example DIR [DIR...]

Each DIR is scanned for what `tauri build` produced on one OS:
  *.app.tar.gz (+ .sig)   macOS update archive         -> platforms darwin-aarch64 / darwin-x86_64
  *.dmg                   macOS installer              -> installers.mac
  *-setup.exe (+ .sig)    Windows installer            -> platforms windows-x86_64, installers.windows
  *.AppImage (+ .sig)     Linux                        -> platforms linux-x86_64, installers.linux
  *.deb                   uploaded beside the AppImage -> installers.linux_deb
A platform that is not in any DIR keeps its entry from the manifest already published, so one
OS can be released on its own. `--dry-run` prints the manifest and uploads nothing.
`--github --version X.Y.Z --output latest.json DIR [DIR...]` writes a complete release's
manifest using public GitHub asset URLs, without credentials or S3. `--tag` preserves a tag suffix.
`--kind recorder` does the same for Tico Recorder (macOS only, so only the darwin platforms and the mac
installer are required); its manifest is recorder-latest.json, read by the recorder's updater.
"""
import argparse
import json
import re
import sys
from urllib.parse import quote
from datetime import datetime, timezone
from pathlib import Path

PREFIX = "releases/app/"


def classify(directory):
    found = {"platforms": {}, "installers": {}, "files": []}
    for path in sorted(Path(directory).rglob("*")):
        if not path.is_file() or path.suffix == ".sig":
            continue
        name = path.name
        sig = path.with_name(name + ".sig")
        signature = sig.read_text().strip() if sig.exists() else None
        entry = {"file": name, "bytes": path.stat().st_size}
        if name.endswith(".app.tar.gz"):
            for target in ("darwin-aarch64", "darwin-x86_64"):
                if "universal" in name or target.split("-")[1] in name or "aarch64" not in name and "x86_64" not in name:
                    found["platforms"][target] = {**entry, "signature": signature}
        elif name.endswith(".dmg"):
            found["installers"]["mac"] = entry
        elif name.endswith("-setup.exe"):
            found["platforms"]["windows-x86_64"] = {**entry, "signature": signature}
            found["installers"]["windows"] = entry
        elif name.endswith(".msi"):
            found["installers"].setdefault("windows", entry)
        elif name.endswith(".AppImage"):
            found["platforms"]["linux-x86_64"] = {**entry, "signature": signature}
            found["installers"]["linux"] = entry
        elif name.endswith(".deb"):
            found["installers"]["linux_deb"] = entry
        else:
            continue
        found["files"].append(path)
        if signature is not None:
            found["files"].append(sig)
    return found


def manifest(version, base, scans, previous, notes="", signed=False, notarized=False, github=False, tag=None,
             kind="app"):
    asset_base = f"https://github.com/ticoteam/tico/releases/download/{quote(tag or 'v' + version, safe='')}"
    platforms = dict((previous or {}).get("platforms") or {})
    installers = dict((previous or {}).get("installers") or {})
    for scan in scans:
        for target, entry in scan["platforms"].items():
            if not entry.get("signature"):
                raise SystemExit(f"{entry['file']} has no .sig beside it; build with TAURI_SIGNING_PRIVATE_KEY set")
            platforms[target] = {"signature": entry["signature"],
                                 "url": f"{asset_base}/{quote(entry['file'])}" if github else f"{base}/download/file/{quote(version, safe='')}/{quote(entry['file'])}"}
        for os_name, entry in scan["installers"].items():
            installers[os_name] = {"file": entry["file"], "bytes": entry["bytes"],
                                   "signed": signed and os_name == "mac", "notarized": signed and notarized and os_name == "mac"}
            if github:
                installers[os_name]["url"] = f"{asset_base}/{quote(entry['file'])}"
    return {"version": version, "notes": notes, "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "platforms": platforms, "installers": installers,
            "app_kind": kind if kind == "recorder" else "generic" if github else "company"}


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dirs", nargs="+")
    parser.add_argument("--version", required=True)
    parser.add_argument("--bucket")
    parser.add_argument("--base", help="the hub URL the app reaches without a browser session")
    parser.add_argument("--github", action="store_true", help="write a GitHub release manifest without accessing S3")
    parser.add_argument("--tag", help="GitHub tag (defaults to v<version>)")
    parser.add_argument("--output", default="latest.json", help="manifest path for --github")
    parser.add_argument("--notes", default="")
    parser.add_argument("--signed", action="store_true")
    parser.add_argument("--notarized", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prefix", default="", help="storage prefix before releases/app/")
    parser.add_argument("--quiet", action="store_true", help="do not print private manifest or filenames")
    parser.add_argument("--complete", action="store_true", help="require all company platforms before publishing")
    parser.add_argument("--kind", choices=("app", "recorder"), default="app", help="recorder: macOS bundles only")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?", args.version):
        parser.error("invalid version")
    prefix = args.prefix.strip("/")
    if prefix and (not re.fullmatch(r"[A-Za-z0-9_/-]+", prefix) or any(p in ("", ".", "..") for p in prefix.split("/"))):
        parser.error("invalid storage prefix")
    prefix = (prefix + "/" if prefix else "") + PREFIX
    if not args.github and (not args.bucket or not args.base):
        parser.error("--bucket and --base are required unless --github is set")
    scans = [classify(d) for d in args.dirs]
    if not any(s["files"] for s in scans):
        raise SystemExit("no bundles found in " + ", ".join(args.dirs))
    previous = None
    s3 = None
    if not args.dry_run and not args.github:
        import boto3
        s3 = boto3.client("s3")
        try:
            previous = json.loads(s3.get_object(Bucket=args.bucket, Key=prefix + "latest.json")["Body"].read())
            if args.complete:
                previous = None
        except Exception:
            previous = None
    value = manifest(args.version, (args.base or "").rstrip("/"), scans, previous, args.notes, args.signed,
                     args.notarized, args.github, args.tag, args.kind)
    if args.kind == "recorder":
        required, installers_needed = {"darwin-aarch64", "darwin-x86_64"}, {"mac"}
        problem = "Recorder releases need the macOS universal app archive and disk image"
    else:
        required = {"darwin-aarch64", "darwin-x86_64", "windows-x86_64", "linux-x86_64"}
        installers_needed = {"mac", "windows", "linux", "linux_deb"}
        problem = "GitHub releases need macOS universal, Windows NSIS, Linux AppImage and Debian bundles"
    if args.github or args.complete:
        if not required.issubset(value["platforms"]) or not installers_needed.issubset(value["installers"]):
            raise SystemExit(problem)
    if not args.quiet:
        print(json.dumps(value, indent=2))
    if args.dry_run:
        return 0
    if args.github:
        Path(args.output).write_text(json.dumps(value, indent=2) + "\n")
        return 0
    for scan in scans:
        for path in scan["files"]:
            key = f"{prefix}{args.version}/{path.name}"
            if not args.quiet:
                print("upload", key, file=sys.stderr)
            s3.upload_file(str(path), args.bucket, key)
    s3.put_object(Bucket=args.bucket, Key=prefix + "latest.json", Body=json.dumps(value).encode(),
                  ContentType="application/json", CacheControl="no-cache")
    if not args.quiet:
        print("published", args.version, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
