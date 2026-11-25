#!/usr/bin/env python3
"""Audit one of the company's public websites locally and write an HTML report."""

from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import html
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

UNLIGHTHOUSE = "unlighthouse@0.19.2"
MAX_ROUTES = 200
TIME_CAP_SECONDS = 15 * 60
CATEGORIES = ("performance", "accessibility", "best-practices", "seo")
LABELS = {
    "performance": "Performance",
    "accessibility": "Accessibility",
    "best-practices": "Best practices",
    "seo": "SEO",
}
GOOD = 0.9  # the score Lighthouse itself calls good
HOP_HEADERS = {"connection", "proxy-connection", "proxy-authorization", "keep-alive"}
REDIRECTS = {301, 302, 303, 307, 308}
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
PUBLIC_V6 = ipaddress.IPv6Network("2000::/3")
SIX_TO_FOUR = ipaddress.IPv6Network("2002::/16")


class Refused(Exception):
    """A host, address or redirect this audit will not reach."""


def public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv4-mapped addresses, local translation prefixes and 6to4 can embed a private IPv4 address.
        if ip not in PUBLIC_V6 or ip in SIX_TO_FOUR:
            return False
    return ip.is_global and not (ip.is_multicast or ip.is_reserved)


async def resolve_public(host: str, port: int) -> list[str]:
    """Every address the host resolves to. Refused when there is none, or when any one of them is not public."""
    try:
        found = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise Refused(f"{host} does not resolve") from None
    addresses = []
    for *_, sockaddr in found:
        ip = ipaddress.ip_address(sockaddr[0].split("%")[0])
        if not public_ip(ip):
            raise Refused(f"{host} resolves to {ip}, which is not a public address")
        addresses.append(str(ip))
    if not addresses:
        raise Refused(f"{host} does not resolve")
    return list(dict.fromkeys(addresses))


def dns_name(value: str) -> str:
    host = value.lower().removesuffix(".")
    labels = host.split(".")
    if (
        len(host) > 253
        or len(labels) < 2
        or not all(DNS_LABEL.fullmatch(label) for label in labels)
        or not labels[-1][0].isalpha()
        or labels[-1] in {"localhost", "local", "internal"}
    ):
        raise Refused("use a public DNS name")
    return host


def on_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # each hop is checked by check_site
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects)


def site_url(url: str, domain: str) -> str:
    try:
        parts = urlsplit(url)
        if (
            parts.scheme not in ("http", "https")
            or parts.username is not None
            or parts.port not in (None, 443 if parts.scheme == "https" else 80)
            or re.search(r"[\\\x00-\x20\x7f]", url)
        ):
            raise ValueError
        host = dns_name(parts.hostname or "")
    except ValueError:
        raise Refused("use an HTTP(S) address without credentials or a custom port") from None
    if not on_domain(host, domain):
        raise Refused(f"{host} is outside the owned domain {domain}")
    return parts._replace(netloc=host, fragment="").geturl()


