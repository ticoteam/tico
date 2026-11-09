#!/bin/sh
# Runs inside a distro container (see test_install_sh.py): scripts/install.sh against local release files, with
# docker, systemctl, ss, df and uname stubbed so nothing on the host or the network (beyond apt) is touched.
S=/stubs
mkdir -p $S /work
printf 'MemTotal:        2000000 kB\n' > $S/meminfo
: > $S/health
cat > $S/docker <<'STUB'
#!/bin/sh
echo "docker $*" >> /stubs/docker.log
case "$*" in
  "compose version --short") echo 2.29.7 ;;
  "compose version") echo "Docker Compose version v2.29.7" ;;
  "version --format"*) echo 27.0.0 ;;
esac
exit 0
STUB
printf '#!/bin/sh\necho "systemctl $*" >> /stubs/docker.log\n' > $S/systemctl
cat > $S/id <<'STUB'
#!/bin/sh
if [ "$1" = -u ]; then echo "${STUB_UID:-0}"; else exec /usr/bin/id "$@"; fi
STUB
cat > $S/uname <<'STUB'
#!/bin/sh
case "$1" in -s) echo "${STUB_OS:-Linux}" ;; -m) echo "${STUB_ARCH:-x86_64}" ;; *) exec /usr/bin/uname "$@" ;; esac
STUB
cat > $S/df <<'STUB'
#!/bin/sh
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\nfake 100000000 1 %s 1%% /\n' "${STUB_DF:-50000000}"
STUB
cat > $S/ss <<'STUB'
#!/bin/sh
[ -f /stubs/ss.out ] && cat /stubs/ss.out
exit 0
STUB
chmod +x $S/*
export PATH="$S:$PATH"
export TICO_INSTALL_RELEASES_URL=file:///rel TICO_INSTALL_MEMINFO=$S/meminfo TICO_INSTALL_HEALTH_URL=file://$S/health TICO_INSTALL_HEALTH_TRIES=1
export TICO_INSTALL_LATEST_URL=file:///rel/latest.json

ok() { echo "ok $1"; }
bad() { echo "not ok $1: $2"; }
code_is() { if [ "$3" = "$2" ]; then ok "$1"; else bad "$1" "exit $3, wanted $2: $4"; fi; }
has() { case $3 in *"$2"*) ok "$1" ;; *) bad "$1" "missing '$2' in: $3" ;; esac; }
lacks() { case $3 in *"$2"*) bad "$1" "found '$2' in: $3" ;; *) ok "$1" ;; esac; }
sum() { sha256sum "$1" | cut -d' ' -f1; }
inst() { sh /rel/download/v0.2.0/install.sh "$@"; }

# --- preflight -------------------------------------------------------------------------------------
D=/work/pre
out=$(STUB_OS=Darwin inst --dir $D 2>&1); code_is preflight-not-linux 3 $? "$out"
out=$(STUB_ARCH=s390x inst --dir $D 2>&1); code_is preflight-arch 3 $? "$out"
out=$(STUB_UID=1000 inst --dir $D 2>&1); code_is preflight-needs-root-or-sudo 3 $? "$out"; has preflight-needs-root-msg "root" "$out"
printf 'MemTotal:         500000 kB\n' > $S/meminfo_small
out=$(TICO_INSTALL_MEMINFO=$S/meminfo_small inst --dir $D 2>&1); code_is preflight-memory 3 $? "$out"; has preflight-memory-msg "1 GB of memory" "$out"
out=$(STUB_DF=200000 inst --dir $D 2>&1); code_is preflight-disk 3 $? "$out"; has preflight-disk-msg "free disk" "$out"
printf 'LISTEN 0 4096 0.0.0.0:80 0.0.0.0:*\n' > $S/ss.out
out=$(inst --dir $D 2>&1); code_is preflight-port-80 3 $? "$out"; has preflight-port-msg "Port 80" "$out"
out=$(inst --dir /work/pre-tunnel --tunnel -- --non-interactive 2>&1); lacks preflight-tunnel-skips-ports "Port 80" "$out"
rm -f $S/ss.out
out=$(inst --dir relative/path 2>&1); code_is usage-dir-absolute 2 $? "$out"
out=$(inst --bogus 2>&1); code_is usage-unknown-flag 2 $? "$out"
out=$(inst --version latest --dir $D 2>&1); code_is usage-version-shape 2 $? "$out"
if [ ! -e $D/compose.yaml ]; then ok preflight-changed-nothing; else bad preflight-changed-nothing "$D/compose.yaml exists"; fi

# --- fresh install ---------------------------------------------------------------------------------
D=/work/fresh
: > $S/docker.log
out=$(TICO_OIDC_CLIENT_SECRET=SEKRET-VALUE-123 inst --dir $D --yes -- --domain tico.example.com --company Acme 2>&1); code_is fresh-exit 0 $? "$out"
lacks fresh-secret-not-echoed SEKRET-VALUE "$out"
if [ "$(cat $D/.bundle-version)" = v0.2.0 ]; then ok fresh-pins-baked-version; else bad fresh-pins-baked-version "$(cat $D/.bundle-version 2>&1)"; fi
if grep -q 'marker v0.2.0' $D/compose.yaml; then ok fresh-compose-from-bundle; else bad fresh-compose-from-bundle "no marker"; fi
args=$(cat $D/wizard-args.txt)
has fresh-wizard-target "--target local --tico-version v0.2.0" "$args"
has fresh-wizard-yes "--yes" "$args"
has fresh-wizard-noninteractive-without-tty "--non-interactive" "$args"
has fresh-wizard-passthrough "--domain tico.example.com --company Acme" "$args"
if [ "$(stat -c %a $D/.env)" = 600 ]; then ok fresh-env-private; else bad fresh-env-private "$(stat -c %a $D/.env)"; fi
if [ "$(stat -c %a $D/.setup)" = 700 ]; then ok fresh-wizard-state-private; else bad fresh-wizard-state-private "$(stat -c %a $D/.setup)"; fi
has fresh-next-steps "Add computer" "$out"
out=$(inst --dir /work/tun --tunnel --yes -- --domain t.example.com 2>&1); has tunnel-front-door "--front-door cloudflared" "$(cat /work/tun/wizard-args.txt)"
# A failed wizard with no terminal: the failure's exit code, and no offer to send it to support (nothing could answer).
out=$(FAKE_WIZARD_FAIL=1 inst --dir /work/wfail --yes -- --domain f.example.com 2>&1); code_is wizard-fail-exit 6 $? "$out"
lacks wizard-fail-no-offer-without-tty "Send this to Tico support" "$out"
if [ "$(wc -l < /work/wfail/wizard-args.txt)" = 1 ]; then ok wizard-fail-setup-ran-once; else bad wizard-fail-setup-ran-once "$(cat /work/wfail/wizard-args.txt)"; fi

# --- local quick start: no domain, no sign-in, no wizard --------------------------------------------------
L=/work/local
printf 'LISTEN 0 4096 0.0.0.0:80 0.0.0.0:*\n' > $S/ss.out
out=$(inst --dir $L --local --owner-email ana@acme.example --company Acme 2>&1); code_is local-exit 0 $? "$out"
rm -f $S/ss.out
if [ ! -e $L/wizard-args.txt ]; then ok local-skips-wizard; else bad local-skips-wizard "wizard ran"; fi
if grep -q '^TICO_OWNER_EMAIL=ana@acme.example$' $L/.env; then ok local-env-owner; else bad local-env-owner "$(cat $L/.env)"; fi
if grep -q -e TICO_DOMAIN -e TICO_AUTH_PROXY $L/.env; then bad local-env-has-no-domain "$(cat $L/.env)"; else ok local-env-has-no-domain; fi
if [ "$(stat -c %a $L/.env)" = 600 ]; then ok local-env-private; else bad local-env-private "$(stat -c %a $L/.env)"; fi
has local-says-local "this computer only" "$out"
out=$(inst --dir /work/local2 --local 2>&1); code_is local-needs-email 2 $? "$out"
out=$(inst --dir /work/local3 --local --tunnel --owner-email a@b.example 2>&1); code_is local-not-with-tunnel 2 $? "$out"

# --- a Mac (Docker Desktop): --local and --runner only, no sudo, no Docker install --------------------------------
out=$(STUB_OS=Darwin inst --dir /work/mac --yes 2>&1); code_is mac-team-install-refused 3 $? "$out"; has mac-team-install-says-local "--local" "$out"
out=$(STUB_OS=Darwin STUB_UID=501 inst --dir /work/mac-local --local --owner-email ana@acme.example 2>&1); code_is mac-local-exit 0 $? "$out"
has mac-local-says-macos "macOS" "$out"; has mac-local-says-local "this computer only" "$out"
if grep -q '^TICO_OWNER_EMAIL=ana@acme.example$' /work/mac-local/.env; then ok mac-local-env-owner; else bad mac-local-env-owner "no .env"; fi
out=$(STUB_OS=Darwin STUB_UID=501 inst --dir /work/mac-runner --runner --url http://server:8765 --code abc123 --label "This computer" 2>&1); code_is mac-runner-exit 0 $? "$out"
if grep -q '^TICO_URL=http://server:8765$' /work/mac-runner/.env; then ok mac-runner-env; else bad mac-runner-env "no .env"; fi
# Docker Desktop not running: say what to do, install nothing.
printf '#!/bin/sh\nexit 1\n' > $S/docker_down; cp $S/docker $S/docker_up; cp $S/docker_down $S/docker
out=$(STUB_OS=Darwin STUB_UID=501 inst --dir /work/mac-nodocker --local --owner-email ana@acme.example 2>&1); code_is mac-docker-not-running 5 $? "$out"; has mac-docker-desktop-msg "Docker Desktop" "$out"
cp $S/docker_up $S/docker; chmod +x $S/docker

# --- idempotent re-run: repair, keep .env ------------------------------------------------------------
printf 'MY_CUSTOM_SETTING=keep-me\n' >> $D/.env
before=$(sum $D/.env)
: > $S/docker.log
out=$(inst --dir $D --yes 2>&1); code_is rerun-exit 0 $? "$out"
if [ "$(sum $D/.env)" = "$before" ]; then ok rerun-env-untouched; else bad rerun-env-untouched "changed"; fi
if [ "$(wc -l < $D/wizard-args.txt)" = 1 ]; then ok rerun-skips-wizard; else bad rerun-skips-wizard "wizard ran again"; fi
has rerun-restarts-stack "compose up -d" "$(cat $S/docker.log)"
has rerun-says-repair "repairing" "$out"
rm -f $D/compose.yaml
out=$(inst --dir $D --yes 2>&1); code_is repair-exit 0 $? "$out"
if [ -f $D/compose.yaml ]; then ok repair-restores-compose; else bad repair-restores-compose "missing"; fi

# --- upgrade and no silent downgrade -----------------------------------------------------------------
out=$(inst --dir $D --version v0.3.0 --yes 2>&1); code_is upgrade-exit 0 $? "$out"
if grep -q 'marker v0.3.0' $D/compose.yaml; then ok upgrade-new-compose; else bad upgrade-new-compose "no marker"; fi
if grep -q '^TICO_TAG=v0.3.0$' $D/.env; then ok upgrade-pins-tag; else bad upgrade-pins-tag "$(cat $D/.env | grep TICO_TAG)"; fi
if grep -q '^MY_CUSTOM_SETTING=keep-me$' $D/.env; then ok upgrade-keeps-settings; else bad upgrade-keeps-settings "lost"; fi
if [ "$(grep -c '^TICO_TAG=' $D/.env)" = 1 ]; then ok upgrade-single-tag; else bad upgrade-single-tag "duplicate"; fi
if [ "$(stat -c %a $D/.env)" = 600 ]; then ok upgrade-env-private; else bad upgrade-env-private "mode"; fi
if [ -f $D/compose.yaml.previous ]; then ok upgrade-keeps-previous-compose; else bad upgrade-keeps-previous-compose "missing"; fi
out=$(inst --dir $D --yes 2>&1); code_is older-installer-exit 0 $? "$out"; has older-installer-holds "newer than this installer" "$out"
if grep -q '^TICO_TAG=v0.3.0$' $D/.env; then ok older-installer-keeps-tag; else bad older-installer-keeps-tag "changed"; fi
out=$(inst --dir $D --version v0.2.0 --yes 2>&1); code_is explicit-downgrade 0 $? "$out"
if grep -q '^TICO_TAG=v0.2.0$' $D/.env; then ok explicit-downgrade-applies; else bad explicit-downgrade-applies "not applied"; fi

# --- checksum ----------------------------------------------------------------------------------------
D=/work/tampered
out=$(inst --dir $D --version v0.9.0 --yes 2>&1); code_is checksum-mismatch-refused 4 $? "$out"; has checksum-msg "Checksum mismatch" "$out"
if [ ! -e $D/compose.yaml ] && [ ! -e $D/.env ]; then ok checksum-installs-nothing; else bad checksum-installs-nothing "files exist"; fi
out=$(inst --dir $D --version v0.8.0 --yes 2>&1); code_is missing-release 4 $? "$out"

# --- version pinning ---------------------------------------------------------------------------------
D=/work/unpinned
out=$(sh /rel/unpinned/install.sh --dir $D --yes -- --domain a.example.com 2>&1); code_is unpinned-exit 0 $? "$out"
if [ "$(cat $D/.bundle-version)" = v0.3.0 ]; then ok unpinned-uses-latest-release; else bad unpinned-uses-latest-release "$(cat $D/.bundle-version)"; fi
has unpinned-warns "not pinned to a release" "$out"
D=/work/plain
out=$(inst --dir $D --version 0.3.0 --yes -- --domain b.example.com 2>&1); code_is version-flag-without-v 0 $? "$out"
if [ "$(cat $D/.bundle-version)" = v0.3.0 ]; then ok version-flag-overrides-baked; else bad version-flag-overrides-baked "$(cat $D/.bundle-version)"; fi
if grep -q '"' /rel/download/v0.2.0/install.sh && grep -q "^TICO_VERSION_BAKED='v0.2.0'" /rel/download/v0.2.0/install.sh; then ok baked-line; else bad baked-line "not baked"; fi

# --- docker-only and help -----------------------------------------------------------------------------
out=$(inst --docker-only --dir /work/d 2>&1); code_is docker-only 0 $? "$out"
if [ ! -e /work/d/compose.yaml ]; then ok docker-only-downloads-nothing; else bad docker-only-downloads-nothing "bundle fetched"; fi
out=$(inst --help 2>&1); code_is help 0 $? "$out"; has help-text "--tunnel" "$out"
echo "done"
