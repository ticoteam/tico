# syntax=docker/dockerfile:1
# Three images from one file, chosen with --target:
#   server   ghcr.io/ticoteam/tico          the API and web app, plus Litestream. It runs no bots.
#   runner   ghcr.io/ticoteam/tico-runner   what a bot needs: git, gh, node, python, build tools, and the system
#            libraries a Playwright Chromium needs (bots install the browser itself). No model CLIs:
#            the runner installs the ones the company's providers need into its volume (docs/harnesses.md).
#   updater  ghcr.io/ticoteam/tico-updater  the one-click updater (compose service `updater`).
# Tool versions and sha256 digests come from docker/versions.env, so the server and runner
# images pin the same things.
# Health checks probe every 2 seconds while a container starts (Docker 25+), so an update sees it healthy at once.
# Layer order is for release speed: system packages, tools and the venv come first, and the source tree is
# copied last, so a release that changes only source rebuilds one small layer per image.
# The Rust runner and screen relay are compiled here by default. Release validation instead passes the static
# binaries it built and tests (NATIVE=prebuilt, with dist/native/amd64/ and dist/native/arm64/ in the context), so a
# release ships exactly the binaries its gate ran.
ARG NATIVE=compiled
ARG PYTHON_IMAGE=python:3.15.0-slim-trixie@sha256:d802f03223b9e00604cd54574bcac1a4638914c43683d3f0afd6ebee35f25313

FROM ${PYTHON_IMAGE} AS download
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*
COPY docker/versions.env /tmp/versions.env
COPY docker/fetch-tool.sh /usr/local/bin/fetch-tool

# SQLite at SQLITE_VERSION (docker/versions.env), built from the amalgamation: Debian trixie ships 3.46.1.
FROM download AS sqlite
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY docker/sqlite.sh /usr/local/bin/tico-sqlite
RUN tico-sqlite build /out

# What every image below starts from: the Python image with that SQLite in place of Debian's, checked here (the
# build fails unless Python's sqlite3 reports it), and libyaml for PyYAML's C loader (backend/onboarding.py).
FROM ${PYTHON_IMAGE} AS python
RUN apt-get update && apt-get install -y --no-install-recommends libyaml-0-2 \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=bind,from=sqlite,source=/out,target=/tmp/sqlite \
    --mount=type=bind,source=docker/versions.env,target=/tmp/versions.env \
    --mount=type=bind,source=docker/sqlite.sh,target=/tmp/sqlite.sh \
    sh /tmp/sqlite.sh install /tmp/sqlite

FROM download AS litestream
RUN fetch-tool litestream /out

FROM download AS node
RUN fetch-tool node /opt/node

FROM download AS gh
RUN fetch-tool gh /out

FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

# The Python dependencies, kept apart from the source so a source change reuses this layer. uv installs the same
# pinned requirements as pip, about ten times faster; it is only mounted here, so no image ships it.
FROM python AS venv
# A compiler and libyaml headers for any package without a wheel for this Python (PyYAML 6.0.3 on 3.15); this
# stage is not shipped, only its venv.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libyaml-dev \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/tico/.venv
COPY backend/requirements.txt /tmp/requirements.txt
RUN --mount=type=bind,from=uv,source=/uv,target=/usr/local/bin/uv \
    uv pip install --python /opt/tico/.venv/bin/python --no-cache --compile-bytecode -r /tmp/requirements.txt \
    && chmod -R go-w,go+rX /opt/tico/.venv

FROM docker:cli@sha256:018edbc908e08fcc9dbf029c812c34251e9b4719e6f71ca0e5eae2a987d014ca AS dockercli

FROM python AS updater
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose
COPY LICENSE NOTICE /usr/share/doc/tico/
COPY licenses/Apache-2.0.txt /usr/share/doc/tico/licenses/
COPY docker/updater.py /usr/local/bin/tico-updater
COPY docker/runner-memory.sh /usr/local/bin/tico-runner-memory
ARG TICO_VERSION=dev
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
LABEL org.opencontainers.image.version=${TICO_VERSION} \
      org.opencontainers.image.source=https://github.com/ticoteam/tico \
      org.opencontainers.image.licenses=LicenseRef-PolyForm-Perimeter-1.0.1
EXPOSE 8080
CMD ["python", "/usr/local/bin/tico-updater"]