def check_site(start: str, fence: Fence, deadline: float) -> str:
    """Resolve redirects through the same proxy used by the crawl."""
    url = start
    for _ in range(6):
        url = site_url(url, fence.domain)
        req = urllib.request.Request(url, headers={"User-Agent": "audit-site", "Host": urlsplit(url).netloc})
        req.set_proxy(f"127.0.0.1:{fence.port}", "http")
        try:
            with _OPENER.open(req, timeout=min(15, remaining(deadline))):
                return url
        except urllib.error.HTTPError as error:
            with error:
                if error.code in REDIRECTS and (location := error.headers.get("Location")):
                    url = urljoin(url, location)
                    continue
                raise Refused(f"the start page answered HTTP {error.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise Refused(f"the start page could not be reached: {getattr(error, 'reason', error)}") from None
    raise Refused("too many redirects")


def preflight_timeout(_signum, _frame) -> None:
    raise TimeoutError("the audit reached its 15-minute cap")


class Fence(threading.Thread):
    """Connect to a checked numeric address, so a second DNS lookup cannot change the destination."""

    def __init__(self, domain: str) -> None:
        super().__init__(daemon=True)
        self.domain = domain
        self.port = 0
        self.blocked = 0
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._done: asyncio.Event | None = None

    def run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._done = asyncio.Event()
        async with await asyncio.start_server(self._client, "127.0.0.1", 0) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await self._done.wait()

    def start_and_wait(self) -> "Fence":
        self.start()
        if not self._ready.wait(10) or not self.port:
            raise Refused("the audit proxy did not start")
        return self

    def stop(self) -> None:
        if self._loop is not None and self._done is not None:
            self._loop.call_soon_threadsafe(self._done.set)
            self.join(timeout=1)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._relay(reader, writer)
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()

    async def _relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3:
            return await self._refuse(writer, "a malformed request")
        method, target, version = parts
        if method == "CONNECT":
            host, _, port_text = target.rpartition(":")
            host, path = host.strip("[]"), None
        else:
            url = urlsplit(target)
            if url.scheme != "http" or not url.hostname:
                return await self._refuse(writer, "only http and https addresses")
            host, port_text = url.hostname, str(url.port or 80)
            path = (url.path or "/") + (f"?{url.query}" if url.query else "")
        try:
            if port_text not in ("80", "443"):
                raise Refused("only standard web ports")
            port = int(port_text)
            host = dns_name(host)
            if not on_domain(host, self.domain):
                raise Refused("outside the owned domain")
            addresses = await resolve_public(host, port)
        except Refused as error:
            self.blocked += 1
            return await self._refuse(writer, str(error))
        for address in addresses:
            try:
                up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(address, port), 5)
                break
            except (OSError, asyncio.TimeoutError):
                continue
        else:
            return await self._refuse(writer, f"{host} did not accept the connection")
        try:
            if path is None:
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
            else:
                kept = [line for line in lines[1:] if line and line.split(":", 1)[0].strip().lower() not in HOP_HEADERS]
                up_writer.write(
                    "\r\n".join([f"{method} {path} {version}", *kept, "Connection: close", "", ""]).encode("latin-1")
                )
                await up_writer.drain()
            await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
        finally:
            up_writer.close()

    async def _refuse(self, writer: asyncio.StreamWriter, reason: str) -> None:
        body = f"Refused by the audit fence: {reason}\n".encode("ascii", "replace")
        writer.write(
            b"HTTP/1.1 403 Refused by the audit fence\r\nContent-Type: text/plain\r\nConnection: close\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            + body
        )
        await writer.drain()


