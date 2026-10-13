"""`hub doc fetch <url>`: read one public link for the Librarian (docs/librarian.md). Runs on the
computer that runs the bot, never on the Tico server, and never with more reach than a stranger has.

What it enforces, on every hop of every request:

* http and https only, on the ordinary web ports, and no `user:password@` in the address;
* the name is resolved here and every address it resolves to must be public: loopback, private,
  link-local (the cloud metadata address 169.254.169.254 among them), shared, multicast, reserved and
  unspecified addresses are refused, and so are their IPv6 counterparts and an IPv4 address wrapped in
  IPv6. The connection then goes to the address that was checked, so the name cannot be re-resolved to
  something else between the check and the connection (DNS rebinding);
* at most 5 redirects, each one checked as if it were the first request;
* 5 MB, 20 seconds for the whole fetch, and only text, markdown, HTML, JSON, XML (a sitemap) and PDF;
* credentials only where they belong: the GitHub token goes to api.github.com and nowhere else, a Google
  token to googleapis.com and docs.google.com and nowhere else. Every hop builds its own headers from
  its own host, so a redirect can never carry one along.

A public Google Doc is read through its export link, a public Drive folder through its embedded
listing, a GitHub repository through the GitHub API (README and file tree), and a sitemap.xml is a list
of addresses. Anything else that is HTML becomes readable text with its links kept. Pure standard
library, so the tests run with the network and DNS replaced.
"""

import base64
import html
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

MAX_REDIRECTS = 5
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_S = 20
DEFAULT_MAX_CHARS = 30_000
MAX_LINKS = 200
PORTS = {80, 443, 8080, 8443}
TEXT_TYPES = {"text/html", "application/xhtml+xml", "text/plain", "text/markdown", "text/x-markdown",
              "application/json", "text/csv", "application/xml", "text/xml", "application/pdf"}
USER_AGENT = "TicoLibrarian/1.0 (+https://github.com/ticoteam/tico)"
GITHUB_API = "api.github.com"
GOOGLE_HOSTS = ("googleapis.com", "docs.google.com")