# What both the server and the runner run: this source tree, checked against the shared venv. The two images
# copy it after their system packages, so those layers stay cached across releases.
FROM python AS source
ARG TICO_VERSION=dev
ARG TICO_COMMIT
ARG TICO_REPOSITORY
WORKDIR /opt/tico
COPY . /opt/tico
# Deployed task completion needs the source commit, not just the version label.
RUN python - <<'PY'
import json
import os
import re
import sys
from pathlib import Path

commit = os.environ.get("TICO_COMMIT", "")
repository = os.environ.get("TICO_REPOSITORY", "")
if os.environ.get("TICO_VERSION", "dev") != "dev" or commit or repository:
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise SystemExit("Image provenance requires TICO_COMMIT (full SHA) and TICO_REPOSITORY (owner/repo)")
# The shipped code's hash: files copied over a release image later show as modified (backend/flight.py).
sys.path.insert(0, ".")
from backend.code_hash import digest
Path("release-manifest.json").write_text(json.dumps({"commit": commit, "repository": repository,
                                                     "code": digest(".")}) + "\n")
PY
RUN chmod -R go-w,go+rX /opt/tico
# The venv is only mounted here, so this stage's /opt/tico holds the source alone.
RUN --mount=type=bind,from=venv,source=/opt/tico/.venv,target=/opt/tico/.venv \
    .venv/bin/python -c 'import backend.app, runner.connectors, runner.close_calls, runner.importers.service, clients.environments, yaml; yaml.CSafeLoader' \
    && .venv/bin/python -m compileall -q backend runner clients

# The Rust runner (runner-rs/), built on the same Debian release as the runner image so it
# links against the same glibc. Only its sources are copied, so a change elsewhere reuses this layer.
FROM rust:1.92.0-slim-trixie@sha256:bf3368a992915f128293ac76917ab6e561e4dda883273c8f5c9f6f8ea37a378e AS runner-bin-compiled
# The release this binary reports (runner-rs/build.rs). A release build must say its own version: checked below.
ARG TICO_VERSION=dev
WORKDIR /src/runner-rs
COPY runner-rs/Cargo.toml runner-rs/Cargo.lock runner-rs/build.rs ./
COPY runner-rs/src ./src
# The harness manifests are compiled in (harness_tools.rs BUILT_IN), from ../runner/harnesses, and are part of
# the source fingerprint (build.rs, `tico-runner --build-id`).
COPY runner/harnesses /src/runner/harnesses
COPY LICENSE /src/LICENSE
RUN cargo build --release --locked && install -D -m 0755 target/release/tico-runner /out/tico-runner \
    && { [ "$TICO_VERSION" = dev ] || [ "$(/out/tico-runner --release)" = "${TICO_VERSION#v}" ]; }

FROM scratch AS runner-bin-prebuilt
ARG TARGETARCH
COPY --chmod=0755 dist/native/${TARGETARCH}/tico-runner /out/tico-runner

FROM runner-bin-${NATIVE} AS runner-bin

FROM python AS runner
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git curl openssh-client build-essential ripgrep jq procps \
      libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 libcairo2 libcups2t64 libdbus-1-3 libdrm2 libgbm1 \
      libglib2.0-0t64 libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 \
      libxfixes3 libxkbcommon0 libxrandr2 libfontconfig1 fonts-liberation \
      ffmpeg fonts-noto-core fonts-noto-color-emoji \
      xvfb x11vnc matchbox-window-manager \
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10002 ticorun \
    && useradd --uid 10002 --gid ticorun --home-dir /home/runner --no-create-home --shell /bin/bash ticorun \
    && useradd --uid 10003 --gid ticorun --home-dir /home/runner --no-create-home --shell /bin/bash bot \
    && for slot in $(seq 0 63); do \
         useradd --uid "$((10100 + slot))" --gid ticorun --home-dir /home/runner --no-create-home \
           --shell /usr/sbin/nologin "tico-turn-$(printf '%03d' "$slot")"; \
       done
# The SQLite check again, after this image's own apt installs.
RUN --mount=type=bind,source=docker/versions.env,target=/tmp/versions.env \
    --mount=type=bind,source=docker/sqlite.sh,target=/tmp/sqlite.sh \
    sh /tmp/sqlite.sh check