async def _pipe(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
    try:
        while chunk := await source.read(65536):
            target.write(chunk)
            await target.drain()
        target.write_eof()
    except OSError:
        target.close()


def write_config(run_dir: Path, fence: Fence, max_routes: int) -> Path:
    config = {
        # Sitemap fetching uses a separate HTTP client. Discover routes through the browser instead.
        "scanner": {"maxRoutes": max_routes, "dynamicSampling": False, "sitemap": False, "skipJavascript": False},
        "chrome": {"useDownloadFallback": False},
        "puppeteerClusterOptions": {"maxConcurrency": 1, "retryLimit": 0},
        "puppeteerOptions": {
            "acceptInsecureCerts": False,
            "userDataDir": str(run_dir / "chrome-profile"),
            "args": [
                f"--proxy-server=http://127.0.0.1:{fence.port}",
                "--proxy-bypass-list=<-loopback>",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                "--disable-quic",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--disable-background-networking",
                "--disable-breakpad",
            ],
        },
    }
    path = run_dir / "unlighthouse.config.mjs"
    # Node bypasses the proxy for Chrome's own debugging connection. Never let a site redirect a Node fetch there.
    path.write_text(
        "import childProcess from 'node:child_process';\n"
        "import { syncBuiltinESMExports } from 'node:module';\n"
        f"const domain = {json.dumps(fence.domain)};\n"
        "const fetchSite = globalThis.fetch;\n"
        "globalThis.fetch = async (input, options) => {\n"
        "  const url = new URL(input instanceof Request ? input.url : input);\n"
        "  const host = url.hostname.replace(/\\.$/, '');\n"
        "  if (!['https:', 'http:'].includes(url.protocol) || url.port || url.username || url.password ||\n"
        "      (host !== domain && !host.endsWith('.' + domain))) throw new Error('Outside the audit domain');\n"
        "  return fetchSite(input, { ...options, redirect: 'error' });\n"
        "};\n"
        # Keep Puppeteer's normally detached Chrome inside the process group the timeout kills.
        "const spawn = childProcess.spawn;\n"
        "childProcess.spawn = (file, args, options) => Array.isArray(args)\n"
        "  ? spawn(file, args, { ...options, detached: false })\n"
        "  : spawn(file, { ...(options ?? args), detached: false });\n"
        "syncBuiltinESMExports();\n"
        f"export default {json.dumps(config, indent=2)};\n"
    )
    return path


def remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("the audit reached its 15-minute cap")
    return seconds


def run_command(command: list[str], run_dir: Path, env: dict[str, str], deadline: float) -> int:
    with (run_dir / "scan.log").open("a") as log:
        process = subprocess.Popen(
            command, cwd=run_dir, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            return process.wait(timeout=remaining(deadline))
        except subprocess.TimeoutExpired:
            raise TimeoutError("the audit reached its 15-minute cap") from None
        finally:
            # The wrapper may have exited while Chrome or a Lighthouse worker still holds the profile open.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def run_scan(site: str, run_dir: Path, fence: Fence, max_routes: int, deadline: float) -> str:
    clean = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
    if chrome := os.environ.get("CHROME_PATH") or os.environ.get("TICO_CHROME"):
        clean["CHROME_PATH"] = chrome
    # Keep the npm cache warm without passing turn credentials or using the user's config and browser data.
    clean["npm_config_cache"] = os.environ.get("npm_config_cache", str(Path.home() / ".npm"))
    home = run_dir / "home"
    home.mkdir()
    clean["HOME"] = str(home)
    # Older versions can accept the flag while proxying fetch alone, leaving Node HTTP traffic unprotected.
    proxy_version = (
        "const [major, minor] = process.versions.node.split('.').map(Number);"
        "process.exit(major > 24 || (major === 24 && minor >= 5) ? 0 : 1);"
    )
    if run_command(["node", "--use-env-proxy", "-e", proxy_version], run_dir, clean, deadline):
        raise Refused("install Node.js 24.5 or newer with npm")
    npx = [
        "npx",
        "--yes",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "--userconfig=/dev/null",
        "--registry=https://registry.npmjs.org",
        f"--package={UNLIGHTHOUSE}",
    ]
    # Cache the pinned tool before closing network access to anything outside the site.
    if run_command([*npx, "--", "unlighthouse-ci", "--help"], run_dir, clean, deadline):
        raise Refused(f"could not prepare {UNLIGHTHOUSE} with npm")
    proxy = f"http://127.0.0.1:{fence.port}"
    env = dict(clean)
    env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, NO_PROXY="localhost,127.0.0.1,::1", NODE_OPTIONS="--use-env-proxy")
    command = [
        *npx,
        "--offline",
        "--",
        "unlighthouse-ci",
        "--site",
        site,
        "--config-file",
        str(write_config(run_dir, fence, max_routes)),
        "--output-path",
        str(run_dir / "out"),
    ]
    try:
        code = run_command(command, run_dir, env, deadline)
    except TimeoutError:
        return "Stopped at the 15-minute cap."
    return f"Unlighthouse exited with status {code}." if code else ""


def read_pages(out_dir: Path, max_routes: int, blocked_connections: int) -> tuple[list[dict], int]:
    pages = []
    reports = sorted(out_dir.rglob("lighthouse.json"))[:max_routes]
    for path in reports:
        try:
            lhr = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue  # A process stopped at the deadline may have left an incomplete report.
        if not isinstance(lhr, dict) or lhr.get("runtimeError"):
            continue
        categories = lhr.get("categories") or {}
        audits = lhr.get("audits") or {}
        scores = {}
        for key in CATEGORIES:
            score = (categories.get(key) or {}).get("score")
            if score is not None:
                scores[key] = round(score * 100)
        failing = []
        for key in CATEGORIES:
            for ref in (categories.get(key) or {}).get("auditRefs", []):
                # Chrome treats a blocked third-party resource as a console error.
                if ref.get("id") == "errors-in-console" and blocked_connections:
                    continue
                audit = audits.get(ref.get("id"), {})
                group = ref.get("group")
                actionable = group != "metrics" and (ref.get("weight") or group in {"insights", "load-opportunities"})
                if actionable and audit.get("score") is not None and audit["score"] < GOOD:
                    failing.append({"category": key, "id": ref["id"], "title": audit.get("title", ref["id"])})
        if not scores:
            continue
        url = lhr.get("finalDisplayedUrl") or lhr.get("requestedUrl") or ""
        pages.append(
            {
                "path": urlsplit(url).path or "/",
                "scores": scores,
                "mean": sum(scores.values()) / len(scores),
                "failing": failing,
            }
        )
    return pages, len(reports) - len(pages)


def top_fixes(pages: list[dict], limit: int = 8) -> list[tuple[str, str, int]]:
    count, meta = collections.Counter(), {}
    for page in pages:
        seen = set()
        for item in page["failing"]:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            count[item["id"]] += 1
            meta[item["id"]] = (item["title"], item["category"])
    return [(meta[audit_id][0], meta[audit_id][1], n) for audit_id, n in count.most_common(limit)]


def averages(pages: list[dict]) -> dict[str, int]:
    out = {}
    for key in CATEGORIES:
        values = [page["scores"][key] for page in pages if key in page["scores"]]
        if values:
            out[key] = round(sum(values) / len(values))
    return out


def render(site: str, pages: list[dict], status: str, fence: Fence, date: str) -> str:
    esc = html.escape
    host = urlsplit(site).hostname or ""
    avg = averages(pages)
    bars = "".join(f'<span>{LABELS[k]}</span><i style="width:{avg[k]}%">{avg[k]}</i>' for k in CATEGORIES if k in avg)
    under = ", ".join(
        f"{LABELS[k]} {sum(1 for p in pages if k in p['scores'] and p['scores'][k] < 90)}"
        for k in CATEGORIES
        if k in avg
    )
    worst = sorted(pages, key=lambda p: p["mean"])[:10]
    cols = "".join(f"<th class=n>{LABELS[k]}</th>" for k in CATEGORIES)
    rows = "".join(
        "<tr><td>"
        + esc(p["path"])
        + "</td>"
        + "".join(f"<td class=n>{p['scores'].get(k, '—')}</td>" for k in CATEGORIES)
        + "</tr>"
        for p in worst
    )
    fixes = (
        "".join(
            f"<li>{esc(title)}: {n} of {len(pages)} pages ({esc(LABELS[cat])})</li>"
            for title, cat, n in top_fixes(pages)
        )
        or "<li>Nothing failing on the pages scanned.</li>"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Site audit: {esc(host)}</title>
<style>
:root{{--ink:#1d1d22;--muted:#6b6b76;--surface:#fff;--line:#e3e3ea;--accent:#2455b2}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--ink:#f1f1f4;--muted:#9c9ca8;--surface:#16161a;--line:#2d2d35;--accent:#7fa8ff}}}}
:root[data-theme="dark"]{{--ink:#f1f1f4;--muted:#9c9ca8;--surface:#16161a;--line:#2d2d35;--accent:#7fa8ff}}
body{{margin:0;background:var(--surface);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:760px;margin:0 auto;padding:24px 16px}}
h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:19px;margin:28px 0 8px}}
.desc{{color:var(--muted);margin:0 0 12px}} nav.toc a{{color:var(--accent);margin-right:14px}}
.bars{{display:grid;grid-template-columns:150px 1fr;gap:8px 12px;align-items:center}}
.bars i{{display:block;min-width:2ch;height:24px;line-height:24px;border-radius:4px;background:var(--accent);color:var(--surface);font-style:normal;font-variant-numeric:tabular-nums}}
figcaption{{color:var(--muted);font-size:14px;margin-top:8px}}
.pages{{overflow-x:auto}} td:first-child{{overflow-wrap:anywhere}}
table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{text-align:left;padding:6px 4px;border-bottom:1px solid var(--line)}}
td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums}}
li{{margin:4px 0}}
@media (max-width:500px){{figure{{margin-inline:0}}.bars{{grid-template-columns:110px 1fr}}}}
</style></head>
<body><main>
<h1>Site audit: {esc(host)}</h1>
<p class="desc">This is a mobile Lighthouse audit of {len(pages)} pages on {esc(host)} from {esc(date)}.</p>
<nav class="toc"><a href="#scores">Scores</a><a href="#worst">Worst pages</a><a href="#fixes">Top fixes</a></nav>
<section id="scores"><h2>Average scores</h2>
<figure class="chart" aria-label="Average scores"><div class="bars">{bars}</div>
<figcaption>Pages under 90 in each category: {esc(under)}. {esc(status)}</figcaption></figure></section>
<section id="worst"><h2>Worst pages</h2>
<div class="pages"><table><thead><tr><th>Page</th>{cols}</tr></thead><tbody>{rows}</tbody></table></div></section>
<section id="fixes"><h2>Top fixes</h2>
<ul>{fixes}</ul></section>
<p class="desc">The crawl starts at {esc(urlsplit(site).path or '/')} and blocks third-party and non-public resources,
which can affect scores. Connections blocked: {fence.blocked}.</p>
</main></body></html>
"""


def run(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + TIME_CAP_SECONDS
    try:
        domain = dns_name(args.own_domain)
        site = site_url(args.site, domain)
        if urlsplit(site).query:
            raise Refused("use a start URL without a query string")
        out = Path(args.out)
        with tempfile.TemporaryDirectory(prefix="audit-site-", ignore_cleanup_errors=True) as tmp:
            run_dir = Path(tmp)
            fence = Fence(domain).start_and_wait()
            try:
                previous = signal.signal(signal.SIGALRM, preflight_timeout)
                try:
                    signal.setitimer(signal.ITIMER_REAL, remaining(deadline))
                    site = check_site(site, fence, deadline)
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, previous)
                status = run_scan(site, run_dir, fence, args.max_routes, deadline)
            finally:
                fence.stop()
            pages, unscored = read_pages(run_dir / "out", args.max_routes, fence.blocked)
            if not pages:
                tail = (run_dir / "scan.log").read_text(errors="replace").splitlines()[-10:]
                print(json.dumps({"ok": False, "error": status or "no routes produced scores", "log_tail": tail}))
                return 3
            out.parent.mkdir(parents=True, exist_ok=True)
            date = dt.datetime.now(tz=dt.timezone.utc).date().isoformat()
            note = f"{status} {unscored} route reports had no usable scores." if unscored else status
            out.write_text(render(site, pages, note.strip(), fence, date), encoding="utf-8")
    except (Refused, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "site": site.split("?", 1)[0],
                "pages": len(pages),
                "averages": averages(pages),
                "status": status or ("partial" if unscored else "complete"),
                "unscored_reports": unscored,
                "blocked_connections": fence.blocked,
                "report": str(out),
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit one of the company's own public sites with Lighthouse.")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("run", help="scan a site and write its report page")
    scan.add_argument("site", help="the site's address, e.g. https://www.example.com")
    scan.add_argument("--own-domain", required=True, help="the company's domain the site belongs to, e.g. example.com")
    scan.add_argument("--max-routes", type=int, default=5, help=f"pages to scan, 1 to {MAX_ROUTES} (default: 5)")
    scan.add_argument("--out", required=True, help="the report page to write, e.g. reports/2026-11-24-site-audit.html")
    args = parser.parse_args(argv)
    if not 1 <= args.max_routes <= MAX_ROUTES:
        parser.error(f"--max-routes must be from 1 to {MAX_ROUTES}")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