class FetchError(Exception):
    """Something the caller should be told in a sentence: code is stable, message is for reading."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


# ----------------------------------------------------------------------------- what may be reached
def public_address(value):
    """True for an address anyone on the internet may be sent to, False for everything else."""
    try:
        ip = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return False
    if ip.version == 6:
        if ip.ipv4_mapped:
            return public_address(str(ip.ipv4_mapped))
        if ip.sixtofour:
            return public_address(str(ip.sixtofour))
        if ip.teredo or ip in ipaddress.ip_network("64:ff9b::/96"):
            return False                          # tunnels that can wrap any IPv4 address, private ones too
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified or not ip.is_global)


def resolve(host, port):
    """Every address `host` resolves to. Overridden in tests."""
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except socket.gaierror as exc:
        raise FetchError("dns", f"{host} does not resolve ({exc.strerror or exc})") from None


def connect(ip, port, tls, host, timeout):
    """A connected socket to the checked address; TLS verifies the certificate against the name."""
    sock = socket.create_connection((ip, port), timeout=timeout)
    if not tls:
        return sock
    try:
        return ssl.create_default_context().wrap_socket(sock, server_hostname=host)
    except Exception:
        sock.close()
        raise


class _Connection(http.client.HTTPConnection):
    def __init__(self, host, port, ip, tls, timeout, opener):
        super().__init__(host, port, timeout=timeout)
        self._ip, self._tls, self._opener = ip, tls, opener

    def connect(self):
        self.sock = self._opener(self._ip, self.port, self._tls, self.host, self.timeout)


def credential_headers(host, env):
    """The Authorization header this host may have, if any. The token is looked up per host, so it can
    only ever be sent where it belongs."""
    host = (host or "").lower()
    if host == GITHUB_API:
        token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
        if token:
            return {"Authorization": "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28"}
    if any(host == h or host.endswith("." + h) for h in GOOGLE_HOSTS):
        token = env.get("GOOGLE_ACCESS_TOKEN")
        if token:
            return {"Authorization": "Bearer " + token}
    return {}


class Fetcher:
    """One fetch: a shared 20 second deadline over every request it makes."""

    def __init__(self, env=None, resolver=resolve, opener=connect, clock=time.monotonic):
        self.env = os.environ if env is None else env
        self.resolver, self.opener, self.clock = resolver, opener, clock
        self.deadline = clock() + TIMEOUT_S
        self.requests = 0

    def left(self):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise FetchError("timeout", f"Took longer than {TIMEOUT_S} seconds")
        return remaining

    def check_url(self, url):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise FetchError("scheme", "Only http and https addresses are fetched")
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise FetchError("userinfo", "An address with a user name or password in it is not fetched")
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            raise FetchError("url", "That is not a web address")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in PORTS:
            raise FetchError("port", f"Port {port} is not fetched")
        return parts, host, port

    def address(self, host, port):
        """A public address for the host, or a refusal. Every address it resolves to must be public."""
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
            found = [str(literal)]
        except ValueError:
            found = self.resolver(host, port)
        if not found:
            raise FetchError("dns", f"{host} does not resolve")
        bad = [ip for ip in found if not public_address(ip)]
        if bad:
            raise FetchError("private_address", f"{host} points to a private or internal address; only public "
                                                "web addresses are fetched")
        return found[0]

    def get(self, url, accept="*/*", extra=None):
        """(final_url, content_type, body bytes, truncated) after following at most 5 redirects."""
        for hop in range(MAX_REDIRECTS + 1):
            parts, host, port = self.check_url(url)
            ip = self.address(host, port)
            tls = parts.scheme == "https"
            headers = {"Host": parts.netloc.rsplit("@", 1)[-1], "User-Agent": USER_AGENT, "Accept": accept,
                       "Accept-Encoding": "identity", "Connection": "close",
                       **credential_headers(host, self.env), **(extra or {})}
            target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            conn = _Connection(host, port, ip, tls, min(self.left(), TIMEOUT_S), self.opener)
            try:
                self.requests += 1
                try:
                    conn.request("GET", target, headers=headers)
                    response = conn.getresponse()
                except (OSError, http.client.HTTPException) as exc:
                    raise FetchError("network", f"Could not read {host}: {type(exc).__name__}") from None
                if response.status in (301, 302, 303, 307, 308):
                    where = response.getheader("Location")
                    if not where:
                        raise FetchError("redirect", "A redirect with no destination")
                    url = urljoin(url, where)
                    response.read(0)
                    continue
                if response.status >= 400:
                    raise FetchError("http_" + str(response.status),
                                     f"{host} answered {response.status} {response.reason or ''}".strip())
                ctype = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
                if ctype not in TEXT_TYPES and not ctype.endswith("+json"):
                    raise FetchError("content_type", f"{ctype or 'An unknown type'} is not fetched; only text, "
                                                     "markdown, HTML, JSON, XML and PDF")
                body, truncated = self.read(response)
                return url, ctype, body, truncated
            finally:
                conn.close()
        raise FetchError("redirects", f"More than {MAX_REDIRECTS} redirects")

    def read(self, response):
        chunks, size = [], 0
        while size <= MAX_BYTES:
            self.left()
            chunk = response.read(min(65536, MAX_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        body = b"".join(chunks)
        if len(body) > MAX_BYTES:
            return body[:MAX_BYTES], True
        return body, False


# ----------------------------------------------------------------------------- turning it into text
class _Text(HTMLParser):
    """HTML to readable text: headings, lists, links kept as [text](url), code and tables plain."""
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "head", "nav", "footer", "form"}
    BLOCK = {"p", "div", "section", "article", "main", "header", "br", "tr", "ul", "ol", "table", "blockquote",
             "pre", "figure", "dl", "dt", "dd", "details", "summary"}

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.links, self.title = base, [], [], ""
        self.skip = self.pre = 0
        self.href, self.text, self.in_title, self.inside_head = None, [], False, False

    def add(self, text):
        (self.text if self.href is not None else self.out).append(text)

    def newline(self, count=1):
        self.out.append("\n" * count)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "head":
            self.inside_head = True
        if tag in self.SKIP and tag != "head":
            self.skip += 1
        if self.skip or self.inside_head:
            return
        if tag == "pre":
            self.pre += 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.newline(2)
            self.add("#" * int(tag[1]) + " ")
        elif tag == "li":
            self.newline()
            self.add("- ")
        elif tag in ("td", "th"):
            self.add(" | ")
        elif tag == "a" and attrs.get("href"):
            target = urljoin(self.base, html.unescape(attrs["href"]).strip())
            if urlsplit(target).scheme in ("http", "https"):
                self.href, self.text = target.split("#")[0] or target, []
        elif tag in self.BLOCK:
            self.newline()

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "head":
            self.inside_head = False
        if tag in self.SKIP and tag != "head":
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or self.inside_head:
            return
        if tag == "pre":
            self.pre = max(0, self.pre - 1)
        if tag == "a" and self.href is not None:
            label = re.sub(r"\s+", " ", "".join(self.text)).strip()
            self.links.append({"text": label, "url": self.href})
            self.out.append(f"[{label}]({self.href})" if label else self.href)
            self.href = None
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "pre", "blockquote"):
            self.newline()

    def handle_data(self, data):
        if self.in_title:
            self.title += data
            return
        if not self.skip and not self.inside_head:
            self.add(data if self.pre else re.sub(r"\s+", " ", data))


def html_to_text(source, base):
    parser = _Text(base)
    parser.feed(source)
    parser.close()
    text = "".join(parser.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    links, seen = [], set()
    for link in parser.links:
        if link["url"] not in seen:
            seen.add(link["url"])
            links.append(link)
    return re.sub(r"\s+", " ", parser.title).strip(), text, links


def sitemap_urls(source):
    """The addresses in a sitemap or a sitemap index. Read with a pattern, never an XML parser, so an
    entity in a hostile file has nothing to expand."""
    return [html.unescape(u).strip() for u in re.findall(r"<loc>\s*(.*?)\s*</loc>", source, re.S)]


def pdf_to_text(data):
    try:
        import pypdf
    except ImportError:
        raise FetchError("pdf", "This computer cannot read a PDF (pypdf is not installed); ask for the "
                                "document as a web page or a Google Doc") from None
    import io
    try:
        return "\n\n".join((page.extract_text() or "") for page in pypdf.PdfReader(io.BytesIO(data)).pages)
    except Exception as exc:
        raise FetchError("pdf", f"That PDF could not be read ({type(exc).__name__})") from None


def decode(body, ctype, response_charset=None):
    for charset in (response_charset, "utf-8"):
        if charset:
            try:
                return body.decode(charset)
            except (UnicodeDecodeError, LookupError):
                pass
    return body.decode("utf-8", "replace")


# ----------------------------------------------------------------------------- the fetch
GOOGLE_DOC = re.compile(r"^https://docs\.google\.com/document/d/([\w-]+)")
GOOGLE_FOLDER = re.compile(r"^https://drive\.google\.com/drive/(?:u/\d+/)?folders/([\w-]+)")
GITHUB_REPO = re.compile(r"^https://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/(tree|blob)/([^/]+)/?(.*))?/?$")


def fetch(url, max_chars=DEFAULT_MAX_CHARS, env=None, resolver=resolve, opener=connect, clock=time.monotonic):
    """{url, final_url, title, text, links, truncated}. Raises FetchError."""
    url = str(url or "").strip()
    max_chars = max(500, min(int(max_chars or DEFAULT_MAX_CHARS), 200_000))
    fetcher = Fetcher(env, resolver, opener, clock)
    fetcher.check_url(url)
    doc = GOOGLE_DOC.match(url)
    folder = GOOGLE_FOLDER.match(url)
    repo = GITHUB_REPO.match(url)
    if doc:
        result = _google_doc(fetcher, url, doc.group(1))
    elif folder:
        result = _google_folder(fetcher, url, folder.group(1))
    elif repo and not urlsplit(url).path.lower().startswith(("/orgs/", "/settings", "/marketplace")):
        result = _github(fetcher, url, *repo.groups())
    else:
        result = _page(fetcher, url)
    text = result["text"]
    truncated = result.get("truncated", False) or len(text) > max_chars
    return {"url": url, "final_url": result["final_url"], "title": result["title"], "text": text[:max_chars],
            "links": result["links"][:MAX_LINKS], "truncated": truncated,
            **({"note": result["note"]} if result.get("note") else {})}


def _charset(ctype_header):
    match = re.search(r"charset=([\w-]+)", ctype_header or "", re.I)
    return match.group(1) if match else None


def _page(fetcher, url, accept="text/html,text/markdown,text/plain,application/json,application/xml;q=0.8,*/*;q=0.5"):
    final, ctype, body, truncated = fetcher.get(url, accept)
    return _convert(final, ctype, body, truncated)


def _convert(final, ctype, body, truncated):
    if ctype == "application/pdf":
        if truncated:
            raise FetchError("too_large", "That PDF is larger than 5 MB")
        return {"final_url": final, "title": "", "text": pdf_to_text(body), "links": []}
    source = decode(body, ctype)
    path = urlsplit(final).path.lower()
    if ctype in ("application/xml", "text/xml") or path.endswith(("sitemap.xml", "sitemap_index.xml")):
        urls = sitemap_urls(source)
        if urls:
            return {"final_url": final, "title": "sitemap", "truncated": truncated,
                    "text": "\n".join(urls), "links": [{"text": "", "url": u} for u in urls]}
    if ctype in ("text/html", "application/xhtml+xml"):
        title, text, links = html_to_text(source, final)
        return {"final_url": final, "title": title, "text": text, "links": links, "truncated": truncated}
    if ctype == "application/json" or ctype.endswith("+json"):
        try:
            source = json.dumps(json.loads(source), indent=2)
        except ValueError:
            pass
    links = [{"text": t, "url": u} for t, u in re.findall(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", source)]
    heading = re.match(r"\s*#\s+(.+)", source)
    return {"final_url": final, "title": heading.group(1).strip() if heading else "", "text": source,
            "links": links, "truncated": truncated}


def _google_doc(fetcher, url, doc_id):
    export = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
    try:
        final, ctype, body, truncated = fetcher.get(export, "text/plain")
    except FetchError as exc:
        if exc.code in ("http_401", "http_403", "http_404"):
            raise FetchError("not_public", "That Google Doc is not shared with anyone who has the link, so it "
                                           "cannot be read here. Ask its owner to share it that way, or paste it "
                                           "into an internal doc") from None
        raise
    if urlsplit(final).hostname == "accounts.google.com" or ctype != "text/plain":
        raise FetchError("not_public", "That Google Doc is not shared with anyone who has the link, so it "
                                       "cannot be read here. Ask its owner to share it that way, or paste it "
                                       "into an internal doc")
    text = decode(body, ctype).lstrip("﻿")
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return {"final_url": url, "title": first[:120], "text": text, "links": [], "truncated": truncated,
            "note": "Read as a public Google Doc (plain-text export)"}


def _google_folder(fetcher, url, folder_id):
    listing = f"https://drive.google.com/embeddedfolderview?id={folder_id}"
    final, ctype, body, truncated = fetcher.get(listing, "text/html")
    if urlsplit(final).hostname == "accounts.google.com":
        raise FetchError("not_public", "That Drive folder is not shared with anyone who has the link, so it "
                                       "cannot be listed here")
    title, text, links = html_to_text(decode(body, ctype), final)
    files = [link for link in links if "drive.google.com" in link["url"] or "docs.google.com" in link["url"]]
    if not files:
        raise FetchError("not_public", "That Drive folder could not be listed without signing in (it is not "
                                       "public, or it is empty)")
    return {"final_url": url, "title": title or "Google Drive folder", "truncated": truncated,
            "text": "\n".join(f"- [{f['text'] or f['url']}]({f['url']})" for f in files), "links": files,
            "note": "A public Drive folder's listing; open each link to read it"}


def _github(fetcher, url, owner, name, kind, ref, path):
    base = f"https://{GITHUB_API}/repos/{owner}/{name}"
    api = {"Accept": "application/vnd.github+json"}

    def call(endpoint):
        final, ctype, body, truncated = fetcher.get(base + endpoint, "application/vnd.github+json", api)
        try:
            return json.loads(decode(body, ctype))
        except ValueError:
            raise FetchError("github", "GitHub answered with something that is not JSON") from None

    def scoped_contents():
        try:
            return call(f"/contents/{quote(unquote(path))}?ref={quote(unquote(ref), safe='')}")
        except FetchError as exc:
            if exc.code != "http_404":
                raise
            from clients import github_scope
            scope = github_scope.parse_url(url)
            try:
                reference, folder = github_scope.resolve(scope, {}, call)
            except ValueError as error:
                raise FetchError("github_reference", str(error)) from None
            return call(f"/contents/{quote(folder)}?ref={quote(reference, safe='')}")

    try:
        if kind == "blob":
            data = scoped_contents()
            if isinstance(data, dict) and data.get("encoding") == "base64":
                text = base64.b64decode(data.get("content") or "").decode("utf-8", "replace")
                links = [{"text": t, "url": u} for t, u in re.findall(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", text)]
                return {"final_url": url, "title": data.get("name") or path, "text": text, "links": links}
            raise FetchError("github", "That GitHub file is too large or is not text")
        if kind == "tree":
            data = scoped_contents()
            entries = data if isinstance(data, list) else []
            return {"final_url": url, "title": f"{owner}/{name}/{path}", "links": [
                        {"text": e["path"], "url": e.get("html_url") or ""} for e in entries],
                    "text": "\n".join(f"- {e['type']}: {e['path']}" for e in entries)}
        info = call("")
        branch = info.get("default_branch") or "HEAD"
        parts = [f"# {owner}/{name}", info.get("description") or ""]
        links = []
        try:
            readme = call("/readme")
            text = base64.b64decode(readme.get("content") or "").decode("utf-8", "replace")
            links = [{"text": t, "url": u} for t, u in re.findall(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", text)]
            parts += ["", "## README", text]
        except FetchError as exc:
            if exc.code != "http_404":
                raise
            parts += ["", "(no README)"]
        tree = call(f"/git/trees/{quote(branch)}?recursive=1")
        paths = [f"{e['path']}" for e in tree.get("tree", []) if e.get("type") == "blob"]
        parts += ["", f"## Files on {branch}" + (" (list cut short by GitHub)" if tree.get("truncated") else "")]
        parts += [f"- {p}" for p in paths[:400]] + ([f"- ... and {len(paths) - 400} more"] if len(paths) > 400 else [])
        return {"final_url": url, "title": f"{owner}/{name}", "text": "\n".join(parts), "links": links,
                "note": "Read through the GitHub API" + (" with this bot's GitHub token" if credential_headers(
                    GITHUB_API, fetcher.env) else ", without a token (public repositories only)")}
    except FetchError as exc:
        if exc.code in ("http_401", "http_403", "http_404"):
            raise FetchError("github_access", f"GitHub would not show {owner}/{name}: it is private or does not "
                                              "exist, and this bot has no GitHub access to it") from None
        raise
