#!/usr/bin/env python3
"""Prepare, validate and publish locally built release artifacts; never compiles or builds images."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

IMAGES = ("tico", "tico-runner", "tico-updater")
PLATFORMS = ("linux/amd64", "linux/arm64")
RUNNERS = {"linux-arm64": ("elf", 183), "linux-x86_64": ("elf", 62),
           "macos-arm64": ("macho", 0x100000C), "macos-x86_64": ("macho", 0x1000007)}
DESKTOP_INPUTS = ("app", "scripts/app_release.py", "scripts/company_apps.py",
                  ".github/workflows/app.yml", ".github/workflows/company-app.yml")
BUILD_WORKFLOWS = ("docker.yml", "release.yml", "app.yml")
PHASES = {"journey", "build-server", "build-runner", "build-updater", "all-python", "all-browser",
          "smoke", "side-jobs", "isolation"}
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
VERSION = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.]+)?\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def command(args, *, missing=False):
    result = subprocess.run(args, capture_output=True, text=True, env={**os.environ,
                            "GIT_OPTIONAL_LOCKS": "0", "PYTHONDONTWRITEBYTECODE": "1"}, timeout=300)
    if missing and result.returncode and "(HTTP 404)" in result.stderr:
        return None
    require(result.returncode == 0, f"{args[0]} command failed (exit {result.returncode}); no automatic retry")
    return result.stdout


def gh(repo, path, *, missing=False):
    raw = command(["gh", "api", f"repos/{repo}/{path}"], missing=missing)
    return json.loads(raw) if raw is not None else None


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def module(source, relative):
    namespace = {"__name__": "prebuilt_helper", "__file__": str(source / relative)}
    exec(compile((source / relative).read_bytes(), str(source / relative), "exec"), namespace)
    return namespace


def check_source(source, version, commit, repo):
    require(VERSION.fullmatch(version) and SHA.fullmatch(commit), "Use a version tag and full commit SHA")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo), "Invalid repository")
    require(command(["git", "-C", str(source), "rev-parse", "HEAD"]).strip() == commit, "Source HEAD differs from approval")
    require(not command(["git", "-C", str(source), "status", "--porcelain"]).strip(), "Source checkout must be clean")
    notes = module(source, "scripts/changelog_notes.py")["section"]((source / "CHANGELOG.md").read_text(), version[1:])
    require(bool(notes.strip()), "Missing versioned release notes")
    return notes


def source_id(source):
    paths = [source / "runner-rs" / x for x in ("build.rs", "Cargo.toml", "Cargo.lock")]
    paths += list((source / "runner-rs/src").rglob("*")) + list((source / "runner/harnesses").glob("*.toml"))
    h = hashlib.sha256()
    for p in sorted((p for p in paths if p.is_file()), key=lambda p: p.relative_to(source).as_posix()):
        raw = p.read_bytes()
        h.update(f"{p.relative_to(source).as_posix()}\0{len(raw)}\0".encode() + raw)
    return h.hexdigest()[:16]


def gate(log, commit):
    text = log.read_text()
    require(len(re.findall(r"^Release check: candidate .* from " + commit[:7] + r";", text, re.M)) == 1,
            "Gate log must identify this candidate once")
    phases = re.findall(r"^\| ([a-z-]+) \| [0-9.]+ \| [0-9.]+ \| ([0-9]+) \|$", text, re.M)
    require(len(phases) == len(PHASES) and {name for name, _ in phases} == PHASES and
            all(code == "0" for _, code in phases), "Gate must contain all nine successful phases")
    require(re.search(r"^Release check passed(?:,|:)", text, re.M), "Gate did not pass")
    return sha256(log.read_bytes())


def checksums(path, expected):
    found = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        require(match is not None, f"Malformed checksum in {path.name}")
        digest, name = match.groups()
        require(name not in found, f"Duplicate checksum for {name}")
        found[name] = digest
    require(set(found) == set(expected), f"Incomplete or extra entries in {path.name}")
    for name, digest in found.items():
        p = path.parent / name
        require(p.is_file() and not p.is_symlink() and sha256(p.read_bytes()) == digest, f"Checksum mismatch: {name}")
    return found


def runners(directory, build_id):
    names = [f"tico-runner-{name}.gz" for name in RUNNERS]
    sums = checksums(directory / "tico-runner-SHA256SUMS", names)
    for name, (kind, machine) in RUNNERS.items():
        with gzip.open(directory / f"tico-runner-{name}.gz", "rb") as stream:
            raw = stream.read(64 * 1024 * 1024 + 1)
        require(20 <= len(raw) <= 64 * 1024 * 1024, f"Invalid runner size: {name}")
        if kind == "elf":
            valid = raw[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<H", raw, 18)[0] == machine
        else:
            valid = raw[:4] == b"\xcf\xfa\xed\xfe" and struct.unpack_from("<I", raw, 4)[0] == machine
        require(valid and build_id.encode() in raw, f"Wrong runner platform or source fingerprint: {name}")
    return sums


def image_receipt(receipt, source, version, commit, repo):
    require(receipt.get("version") == version and receipt.get("commit") == commit and receipt.get("repository") == repo,
            "Build receipt differs from the approved version/source/repository")
    images = receipt.get("images", {})
    require(set(images) == set(IMAGES), "Receipt must include all three images")
    public = {}
    code = module(source, "backend/code_hash.py")["digest"](source)
    build_id = source_id(source)
    updater = sha256((source / "docker/updater.py").read_bytes())
    for image in IMAGES:
        public[image] = {}
        require(set(images[image]) == set(PLATFORMS), f"Receipt must include both platforms: {image}")
        for platform, row in images[image].items():
            require(isinstance(row, dict) and DIGEST.fullmatch(row.get("digest", "")) and
                    DIGEST.fullmatch(row.get("config_digest", "")), f"Missing immutable image/config digests: {image}/{platform}")
            require(row.get("commit") == commit and row.get("repository") == repo, "Wrong image source receipt")
            if image == "tico-updater":
                require(row.get("updater_sha256") == updater, "Updater file does not match the approved source")
            else:
                require(row.get("code_sha256") == code, "Image release-manifest code does not match the approved source")
            if image == "tico-runner":
                require(row.get("runner_build_id") == build_id, "Image runner fingerprint differs from source")
            fields = {"digest", "config_digest", "commit", "repository"}
            fields.add("updater_sha256" if image == "tico-updater" else "code_sha256")
            if image == "tico-runner":
                fields.add("runner_build_id")
            public[image][platform] = {key: row[key] for key in sorted(fields)}
    return public


def registry(images, version, commit, repo):
    owner = repo.split('/')[0]
    for image in IMAGES:
        ref = f"ghcr.io/{owner}/{image}"
        index = json.loads(command(["docker", "buildx", "imagetools", "inspect", f"{ref}:{version}", "--raw"]))
        entries = {}
        for item in index.get("manifests", []):
            p = item.get("platform", {})
            platform = p.get("os", "") + "/" + p.get("architecture", "")
            if platform == "unknown/unknown" and item.get("annotations", {}).get("vnd.docker.reference.type") == "attestation-manifest":
                continue
            require(platform in PLATFORMS and platform not in entries, f"Unexpected or duplicate image platform: {image}/{platform}")
            entries[platform] = item.get("digest")
        require(set(entries) == set(PLATFORMS), f"Registry image lacks both platforms: {image}")
        for platform, expected in images[image].items():
            require(entries[platform] == expected["digest"], f"Published platform digest differs from receipt: {image}/{platform}")
            pinned = f"{ref}@{expected['digest']}"
            manifest = json.loads(command(["docker", "buildx", "imagetools", "inspect", pinned, "--raw"]))
            require(manifest.get("config", {}).get("digest") == expected["config_digest"], "Image config differs from trusted file receipt")
            config = json.loads(command(["docker", "buildx", "imagetools", "inspect", pinned, "--format", "{{json .Image}}"]))
            require(config.get("os", "") + "/" + config.get("architecture", "") == platform, "Image config platform mismatch")
            labels = config.get("config", {}).get("Labels", {})
            require(labels.get("org.opencontainers.image.version") == version and
                    labels.get("org.opencontainers.image.source") == f"https://github.com/{repo}", "Image version/source label mismatch")
            if labels.get("org.opencontainers.image.revision"):
                require(labels["org.opencontainers.image.revision"] == commit, "Image revision label mismatch")
            if image != "tico-updater":
                require(f"TICO_VERSION={version}" in config.get("config", {}).get("Env", []), "Runtime version differs from image label")


def no_automatic_builds(source, repo):
    workflows = gh(repo, "actions/workflows?per_page=100")["workflows"]
    for name in BUILD_WORKFLOWS:
        definition = yaml.safe_load((source / ".github/workflows" / name).read_text())
        events = definition.get("on", definition.get(True, {}))
        auto = events == "push" if isinstance(events, str) else "push" in events
        if auto and isinstance(events, dict) and isinstance(events["push"], dict):
            push = events["push"]
            auto = "tags" in push or "tags-ignore" in push or not ("branches" in push or "branches-ignore" in push)
        if auto:
            matches = [w for w in workflows if w["path"] == f".github/workflows/{name}"]
            require(len(matches) == 1 and matches[0]["state"].startswith("disabled_"),
                    f"Owner must disable {name} before publishing this older source tag and restore its prior state afterwards")


def desktop(source, previous, version, repo, directory, *, download=False):
    require(VERSION.fullmatch(previous) and previous != version, "Invalid previous desktop release")
    command(["git", "-C", str(source), "rev-parse", "--verify", previous + "^{commit}"])
    require(not command(["git", "-C", str(source), "diff", "--name-only", previous, "HEAD", "--", *DESKTOP_INPUTS]).strip(),
            "Desktop inputs changed; use explicitly built/signed desktop artifacts through the manual fallback")
    release = gh(repo, "releases/tags/" + previous)
    require(not release.get("draft") and release.get("tag_name") == previous, "Previous desktop release is not published")
    assets = {x['name']: x for x in release['assets']}
    require("latest.json" in assets and DIGEST.fullmatch(assets["latest.json"].get("digest") or ""), "Previous desktop manifest lacks a checksum")
    raw = command(["gh", "release", "download", previous, "--repo", repo, "--pattern", "latest.json", "--output", "-"])
    require("sha256:" + sha256(raw.encode()) == assets["latest.json"]["digest"], "Previous desktop manifest checksum mismatch")
    original = json.loads(raw)
    path = directory / "latest.json"
    value = json.loads(raw) if download else json.loads(path.read_text())
    require(value.get("app_kind") == "generic" and set(value.get("platforms", {})) ==
            {"darwin-aarch64", "darwin-x86_64", "windows-x86_64", "linux-x86_64"} and
            set(value.get("installers", {})) == {"mac", "windows", "linux", "linux_deb"}, "Incomplete generic desktop manifest")
    expected_base = f"https://github.com/{repo}/releases/download/{previous}/"
    target_base = f"https://github.com/{repo}/releases/download/{version}/"
    required = set()
    for entry in [*value["platforms"].values(), *value["installers"].values()]:
        url = entry.get("url", "")
        require(url.startswith(expected_base) if download else url.startswith(target_base), "Desktop URL is outside its release")
        name = unquote(urlsplit(url).path.rsplit('/', 1)[-1])
        require(name not in ("", ".", "..") and re.fullmatch(r"[A-Za-z0-9_. +()-]+", name) and name in assets,
                "Unsafe or missing desktop asset")
        required.add(name)
        if "signature" in entry:
            require(bool(entry["signature"]), "Unsigned desktop updater asset")
            required.add(name + ".sig")
    for name in sorted(required):
        require(name in assets and DIGEST.fullmatch(assets[name].get("digest") or ""), "Previous desktop asset lacks a GitHub checksum")
        if download:
            command(["gh", "release", "download", previous, "--repo", repo, "--pattern", name, "--dir", str(directory)])
        p = directory / name
        require(p.is_file() and not p.is_symlink() and "sha256:" + sha256(p.read_bytes()) == assets[name]["digest"], "Desktop asset changed from previous release")
    for entry in value["platforms"].values():
        require(bool(entry.get("signature")), "Unsigned desktop updater asset")
        name = unquote(urlsplit(entry["url"]).path.rsplit('/', 1)[-1])
        require((directory / (name + '.sig')).read_text().strip() == entry['signature'], "Desktop signature file differs from manifest")
    if download:
        for entry in [*value["platforms"].values(), *value["installers"].values()]:
            entry["url"] = target_base + entry["url"][len(expected_base):]
        path.write_text(json.dumps(value, indent=2) + '\n')
    else:
        for entry in [*original["platforms"].values(), *original["installers"].values()]:
            require(entry.get("url", "").startswith(expected_base), "Previous desktop URL is outside its release")
            entry["url"] = target_base + entry["url"][len(expected_base):]
        require(value == original, "Reused desktop manifest changed beyond its release URLs")
    return required | {"latest.json"}


def prepare(args):
    source, out = args.source.resolve(), args.artifacts.resolve()
    require(out != source and source not in out.parents, "Keep artifacts outside the approved source checkout")
    notes = check_source(source, args.version, args.commit, args.repository)
    receipt = json.loads(args.receipt.read_text())
    images = image_receipt(receipt, source, args.version, args.commit, args.repository)
    gate_hash = gate(args.gate_log, args.commit)
    require(not out.exists() or not any(out.iterdir()), "Artifact output must be empty")
    out.mkdir(parents=True, exist_ok=True)
    for name in [*(f"tico-runner-{x}.gz" for x in RUNNERS), "tico-runner-SHA256SUMS"]:
        shutil.copyfile(args.runner_dir / name, out / name)
    runners(out, source_id(source))
    command([sys.executable, "-B", str(source / "scripts/build_install_bundle.py"), "--source", str(source),
             "--version", args.version, "--output", str(out)])
    (out / "notes.md").write_text(notes)
    shutil.copyfile(args.gate_log, out / 'gate.log')
    desktop_files = desktop(source, args.desktop_from, args.version, args.repository, out, download=True)
    names = {"install.sh", "install-wsl.ps1", f"tico-bundle-{args.version}.tar.gz", "SHA256SUMS",
             "tico-runner-SHA256SUMS", "notes.md", *desktop_files, *(f"tico-runner-{x}.gz" for x in RUNNERS)}
    public = {"schema_version": 1, "version": args.version, "commit": args.commit, "repository": args.repository,
              "gate_log_sha256": gate_hash, "images": images, "runner_build_id": source_id(source),
              "desktop_from": args.desktop_from, "assets": {n: sha256((out / n).read_bytes()) for n in sorted(names)}}
    (out / 'prebuilt-release.json').write_text(json.dumps(public, indent=2) + '\n')
    return public


def validate(source, directory):
    value = json.loads((directory / 'prebuilt-release.json').read_text())
    require(set(value) == {"schema_version", "version", "commit", "repository", "gate_log_sha256", "images",
                           "runner_build_id", "desktop_from", "assets"}, "Unknown or missing public manifest fields")
    require(value.get('schema_version') == 1, "Unsupported release manifest")
    version, commit, repo = (value[x] for x in ['version', 'commit', 'repository'])
    notes = check_source(source, version, commit, repo)
    images = image_receipt(value, source, version, commit, repo)
    require(value["images"] == images, "Unknown public image receipt fields")
    require(gate(directory / 'gate.log', commit) == value['gate_log_sha256'], "Gate log changed")
    require(value['runner_build_id'] == source_id(source), "Wrong runner source identity")
    names = {"install.sh", "install-wsl.ps1", f"tico-bundle-{version}.tar.gz"}
    checksums(directory / 'SHA256SUMS', names)
    helper = module(source, "scripts/build_install_bundle.py")
    require((directory / f'tico-bundle-{version}.tar.gz').read_bytes() == helper['build_bundle'](source, version), "Installer bundle differs from source")
    for name, relative in [('install.sh', 'scripts/install.sh'), ('install-wsl.ps1', 'infra/windows/install-wsl.ps1')]:
        require((directory / name).read_text() == helper['bake']((source / relative).read_text(), version, name), "Installer version/source mismatch")
    require((directory / 'notes.md').read_text() == notes, "Release notes differ from source")
    runners(directory, value['runner_build_id'])
    desktop_files = desktop(source, value['desktop_from'], version, repo, directory)
    required = names | desktop_files | {'SHA256SUMS', 'notes.md', 'tico-runner-SHA256SUMS'} | {f'tico-runner-{x}.gz' for x in RUNNERS}
    require(set(value['assets']) == required, "Manifest omits or adds release assets")
    for name, digest in value['assets'].items():
        p = directory / name
        require(p.is_file() and not p.is_symlink() and sha256(p.read_bytes()) == digest, f"Artifact changed: {name}")
    registry(images, version, commit, repo)
    no_automatic_builds(source, repo)
    return value


def tag_commit(repo, version):
    ref = gh(repo, 'git/ref/tags/' + version, missing=True)
    if ref is None:
        return None
    obj = ref['object']
    for _ in range(4):
        if obj['type'] != 'tag':
            break
        obj = gh(repo, 'git/tags/' + obj['sha'])['object']
    require(obj['type'] == 'commit' and SHA.fullmatch(obj['sha']), "Invalid release tag")
    return obj['sha']


def publish(source, directory):
    value = validate(source, directory)
    repo, version, commit = (value[x] for x in ['repository', 'version', 'commit'])
    require(gh(repo, 'releases/tags/' + version, missing=True) is None, "Release already exists; inspect it before any explicit repair")
    existing = tag_commit(repo, version)
    if existing is not None:
        require(existing == commit, "Existing tag points to different source")
    else:
        command(['gh', 'api', '--method', 'POST', f'repos/{repo}/git/refs', '-f', f'ref=refs/tags/{version}', '-f', f'sha={commit}'])
    files = [str(directory / n) for n in sorted(value['assets']) if n not in ['latest.json', 'notes.md']]
    files.append(str(directory / 'prebuilt-release.json'))
    flags = ['--prerelease'] if '-' in version else []
    command(['gh', 'release', 'create', version, '--repo', repo, '--draft', '--verify-tag', '--title', version,
             '--notes-file', str(directory / 'notes.md'), *flags, *files])
    command(['gh', 'release', 'upload', version, '--repo', repo, str(directory / 'latest.json')])
    release = gh(repo, 'releases/tags/' + version)
    require(release.get('draft') is True and release.get('tag_name') == version and
            release.get('body') == (directory / 'notes.md').read_text(), "Uploaded draft metadata/notes differ; draft retained")
    expected = {n: 'sha256:' + h for n, h in value['assets'].items() if n != 'notes.md'}
    expected['prebuilt-release.json'] = 'sha256:' + sha256((directory / 'prebuilt-release.json').read_bytes())
    require({a['name']: a.get('digest') for a in release['assets']} == expected, "Uploaded draft assets/checksums differ; draft retained")
    registry(value['images'], version, commit, repo)
    require(tag_commit(repo, version) == commit, "Tag moved during upload; draft retained")
    command(['gh', 'release', 'edit', version, '--repo', repo, '--draft=false'])
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'validate', 'publish'])
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--artifacts', required=True, type=Path)
    parser.add_argument('--version')
    parser.add_argument('--commit')
    parser.add_argument('--repository', default='ticoteam/tico')
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--gate-log', type=Path)
    parser.add_argument('--runner-dir', type=Path)
    parser.add_argument('--desktop-from')
    args = parser.parse_args(argv)
    try:
        if args.action == 'prepare':
            require(all(getattr(args, x) for x in ['version', 'commit', 'receipt', 'gate_log', 'runner_dir', 'desktop_from']), 'prepare needs version, commit, receipt, gate-log, runner-dir and desktop-from')
            result = prepare(args)
        else:
            result = (publish if args.action == 'publish' else validate)(args.source.resolve(), args.artifacts.resolve())
        print(json.dumps({'action': args.action, 'version': result['version'], 'commit': result['commit'], 'assets': len(result['assets'])}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError, yaml.YAMLError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
