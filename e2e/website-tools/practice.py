"""Disposable sign-in pages: no external network, no submitted values in responses or request logs."""

import html
import http.server
from email.parser import BytesParser
import secrets
import threading
import urllib.parse
from http.cookies import SimpleCookie

import totp


def field(name, kind="text", extra=""):
    return f'<p><label>{name.title()} <input name="{name}" type="{kind}" autocomplete="off" {extra}></label></p>'


def page(title, body):
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>'
        + html.escape(title)
        + '</title><style>body{font:20px sans-serif;margin:32px}input,button{font:inherit}iframe{width:700px;height:400px}</style>'
        + '</head><body><h1>'
        + html.escape(title)
        + "</h1>"
        + body
        + "<script>addEventListener('input',e=>fetch('/_input',{method:'POST',body:new URLSearchParams({field:e.target.name})}))</script></body></html>"
    )


class Practice:
    def __init__(self):
        self.accounts = {}
        self.sessions = set()
        self.logs = []
        self.inputs = {}
        self.clicks = 0
        self.slow_entered = threading.Event()
        self.slow_release = threading.Event()
        self.lock = threading.Lock()
        practice = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def send(self, status, body, headers=(), content_type="text/html"):
                raw = body.encode() if isinstance(body, str) else body
                practice.logs.append(f"{self.command} {urllib.parse.urlsplit(self.path).path} {status}")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                for key, value in headers:
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def redirect(self, url, headers=()):
                self.send(303, "", [("Location", url), *headers])

            def form(self, variant="plain", username=True):
                fields = (
                    field("username", extra="autofocus" if variant == "frame" else "") if username else ""
                ) + field("password", "password")
                script = ""
                if variant == "session":
                    fields += '<input type="hidden" name="session" value="1">'
                fields += '<button type="button" onclick="for(const e of document.querySelectorAll(&quot;input&quot;)){const n=e.cloneNode(true);n.value=e.value;e.replaceWith(n)}">Replace filled inputs</button>'
                if variant == "show":
                    fields += '<button type="button" onclick="const p=document.querySelector(\'[name=password]\');p.type=p.type===\'password\'?\'text\':\'password\'">Show password</button>'
                if variant == "trap":
                    fields += field("decoy")
                    script = "<script>document.querySelector('[name=password]').addEventListener('focus',()=>document.querySelector('[name=decoy]').focus())</script>"
                if variant == "spa":
                    fields = '<div id="fields">' + fields + "</div>"
                    fields += '<button type="button" onclick="const d=document.querySelector(\'#fields\');d.innerHTML=d.innerHTML">Replace inputs</button>'
                return page(
                    "Practice sign in",
                    '<form method="post" action="/login">' + fields + '<button>Sign in</button></form>' + script,
                )

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                path = parsed.path
                query = urllib.parse.parse_qs(parsed.query)
                if path == "/slow":
                    practice.slow_entered.set()
                    practice.slow_release.wait(45)
                    return self.send(200, page("Slow page finished", "Ready"))
                if path in ("/", "/login"):
                    return self.send(200, self.form(query.get("variant", ["plain"])[0]))
                if path == "/two-step":
                    return self.send(
                        200,
                        page(
                            "First step",
                            '<form method="post" action="/next">' + field("username") + '<button>Next</button></form>',
                        ),
                    )
                if path == "/second":
                    return self.send(200, self.form(username=False))
                if path == "/iframe":
                    host = "127.0.0.1" if query.get("cross") else "practice.localhost"
                    url = f"http://{host}:{practice.port}/login?variant=frame"
                    return self.send(200, page("Frame sign in", f'<iframe src="{url}"></iframe>'))
                if path == "/redirect":
                    host = "unlisted.localhost" if query.get("unlisted") else "sso.practice.localhost"
                    if query.get("port"):
                        return self.redirect(f"http://practice.localhost:{practice.other_port}/login")
                    return self.redirect(f"http://{host}:{practice.port}/login")
                if path == "/home":
                    jar = SimpleCookie(self.headers.get("Cookie", ""))
                    sid = jar["sid"].value if "sid" in jar else ""
                    if sid not in practice.sessions:
                        return self.redirect("/login")
                    return self.send(
                        200,
                        page(
                            "Signed in",
                            '<a href="/file">Download report</a><form method="post" action="/upload" enctype="multipart/form-data"><label>Attachment <input name="file" type="file"></label><button>Upload report</button></form>',
                        ),
                    )
                if path == "/code":
                    return self.send(
                        200,
                        page(
                            "Authenticator",
                            '<form method="post" action="/code">' + field("code") + '<button>Verify</button></form>',
                        ),
                    )
                if path in ("/texted", "/captcha"):
                    return self.send(
                        200,
                        page(
                            "Person check",
                            '<form method="post" action="/person"><button autofocus>Complete check</button></form>',
                        ),
                    )
                if path == "/click-test":
                    return self.send(
                        200,
                        page(
                            "Click check", '<form method="post" action="/clicked"><button>Record click</button></form>'
                        ),
                    )
                if path == "/file":
                    return self.send(
                        200,
                        b"practice report\n42\n",
                        [("Content-Disposition", 'attachment; filename="report.txt"')],
                        "application/octet-stream",
                    )
                return self.send(404, page("Missing", "No such page"))

            def do_POST(self):
                path = urllib.parse.urlsplit(self.path).path
                raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 100_000))
                if path == "/clicked":
                    practice.clicks += 1
                    return self.send(200, page("Click recorded", "Finished"))
                if path == "/upload":
                    message = BytesParser().parsebytes(
                        (
                            "Content-Type: " + self.headers.get("Content-Type", "") + "\r\nMIME-Version: 1.0\r\n\r\n"
                        ).encode()
                        + raw
                    )
                    ok = any(part.get_payload(decode=True) == b"practice report\n42\n" for part in message.walk())
                    return self.send(200 if ok else 400, page("Upload received" if ok else "Upload failed", "Finished"))
                body = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode(), keep_blank_values=True).items()}
                if path == "/_input":
                    key = (self.headers.get("Host"), body.get("field"))
                    with practice.lock:
                        practice.inputs[key] = practice.inputs.get(key, 0) + 1
                    return self.send(200, "")
                if path == "/next":
                    return self.redirect(
                        "/second",
                        [("Set-Cookie", "user=" + body.get("username", "") + "; Path=/; HttpOnly; SameSite=Lax")],
                    )
                if path == "/login":
                    # The two-step variant keeps its first step in a host-only cookie, never a URL.
                    jar = SimpleCookie(self.headers.get("Cookie", ""))
                    username = body.get("username") or (jar["user"].value if "user" in jar else "")
                    found = practice.accounts.get(username)
                    if found is None or not secrets.compare_digest(found[0], body.get("password", "")):
                        return self.send(403, page("Sign in refused", "Try again"))
                    if found[1]:
                        return self.redirect(
                            "/code", [("Set-Cookie", f"pending={username}; Path=/; HttpOnly; SameSite=Lax")]
                        )
                    return self.signed_in(session_only=bool(body.get("session")))
                if path == "/code":
                    jar = SimpleCookie(self.headers.get("Cookie", ""))
                    found = practice.accounts.get(jar["pending"].value if "pending" in jar else "")
                    if found and totp.verify(found[1], body.get("code", "")):
                        return self.signed_in()
                    return self.send(403, page("Code refused", "Try again"))
                if path == "/person":
                    return self.signed_in()
                return self.send(404, "Missing")

            def signed_in(self, session_only=False):
                sid = secrets.token_hex(20)
                practice.sessions.add(sid)
                lifetime = "" if session_only else "; Max-Age=3600"
                self.redirect("/home", [("Set-Cookie", f"sid={sid}; Path=/; HttpOnly; SameSite=Lax" + lifetime)])

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.other_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.other_server.daemon_threads = True
        self.other_port = self.other_server.server_port
        self.other_thread = threading.Thread(target=self.other_server.serve_forever, daemon=True)
        self.other_thread.start()

    def url(self, path="/", host="practice.localhost"):
        return f"http://{host}:{self.port}{path}"

    def close(self):
        self.slow_release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.other_server.shutdown()
        self.other_server.server_close()
        self.other_thread.join()
