# syntax=docker/dockerfile:1
# Three images from one file, chosen with --target:
#   server   ghcr.io/ticoteam/tico          the API and web app, plus Litestream. It runs no bots.
#   runner   ghcr.io/ticoteam/tico-runner   what a bot needs: git, gh, node, python, build tools. No model CLIs:
#            the runner installs the ones the company's providers need into its volume (docs/harnesses.md).
#   updater  ghcr.io/ticoteam/tico-updater  the one-click updater (compose service `updater`).
# Tool versions and sha256 digests come from docker/versions.env, so the server and runner
# images pin the same things.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

FROM ${PYTHON_IMAGE} AS download
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*
COPY docker/versions.env /tmp/versions.env
COPY docker/fetch-tool.sh /usr/local/bin/fetch-tool

FROM download AS litestream
RUN fetch-tool litestream /out

FROM download AS node
RUN fetch-tool node /opt/node

FROM download AS gh
RUN fetch-tool gh /out

FROM ${PYTHON_IMAGE} AS venv
RUN python -m venv /opt/tico/.venv
COPY backend/requirements.txt /tmp/requirements.txt
RUN /opt/tico/.venv/bin/pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.txt

FROM docker:cli@sha256:018edbc908e08fcc9dbf029c812c34251e9b4719e6f71ca0e5eae2a987d014ca AS dockercli

FROM ${PYTHON_IMAGE} AS updater
ARG TICO_VERSION=dev
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose
COPY docker/updater.py /usr/local/bin/tico-updater
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
LABEL org.opencontainers.image.version=${TICO_VERSION} \
      org.opencontainers.image.source=https://github.com/ticoteam/tico
EXPOSE 8080
CMD ["python", "/usr/local/bin/tico-updater"]

# What both the server and the runner run: this source tree in the shared venv.
FROM ${PYTHON_IMAGE} AS base
ARG TICO_VERSION=dev
ARG TICO_COMMIT
ARG TICO_REPOSITORY
COPY --from=venv /opt/tico/.venv /opt/tico/.venv
WORKDIR /opt/tico
COPY . /opt/tico
# Deployed task completion needs the source commit, not just the version label.
RUN python - <<'PY'
import json
import os
import re
from pathlib import Path

commit = os.environ.get("TICO_COMMIT", "")
repository = os.environ.get("TICO_REPOSITORY", "")
if os.environ.get("TICO_VERSION", "dev") != "dev" or commit or repository:
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise SystemExit("Image provenance requires TICO_COMMIT (full SHA) and TICO_REPOSITORY (owner/repo)")
Path("release-manifest.json").write_text(json.dumps({"commit": commit, "repository": repository}) + "\n")
PY
RUN chmod -R go-w,go+rX /opt/tico \
    && .venv/bin/python -c 'import backend.app, runner.service, clients.environments' \
    && .venv/bin/python -m compileall -q backend runner clients
ENV PATH=/opt/tico/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TICO_VERSION=${TICO_VERSION} TICO_RELEASE=${TICO_VERSION}
LABEL org.opencontainers.image.version=${TICO_VERSION} \
      org.opencontainers.image.source=https://github.com/ticoteam/tico \
      org.opencontainers.image.licenses=Apache-2.0

FROM base AS runner
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git curl openssh-client build-essential ripgrep jq procps \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10002 ticorun \
    && useradd --uid 10002 --gid ticorun --home-dir /home/runner --no-create-home --shell /bin/bash ticorun \
    && useradd --uid 10003 --gid ticorun --home-dir /home/runner --no-create-home --shell /bin/bash bot
COPY --from=node /opt/node /opt/node
COPY --from=gh /out/gh /usr/local/bin/gh
# node and npm are here for the runner's own installs of model CLIs (runner/harness_tools.py).
RUN ln -s /opt/node/bin/node /opt/node/bin/npm /opt/node/bin/npx /usr/local/bin/
COPY docker/runner-entrypoint.sh /usr/local/bin/tico-runner-entrypoint
COPY docker/gitconfig /etc/gitconfig
# Two users share /home/runner (SECURITY.md, runner/isolation.py). The supervisor stays `ticorun` (10002,
# what every earlier image ran as, so runner.json, state-* and tools/ keep their owner and an older image
# can still start on the volume). Every process that runs bot code is `bot` (10003), in the same group so
# the workspace and model logins work for both, but with no access to the supervisor's 0600/0700 files.
# Started as root with the capabilities of docker/runner.compose.yaml, the entrypoint migrates the volume
# once and re-executes itself as ticorun with those capabilities as ambient ones; without them (a bare
# `docker run`, an older compose file) the image runs as `ticorun` alone, as before. A named volume copies
# the ownership of the directory it first covers, so a new volume starts single-user and is migrated.
RUN chmod 0755 /usr/local/bin/tico-runner-entrypoint \
    && install -d -m 0700 -o ticorun -g ticorun /home/runner /home/runner/workspace /home/runner/workspace/secrets
# The tools directory is in the volume, so installed model CLIs survive a restart or a new image. It is
# last on PATH, so `docker exec` shells find the CLIs the runner installed.
ENV GIT_TERMINAL_PROMPT=0 DISABLE_AUTOUPDATER=1 HOME=/home/runner TICO_TOOLS_DIR=/home/runner/tools \
    PATH=/opt/tico/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/runner/tools/bin
USER 10002:10002
VOLUME /home/runner
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD pgrep -f 'python -m runner .* run$' >/dev/null
ENTRYPOINT ["tico-runner-entrypoint"]
CMD ["run"]

FROM base AS server
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 tico \
    && useradd --uid 10001 --gid tico --home-dir /data --no-create-home --shell /usr/sbin/nologin tico
COPY --from=litestream /out/litestream /usr/local/bin/litestream
COPY docker/entrypoint.sh /usr/local/bin/tico-entrypoint
# A named volume copies the ownership of the directory it first covers.
RUN chmod 0755 /usr/local/bin/tico-entrypoint \
    && install -d -m 0700 -o tico -g tico /data /control /backups \
    && install -d -m 0755 -o tico -g tico /tunnel
EXPOSE 8765
USER 10001:10001
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=4 CMD curl -fsS --max-time 4 http://127.0.0.1:8765/healthz >/dev/null
ENTRYPOINT ["tico-entrypoint"]
CMD ["server"]
