#!/bin/sh
# Installs Google's Antigravity CLI as $AGY_PREFIX/bin/agy (antigravity.toml [install], run by the runner from this
# repository at its pinned checksum). Google publishes it only through its updater: one manifest per platform names a
# build-numbered tarball and its sha512. Anything that does not match exactly what that manifest is expected to say is
# refused, and nothing is installed.
#
# Never edited in place: shipped runners fetch this file by name at their pinned checksum. A change is a new file
# (antigravity-install-v2.sh) with antigravity.toml pointing at it.
set -eu

: "${AGY_PREFIX:?AGY_PREFIX names the install directory}"
MANIFESTS="https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests"
DOWNLOADS="https://storage.googleapis.com/antigravity-public/antigravity-cli/"

musl=""
case "$(uname -s)" in
  Linux)
    os=linux mac=linux
    if [ -e /lib/ld-musl-x86_64.so.1 ] || [ -e /lib/ld-musl-aarch64.so.1 ]; then musl=_musl; fi
    ;;
  Darwin) os=darwin mac=mac ;;
  *) echo "Antigravity has no build for $(uname -s)" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64 | amd64) target=amd64; cpu=x64; folder=x64 ;;
  aarch64 | arm64) target=arm64; cpu=arm64; folder=arm ;;
  *) echo "Antigravity has no build for $(uname -m)" >&2; exit 1 ;;
esac
dash=""
[ -n "$musl" ] && dash=-musl

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
curl -fsSL --proto '=https' --proto-redir '=https' --max-time 60 -o "$work/manifest.json" "$MANIFESTS/${os}_${target}${musl}.json"
# One "name": "value" per line, however the manifest is laid out (none of its values holds a brace or comma).
# shellcheck disable=SC2020  # each of the three characters becomes a newline
field() { tr '{},' '\n\n\n' <"$work/manifest.json" | sed -n "s/^[[:space:]]*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1; }
version=$(field version)
url=$(field url)
sha512=$(field sha512 | tr 'A-F' 'a-f')
echo "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || { echo "The Antigravity manifest names no version" >&2; exit 1; }
echo "$sha512" | grep -Eq '^[0-9a-f]{128}$' || { echo "The Antigravity manifest names no sha512" >&2; exit 1; }
case "$url" in
  "$DOWNLOADS$version-"*) ;;
  *) echo "The Antigravity manifest names a download outside Google's release bucket" >&2; exit 1 ;;
esac
rest=${url#"$DOWNLOADS$version-"}
echo "$rest" | grep -Eq "^[0-9]+/$os-$folder$dash/cli_${mac}_${cpu}${musl}\.tar\.gz\$" \
  || { echo "The Antigravity manifest names a download for another platform" >&2; exit 1; }

curl -fsSL --proto '=https' --proto-redir '=https' --max-time 600 -o "$work/cli.tar.gz" "$url"
if command -v sha512sum >/dev/null 2>&1; then
  got=$(sha512sum "$work/cli.tar.gz" | cut -d' ' -f1)
else
  got=$(shasum -a 512 "$work/cli.tar.gz" | cut -d' ' -f1)
fi
[ "$got" = "$sha512" ] || { echo "The Antigravity download does not match its sha512" >&2; exit 1; }

# One regular file named antigravity, nothing else.
[ "$(tar -tzf "$work/cli.tar.gz")" = antigravity ] || { echo "The Antigravity archive holds more than its CLI" >&2; exit 1; }
tar -tvzf "$work/cli.tar.gz" | grep -q '^-' || { echo "The Antigravity archive's CLI is not a regular file" >&2; exit 1; }
mkdir -p "$work/out" "$AGY_PREFIX/bin"
tar -xzf "$work/cli.tar.gz" -C "$work/out" antigravity
chmod 755 "$work/out/antigravity"
mv "$work/out/antigravity" "$AGY_PREFIX/bin/agy"
echo "Installed Antigravity $version"
