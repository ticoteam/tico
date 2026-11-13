"""Split a URL without letting the parser's own error escape: `urlsplit` raises ValueError on input such as
`https://[` (an unclosed IPv6 bracket). Each caller keeps its own rules for what it accepts (backend/docs.py for a
doc's address, backend/mcp_oauth.py for redirect URIs, clients/s3links.py for view bases; backend/egress.py has its
own); this only turns "the parser rejected it" into None, which each of them treats as not acceptable.
Pure stdlib: the server, the runner and the CLI all import it."""

from urllib.parse import urlsplit


def split(url):
    """`urlsplit(url)`, or None when the parser rejects `url`."""
    try:
        return urlsplit(url)
    except ValueError:
        return None
