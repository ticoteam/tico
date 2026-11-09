#!/usr/bin/env bash
# The pinned shell and workflow linters, from their GitHub releases: shellcheck and actionlint. Each archive is checked
# against the SHA-256 below before it runs, and the binary is cached under ${TICO_TOOLS_DIR:-$HOME/.cache/tico/tools},
# so the versions never drift with whatever Homebrew or apt holds. Bump a version only after its release is two weeks old.
# When GitHub's release download fails (a run of 504s from Actions runners, Nov 9), the same version comes from a second
# source: shellcheck from its PyPI wheel (SHA-256 pinned below), actionlint built by `go install` from the Go module proxy
# (checked against sum.golang.org). Bump the wheel pins with the version.
#   scripts/pinned_tool.sh shellcheck [args...]          shellcheck with the arguments given
#   scripts/pinned_tool.sh actionlint [args...]          actionlint; with no arguments it checks .github/workflows
#   scripts/pinned_tool.sh path shellcheck|actionlint    print the verified binary's path (downloads it once)
set -euo pipefail
cd "$(dirname "$0")/.."

SHELLCHECK_VERSION=0.11.0   # released 2025-08-04
ACTIONLINT_VERSION=1.7.12   # released 2026-03-30

# SHA-256 of each release archive, by tool and <os>-<arch> (x86_64 or aarch64 for both tools).
sha_for() {
  case "$1-$2" in
    shellcheck-linux-x86_64) echo 8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198 ;;
    shellcheck-linux-aarch64) echo 12b331c1d2db6b9eb13cfca64306b1b157a86eb69db83023e261eaa7e7c14588 ;;
    shellcheck-darwin-x86_64) echo 3c89db4edcab7cf1c27bff178882e0f6f27f7afdf54e859fa041fca10febe4c6 ;;
    shellcheck-darwin-aarch64) echo 56affdd8de5527894dca6dc3d7e0a99a873b0f004d7aabc30ae407d3f48b0a79 ;;
    actionlint-linux-x86_64) echo 8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 ;;
    actionlint-linux-aarch64) echo 325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6 ;;
    actionlint-darwin-x86_64) echo 5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644 ;;
    actionlint-darwin-aarch64) echo aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f ;;
    *) echo "pinned_tool: no pinned $1 for $2" >&2; return 1 ;;
  esac
}

# The shellcheck-py wheel for SHELLCHECK_VERSION, by <os>-<arch>: "<file name> <SHA-256>". No linux-aarch64 wheel exists.
shellcheck_wheel() {
  case "$1" in
    linux-x86_64) echo "shellcheck_py-$SHELLCHECK_VERSION.1-py2.py3-none-manylinux1_x86_64.manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_5_x86_64.whl 1b274df81de5b000ff78db433e7328b87e52e3c38481c60f8e488c3095beef05" ;;
    darwin-x86_64) echo "shellcheck_py-$SHELLCHECK_VERSION.1-py2.py3-none-macosx_10_9_x86_64.whl b6a3fee28efda2e16e38d6e6d59faf7224300256456639727370d404730849e8" ;;
    darwin-aarch64) echo "shellcheck_py-$SHELLCHECK_VERSION.1-py2.py3-none-macosx_11_0_arm64.whl 6b88d0a244c82ed07e06a53e444da841f69330ca59ae15d4a66c391655dae7a0" ;;
    *) return 1 ;;
  esac
}

fallback() {  # <tool> <plat> <bin>: the same version from the second source, into <bin>; fails when there is none
  local tool=$1 plat=$2 bin=$3 wheel want got
  case $tool in
    shellcheck)
      wheel="$(shellcheck_wheel "$plat")" || return 1
      want=${wheel#* }
      wheel=${wheel%% *}
      echo "pinned_tool: trying the PyPI wheel for shellcheck $SHELLCHECK_VERSION" >&2
      curl -fsSL --retry 3 --retry-delay 5 -o "$TMP/$wheel" "https://files.pythonhosted.org/packages/py2.py3/s/shellcheck-py/$wheel" || return 1
      got="$(sha256_of "$TMP/$wheel")"
      if [ "$got" != "$want" ]; then
        echo "pinned_tool: $wheel has SHA-256 $got, not the pinned $want" >&2
        return 1
      fi
      mkdir -p "$TMP/wheel-shellcheck"
      python3 -m zipfile -e "$TMP/$wheel" "$TMP/wheel-shellcheck" >/dev/null
      chmod 755 "$TMP/wheel-shellcheck/shellcheck_py-$SHELLCHECK_VERSION.1.data/scripts/shellcheck"
      mv -f "$TMP/wheel-shellcheck/shellcheck_py-$SHELLCHECK_VERSION.1.data/scripts/shellcheck" "$bin" ;;
    actionlint)
      # GitHub's hosted images keep Go in the tool cache even when it is not on PATH.
      local go
      go="$(command -v go || find /opt/hostedtoolcache/go -path '*/x64/bin/go' 2>/dev/null | sort -V | tail -1 || true)"
      [ -n "$go" ] && [ -x "$go" ] || return 1
      echo "pinned_tool: building actionlint $ACTIONLINT_VERSION with $go install" >&2
      GOBIN="$TMP/gobin" GOFLAGS='' GOPATH="$TMP/gopath" GOCACHE="$TMP/gocache" "$go" install \
        "github.com/rhysd/actionlint/cmd/actionlint@v$ACTIONLINT_VERSION" || return 1
      mv -f "$TMP/gobin/actionlint" "$bin" ;;
  esac
}

