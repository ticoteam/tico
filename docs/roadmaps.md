# Public roadmaps

The public roadmap is off in 0.6.0-rc.1. It comes back with the Product pipeline, built on its stages
([Product](pipelines-v3.md#product)).

Until then:

- Every public route under `/public/roadmaps/{label}` answers 404 "The public roadmap is not available yet.", except
  one: `DELETE /public/roadmaps/{label}/tasks/{id}/requesters/{token}` still removes a visitor's request, so a visitor
  can always take back their contact details. Tokens are bound to one task; an unknown token gets 404.
- The settings are kept and stay editable: `GET` and `POST /api/v2/roadmaps/settings`, `tico roadmap settings`
  (owners and admins). An update is partial and an empty one is refused.
- A task's requesters stay internal and work as before: `GET`/`POST /api/v2/tasks/{id}/requesters`,
  `POST .../requesters/{requester_id}` (partial update) and `POST .../requesters/{requester_id}/delete`, and
  `tico task requesters`, `tico task requester-save`, `tico task requester-remove`. Lists are paged and never return
  tokens. Teammate status comes from a verified actor, never from an email claim.

## Client addresses behind a proxy

`TICO_TRUSTED_PROXIES` lists the IP addresses or CIDRs whose forwarding headers are believed, including
`127.0.0.1/32,::1/128` for the supervised Rust relay. A native install's default trusts only loopback; remote
clients cannot choose their IP with forwarding headers.

The standard Docker Compose file needs no change: Caddy and cloudflared reach the server only over a dedicated
`edge` network (`172.30.254.0/28`) from fixed addresses (`172.30.254.2` and `172.30.254.3`). The only other
containers on that network, the server and the tunnel's `hold` (which keeps requests waiting while the server
restarts), draw addresses from its upper half (`172.30.254.8/29`), so they never take a front door's. The server's
default `TICO_TRUSTED_PROXIES` is loopback, those two addresses and that range; `hold` itself believes forwarded
addresses only from cloudflared. If that subnet clashes with a network on your computer, set `TICO_EDGE_SUBNET`,
`TICO_EDGE_DYNAMIC_RANGE`, `TICO_CADDY_ADDRESS` and `TICO_CLOUDFLARED_ADDRESS` together in `.env`; the trusted list
follows them. Setting `TICO_TRUSTED_PROXIES` in `.env` replaces the whole list. With your own proxy in front of a native install, list its
addresses, never a range shared with untrusted machines.

`TICO_CLIENT_IP_HEADER=x-forwarded-for` (default) works behind both Caddy and cloudflared: the relay appends its
peer to the chain, and the API chooses the rightmost untrusted address. `TICO_CLIENT_IP_HEADER=cf-connecting-ip` is
for a deployment whose only trusted ingress is cloudflared. Both processes receive `TICO_TRUSTED_PROXIES` through
the standard server command and Docker Compose. Restart after changing these environment settings. Roadmap settings
and requester updates are partial: omitted fields keep their current values, and an empty update is rejected.
