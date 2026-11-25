"""Save public HTTPS ad media locally with a 50 MB cap. Run with URL and an output path stem."""

import argparse
import http.client
import ipaddress
import os
import socket
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 50_000_000
TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}
REDIRECTS = {301, 302, 303, 307, 308}


def public(ip):
    if ip.version == 6:
        if ip.ipv4_mapped:
            return public(ip.ipv4_mapped)
        if (
            ip.is_site_local
            or ip.sixtofour
            or ip.teredo
            or ip in ipaddress.ip_network("64:ff9b::/96")
            or ip in ipaddress.ip_network("64:ff9b:1::/48")
        ):
            return False
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def connection(parts):
    host = parts.hostname
    if not host or parts.scheme != "https" or "@" in parts.netloc or parts.port not in (None, 443):
        raise ValueError("Use a public HTTPS URL on port 443 without credentials")
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not public(ipaddress.ip_address(entry[4][0])) for entry in addresses):
        raise ValueError("Refusing an address that is not public")

    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            # TLS verifies the hostname while the socket uses only the addresses checked above.
            for family, kind, proto, _, address in addresses:
                sock = socket.socket(family, kind, proto)
                try:
                    sock.settimeout(self.timeout)
                    sock.connect(address)
                except OSError:
                    sock.close()
                    continue
                try:
                    self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                except BaseException:
                    sock.close()
                    raise
                return
            raise OSError("Could not connect to the media host")

    return PinnedHTTPS(host, timeout=15)


def save(response, stem):
    suffix = TYPES.get((response.getheader("Content-Type") or "").split(";")[0].strip().lower())
    if not suffix:
        raise ValueError("The response is not a supported image or video")
    if int(response.getheader("Content-Length") or 0) > MAX_BYTES:
        raise ValueError("The asset exceeds 50 MB")
    path = stem.with_name(stem.name + suffix)
    with tempfile.NamedTemporaryFile(dir=path.parent) as out:
        size = 0
        while chunk := response.read1(min(65536, MAX_BYTES + 1 - size)):
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("The asset exceeds 50 MB")
            out.write(chunk)
        if not size:
            raise ValueError("The asset is empty")
        out.flush()
        os.link(out.name, path)
    return path


def download(url, stem):
    for _ in range(6):
        parts = urlsplit(url)
        conn = connection(parts)
        try:
            target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
            conn.request("GET", target, headers={"Accept-Encoding": "identity"})
            with conn.getresponse() as response:
                location = response.getheader("Location")
                if response.status in REDIRECTS and location:
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise ValueError(f"The media host returned HTTP {response.status}")
                return save(response, stem)
        finally:
            conn.close()
    raise ValueError("Too many redirects")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("output_stem", type=Path)
    args = parser.parse_args()
    try:
        print(download(args.url, args.output_stem))
    except (OSError, ValueError, http.client.HTTPException) as exc:
        parser.exit(1, f"Download refused: {exc}\n")


if __name__ == "__main__":
    main()
