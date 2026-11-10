# cloud-init files for a Tico server or runner

Paste-ready `#cloud-config` user data for any Ubuntu 24.04 cloud VM. `python3 -m setup --cloud hetzner|digitalocean` fills the
same files in for you (`setup/cloudinit.py`).

| File | Installs | Inbound |
|---|---|---|
| `tico-server.yaml` | `/opt/tico/.env`, then the release's `install.sh` (Docker, the compose bundle for the pinned release, the stack) | 80, 443 (and 22 when allowed) |
| `tico-runner.yaml` | Docker (`install.sh --docker-only`) and the `tico-runner` container, joined with a one-time code | none (22 when allowed) |

Both call the same `install.sh` that a person runs by hand (`releases/download/<version>/install.sh`), so there is one
installer to maintain. Edit only the block between `# --- inputs begin ---` and `# --- inputs end ---`. `TICO_CLOUD_VERSION` must be a release
such as `v1.2.3` (the placeholder `v0.0.0` is refused). Full guide: [docs/install.md](../../docs/install.md).
The log is `/var/log/tico-setup.log` on the machine and never contains the inputs.