COPY --from=node /opt/node /opt/node
COPY --from=gh /out/gh /usr/local/bin/gh
# The runner (TICO_RUNNER_BIN): docker/runner-entrypoint.sh enrolls with it and execs it.
COPY --from=runner-bin /out/tico-runner /usr/local/bin/tico-runner
# node and npm are here for the runner's own installs of model CLIs (runner-rs/src/harness_tools.rs).
RUN ln -s /opt/node/bin/node /opt/node/bin/npm /opt/node/bin/npx /usr/local/bin/
COPY docker/runner-entrypoint.sh /usr/local/bin/tico-runner-entrypoint
COPY docker/gitconfig /etc/gitconfig
COPY docker/profile-turn.sh /etc/profile.d/tico-turn.sh
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
COPY --from=venv /opt/tico/.venv /opt/tico/.venv
COPY --from=source /opt/tico /opt/tico
ARG TICO_VERSION=dev
WORKDIR /opt/tico
# The tools directory is in the volume, so installed model CLIs survive a restart or a new image. It is
# last on PATH, so `docker exec` shells find the CLIs the runner installed.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TICO_VERSION=${TICO_VERSION} TICO_RELEASE=${TICO_VERSION} \
    GIT_TERMINAL_PROMPT=0 DISABLE_AUTOUPDATER=1 HOME=/home/runner TICO_TOOLS_DIR=/home/runner/tools TICO_RUNNER_BIN=/usr/local/bin/tico-runner \
    PATH=/opt/tico/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/runner/tools/bin
LABEL org.opencontainers.image.version=${TICO_VERSION} \
      org.opencontainers.image.source=https://github.com/ticoteam/tico \
      org.opencontainers.image.licenses=LicenseRef-PolyForm-Perimeter-1.0.1
USER 10002:10002
VOLUME /home/runner
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --start-interval=2s --retries=3 CMD pgrep -f '(python -m runner|tico-runner) .* run$' >/dev/null
ENTRYPOINT ["tico-runner-entrypoint"]
CMD ["run"]

FROM rust:1.92.0-slim-trixie@sha256:bf3368a992915f128293ac76917ab6e561e4dda883273c8f5c9f6f8ea37a378e AS screen-bin-compiled
WORKDIR /src/relay-rs
COPY LICENSE /src/LICENSE
COPY relay-rs/Cargo.toml relay-rs/Cargo.lock relay-rs/build.rs ./
COPY relay-rs/src ./src
RUN cargo build --release --locked && install -D -m 0755 target/release/tico-screen-relay /out/tico-screen-relay

FROM scratch AS screen-bin-prebuilt
ARG TARGETARCH
COPY --chmod=0755 dist/native/${TARGETARCH}/tico-screen-relay /out/tico-screen-relay

FROM screen-bin-${NATIVE} AS screen-bin

FROM python AS server
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 tico \
    && useradd --uid 10001 --gid tico --home-dir /data --no-create-home --shell /usr/sbin/nologin tico
# The SQLite check again, after this image's own apt installs.
RUN --mount=type=bind,source=docker/versions.env,target=/tmp/versions.env \
    --mount=type=bind,source=docker/sqlite.sh,target=/tmp/sqlite.sh \
    sh /tmp/sqlite.sh check
COPY --from=litestream /out/litestream /usr/local/bin/litestream
COPY --from=screen-bin /out/tico-screen-relay /usr/local/bin/tico-screen-relay
COPY docker/entrypoint.sh /usr/local/bin/tico-entrypoint
# A named volume copies the ownership of the directory it first covers.
RUN chmod 0755 /usr/local/bin/tico-entrypoint \
    && install -d -m 0700 -o tico -g tico /data /control /backups \
    && install -d -m 0755 -o tico -g tico /tunnel /caddy-generated
COPY --from=venv /opt/tico/.venv /opt/tico/.venv
COPY --from=source /opt/tico /opt/tico
ARG TICO_VERSION=dev
# The commit is also in release-manifest.json, which the flight recorder checks against the release tag (backend/flight.py).
ARG TICO_COMMIT
WORKDIR /opt/tico
ENV PATH=/opt/tico/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TICO_VERSION=${TICO_VERSION} TICO_RELEASE=${TICO_VERSION}
# Threaded Python on a small host: glibc's per-thread malloc arenas fragmented until a 3.8 GB host was OOM-killed.
ENV MALLOC_ARENA_MAX=2
LABEL org.opencontainers.image.version=${TICO_VERSION} \
      org.opencontainers.image.revision=${TICO_COMMIT} \
      org.opencontainers.image.source=https://github.com/ticoteam/tico \
      org.opencontainers.image.licenses=LicenseRef-PolyForm-Perimeter-1.0.1
EXPOSE 8765
USER 10001:10001
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --start-interval=2s --retries=4 CMD curl -fsS --max-time 4 http://127.0.0.1:8765/healthz >/dev/null
ENTRYPOINT ["tico-entrypoint"]
CMD ["server"]
