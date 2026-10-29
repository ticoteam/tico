"""One TLS context per trust mode, shared by the outbound clients on recurring paths (the inbox and hook pollers).

httpx builds a new SSL context for every Client that does not pass one, and loading certifi's bundle costs CPU each
time. A poller that opens a client per tick paid that on every tick. A context is safe to share across threads and
clients once built, and each client keeps its own timeouts, headers and proxy settings.
"""
import functools

import httpx


@functools.lru_cache(maxsize=2)
def context(trust_env=True):
    # The same context an httpx.Client builds for itself with these settings, built once per trust mode.
    return httpx.create_ssl_context(verify=True, trust_env=trust_env)