platform() {  # <os>-<arch>, as the release names them
  local os arch
  case "$(uname -s)" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
    *) echo "pinned_tool: unsupported system $(uname -s)" >&2; return 1 ;;
  esac
  case "$(uname -m)" in
    x86_64 | amd64) arch=x86_64 ;;
    aarch64 | arm64) arch=aarch64 ;;
    *) echo "pinned_tool: unsupported machine $(uname -m)" >&2; return 1 ;;
  esac
  echo "$os-$arch"
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

binary() {  # <tool>: the verified binary's path, downloaded and checked on first use
  local tool=$1 plat os arch version archive url member dir bin want got
  plat="$(platform)"
  os=${plat%%-*}
  arch=${plat#*-}
  case $tool in
    shellcheck)
      version=$SHELLCHECK_VERSION
      archive="shellcheck-v$version.$os.$arch.tar.xz"
      url="https://github.com/koalaman/shellcheck/releases/download/v$version/$archive"
      member="shellcheck-v$version/shellcheck" ;;
    actionlint)
      version=$ACTIONLINT_VERSION
      case $arch in x86_64) arch=amd64 ;; aarch64) arch=arm64 ;; esac
      archive="actionlint_${version}_${os}_$arch.tar.gz"
      url="https://github.com/rhysd/actionlint/releases/download/v$version/$archive"
      member="actionlint" ;;
    *) echo "pinned_tool: unknown tool $tool" >&2; return 1 ;;
  esac
  dir="${TICO_TOOLS_DIR:-$HOME/.cache/tico/tools}/$tool-$version"
  bin="$dir/$tool"
  if [ -x "$bin" ]; then echo "$bin"; return 0; fi
  want="$(sha_for "$tool" "$plat")"
  echo "pinned_tool: downloading $tool $version for $plat" >&2
  # Retries cover the transient 5xx answers GitHub gives during a release-asset blip (a 504 run lasted about 7 s, past
  # the old 3 quick retries). A failed download stops here with a message, rather than hashing a missing file.
  if ! curl -fsSL --retry 5 --retry-delay 10 --retry-max-time 240 -o "$TMP/$archive" "$url"; then
    echo "pinned_tool: download of $archive failed after retries: $url" >&2
    mkdir -p "$dir"
    if fallback "$tool" "$plat" "$bin"; then echo "$bin"; return 0; fi
    echo "pinned_tool: no second source for $tool $version on $plat worked" >&2
    return 1
  fi
  got="$(sha256_of "$TMP/$archive")"
  if [ "$got" != "$want" ]; then
    echo "pinned_tool: $archive has SHA-256 $got, not the pinned $want" >&2
    return 1
  fi
  mkdir -p "$TMP/unpacked-$tool" "$dir"
  tar -xf "$TMP/$archive" -C "$TMP/unpacked-$tool" "$member"
  chmod 755 "$TMP/unpacked-$tool/$member"
  mv -f "$TMP/unpacked-$tool/$member" "$bin"
  echo "$bin"
}

case ${1:-} in
  path)
    binary "${2:?usage: pinned_tool.sh path shellcheck|actionlint}" ;;
  shellcheck)
    shift
    bin="$(binary shellcheck)"
    "$bin" "$@" ;;
  actionlint)
    shift
    shellcheck_bin="$(binary shellcheck)"
    bin="$(binary actionlint)"
    # actionlint runs shellcheck on each run: block; the pinned copy goes first on PATH. pyflakes stays off so the
    # result does not depend on what else is installed.
    PATH="$(dirname "$shellcheck_bin"):$PATH"
    export PATH
    "$bin" -pyflakes= "$@" ;;
  *)
    echo "usage: scripts/pinned_tool.sh shellcheck|actionlint [args...] | path shellcheck|actionlint" >&2
    exit 2 ;;
esac
