"""Immutable MIME proofs and inert, complete previews of recipient-visible content."""

import base64
import hashlib
import json
import mimetypes
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser

from .store import Problem

MAX_RAW = 24 * 1024 * 1024
MAX_PREVIEW = 256 * 1024
TEXT_MARKUP = frozenset(
    "a abbr address article aside b big blockquote body br caption center cite code col colgroup "
    "dd del div dl dt em figcaption figure footer h1 h2 h3 h4 h5 h6 head header hr html i ins kbd "
    "li main mark nav ol p pre q rp rt ruby s samp section small span strong sub summary sup "
    "table tbody td tfoot th thead title tr tt u ul var wbr".split()
)
TEXT_ATTRIBUTES = frozenset(("alt", "title", "href", "src", "value", "label", "placeholder", "aria-label"))


class Refused(ValueError):
    """One named check an email failed; its text is what the sender is told (`snapshot`)."""


def kib(size):
    return f"{size / 1024:,.0f} KiB"


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else str(value).encode()).hexdigest()


class VisibleHTML(HTMLParser):
    """Keep all body text and link/image labels, including hidden text; never execute or render HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.ignored = [], 0
        self.complete = True

    def handle_starttag(self, tag, attrs):
        if (tag not in TEXT_MARKUP and tag != "script") or any(name not in TEXT_ATTRIBUTES for name, _ in attrs):
            self.complete = False
        if tag == "script":
            self.ignored += 1
        if not self.ignored:
            self.text.append("\n")
            self.text.extend(
                value
                for name, value in attrs
                if name in ("alt", "title", "href", "src", "style", "value", "label", "placeholder", "aria-label")
                and value
            )

    def handle_endtag(self, tag):
        if tag == "script" and self.ignored:
            self.ignored -= 1
        if not self.ignored:
            self.text.append("\n")

    def handle_comment(self, data):
        # Outlook can render conditional-comment bodies that ordinary HTML text extraction omits.
        if data.lstrip().lower().startswith("[if"):
            self.complete = False
            self.text.append("\n" + data + "\n")

    def unknown_decl(self, data):
        self.complete = False
        self.text.append("\n" + data + "\n")

    def handle_data(self, data):
        if not self.ignored:
            self.text.append(data)


def parsed(raw):
    try:
        data = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    except ValueError as exc:
        raise Refused("the raw email is not base64url MIME (" + str(exc)[:120] + ")") from exc
    if len(data) > MAX_RAW:
        raise Refused(f"it is {len(data) / 1024 / 1024:.1f} MB; the limit is {MAX_RAW // 1024 // 1024} MB")
    return BytesParser(policy=policy.default).parsebytes(data)


def body_parts(msg, primary=True):
    """Structure and disposition identify bodies; an inline filename never hides visible text."""
    if msg.get_content_disposition() == "attachment":
        return []
    if not msg.is_multipart():
        return [msg] if primary or msg.get_content_maintype() == "text" else []
    if msg.get_content_maintype() != "multipart":
        return [msg] if primary else []
    children = list(msg.iter_parts())
    if msg.get_content_subtype() not in ("mixed", "related", "alternative"):
        return [msg] + [part for child in children for part in body_parts(child)]
    if msg.get_content_subtype() == "alternative":
        return [part for child in children for part in body_parts(child)]
    if msg.get_content_subtype() == "related":
        start = msg.get_param("start")
        root = next((child for child in children if start and child.get("Content-ID") == start), None)
        root = root if root is not None else children[0] if children else None
        return [part for child in children for part in body_parts(child, primary=child is root)]
    return [part for index, child in enumerate(children) for part in body_parts(child, primary=index == 0)]


def file_parts(msg):
    leaves = [p for p in msg.walk() if not p.is_multipart()]
    nested = [p for p in msg.walk() if p.is_multipart()]
    return leaves + nested


def attachment_parts(msg):
    """Inline text can be both visible content and an attachment; alternatives remain bodies."""
    result = set()
    for parent in msg.walk():
        if parent.get_content_disposition() == "attachment":
            result.add(id(parent))
        if parent.is_multipart():
            result.update(id(part) for part in parent.iter_attachments())
            if parent.get_content_type() == "multipart/mixed":
                result.update(id(part) for part in list(parent.iter_parts())[1:] if part.get_filename())
    return result


def part_bytes(part):
    if part.get_content_type() == "message/rfc822" and part.is_multipart():
        return b"\n".join(message.as_bytes(policy=policy.SMTP) for message in part.get_payload())
    return part.as_bytes(policy=policy.SMTP) if part.is_multipart() else part.get_payload(decode=True) or b""


def snapshot(raw, mailbox):
    try:
        msg = parsed(raw)
        for key in ("from", "sender", "reply-to", "to", "cc", "bcc", "subject", "in-reply-to", "references"):
            if len(msg.get_all(key, [])) > 1:
                raise Refused(f"the {key.title()} header appears {len(msg.get_all(key, []))} times; an email has one")
        to = sorted(
            {
                a.lower()
                for _, a in getaddresses(msg.get_all("to", []) + msg.get_all("cc", []) + msg.get_all("bcc", []))
                if a
            }
        )
        sender = [a.strip().lower() for _, a in getaddresses(msg.get_all("from", [])) if a.strip()]
        inbox = str(mailbox or "").strip().lower()
        from .mail_settings import ADDRESS

        if sender != [inbox]:
            raise Refused(f"From {', '.join(sender) or '(none)'} doesn't match the inbox {inbox}")
        if not to:
            raise Refused("it has no recipients in To, Cc or Bcc")
        if len(to) > 100:
            raise Refused(f"it has {len(to)} recipients; the limit is 100")
        bad = [a for a in to if not ADDRESS.fullmatch(a)]
        if bad:
            raise Refused("not a valid recipient address: " + ", ".join(a[:80] for a in bad[:3]))
        bodies, files, texts, visible = body_parts(msg), [], [], []
        file_texts = set()  # visible entries that are a text file's words, not a body's: kept while they fit
        complete = all(not body.is_multipart() for body in bodies)
        leaves = file_parts(msg)
        attachments = attachment_parts(msg)
        attached_children = {
            id(child)
            for parent in leaves
            if parent.is_multipart() and id(parent) in attachments
            for child in list(parent.walk())[1:]
        }
        for index, part in enumerate(leaves):
            for key in ("content-type", "content-disposition", "content-transfer-encoding", "content-id"):
                if len(part.get_all(key, [])) > 1:
                    raise Refused(f"a part repeats its {key.title()} header")
            is_body = any(part is body for body in bodies)
            is_file = id(part) in attachments and id(part) not in attached_children
            payload = part_bytes(part) if not part.is_multipart() or is_file or is_body else b""
            supported = part.get_content_type() in ("text/plain", "text/html")
            visible_text = is_body or part.get_content_maintype() == "text"
            unsupported_body = not supported and visible_text
            if unsupported_body:
                complete = False
            if is_file:
                name = str(
                    part.get_filename()
                    or f"part-{index + 1}" + (mimetypes.guess_extension(part.get_content_type()) or ".bin")
                )[:255]
                files.append(
                    {
                        "name": name,
                        "sha256": digest(payload),
                        "size": len(payload),
                        "type": part.get_content_type(),
                        "part": index,
                    }
                )
            if part.is_multipart():
                if is_body:
                    visible.append({"type": part.get_content_type(), "text": base64.b64encode(payload).decode()})
                continue
            if not visible_text:
                continue
            if not supported and part.get_content_maintype() != "text":
                # An opaque primary body still needs an inert, complete representation for review.
                visible.append({"type": part.get_content_type(), "text": base64.b64encode(payload).decode()})
                continue
            if not is_body:
                file_texts.add(len(visible))
            try:
                text = payload.decode(part.get_content_charset() or "utf-8", errors="strict")
            except (UnicodeDecodeError, LookupError):
                text, complete = payload.decode("utf-8", errors="replace"), False
            if part.get_content_type() == "text/plain":
                if is_body:
                    texts.append(text)
                visible.append({"type": "text/plain", "text": text})
            elif part.get_content_type() == "text/html":
                html = VisibleHTML()
                html.feed(text)
                html.close()
                complete = complete and html.complete
                # Unknown rendering stays inert in the preview, with no discarded body or attribute bytes.
                visible.append({"type": "text/html", "text": "".join(html.text) if html.complete else text})
            else:
                visible.append({"type": part.get_content_type(), "text": text})
        if len(str(msg.get("subject", ""))) > 1000:
            raise Refused(f"the subject is {len(str(msg.get('subject', ''))):,} characters; the limit is 1,000")
        if len(files) > 50:
            raise Refused(f"it has {len(files)} files; the limit is 50")
        shown = sum(len(p["text"]) for i, p in enumerate(visible) if i not in file_texts)
        if shown > MAX_PREVIEW:
            raise Refused(f"its visible text is {kib(shown)}; the limit is {kib(MAX_PREVIEW)}")
        if shown + sum(len(visible[i]["text"]) for i in file_texts) > MAX_PREVIEW:
            # A text file is a file like a PDF: listed, hashed and downloadable from the review. Its words join the
            # preview only while they fit; left out, the email is no longer fully classified, so a person is asked.
            visible = [p for i, p in enumerate(visible) if i not in file_texts]
            complete = False
    except Refused as exc:
        raise Problem("email", "Invalid email: " + str(exc), 422) from exc
    except (ValueError, TypeError, LookupError) as exc:
        raise Problem("email", "Invalid email: its MIME could not be read (" + str(exc)[:200] + ")", 422) from exc
    body = "\n".join(texts) if texts else "\n".join(p["text"] for p in visible)
    parts = [
        {
            "type": p.get_content_type(),
            "parameters": sorted((k, str(v)) for k, v in p.get_params()[1:] if k != "boundary")
            if p.get_params()
            else [],
            "name": p.get_filename(),
            "content_id": p.get("Content-ID"),
            "content_location": p.get("Content-Location"),
            "disposition": p.get_content_disposition(),
            "children": len(list(p.iter_parts())) if p.is_multipart() else 0,
            "sha256": digest(part_bytes(p))
            if not p.is_multipart() or id(p) in attachments or p.get_content_type() == "message/rfc822"
            else None,
        }
        for p in leaves
    ]
    material_headers = {
        key: str(msg.get(key) or "") for key in ("from", "sender", "reply-to", "in-reply-to", "references")
    }
    groups = {key: sorted(a.lower() for _, a in getaddresses(msg.get_all(key, [])) if a) for key in ("to", "cc", "bcc")}
    proof = {
        "subject": str(msg.get("subject", "")),
        "headers": material_headers,
        "recipient_headers": groups,
        "parts": parts,
    }
    return {
        "recipients": to,
        "attachments": files,
        "subject": str(msg.get("subject", ""))[:1000],
        "body_sha256": digest(body),
        "body": body,
        "content_sha256": digest(json.dumps(proof, sort_keys=True, separators=(",", ":"))),
        "in_reply_to": str(msg.get("in-reply-to", "")),
        "references": str(msg.get("references", "")),
        "visible_bodies": visible,
        "classification_complete": complete,
        "recipient_headers": groups,
        "raw": raw,
    }


def attachment(raw, index):
    leaves = file_parts(parsed(raw))
    if not 0 <= index < len(leaves):
        raise Problem("not_found", "File not found", 404)
    return part_bytes(leaves[index])
