#!/usr/bin/env bash
# The pinned shell and workflow linters, from their GitHub releases: shellcheck and actionlint. Each archive is checked
# against the SHA-256 below before it is unpacked, and the binary is cached under ${TICO_TOOLS_DIR:-$HOME/.cache/tico/tools},
# so the versions never drift with whatever Homebrew or apt holds. Bump a version only after its release is two weeks old.
# The verified archives are kept in its downloads/ folder, which CI caches (pr-checks.yml static checks, warmed by
# pr-cache.yml, keyed on this file's hash and so on these versions and SHA-256s): a run unpacks the kept archive after
# checking it again, and downloads only when it is missing or does not match.
#   scripts/pinned_tool.sh shellcheck [args...]          shellcheck with the arguments given
#   scripts/pinned_tool.sh actionlint [args...]          actionlint; with no arguments it checks .github/workflows
#   scripts/pinned_tool.sh path shellcheck|actionlint    print the verified binary's path (downloads it once)
#   scripts/pinned_tool.sh fetch shellcheck|actionlint...  keep the verified archives in downloads/ (CI's cache warm-up)
set -euo pipefail
# A failure inside $(...) stops the script there, not at a later, misleading check (bash 4.4+; macOS's bash 3.2 lacks it,
# and the checks below do not rely on it).
shopt -s inherit_errexit 2>/dev/null || true
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

tools_dir() { echo "${TICO_TOOLS_DIR:-$HOME/.cache/tico/tools}"; }

archive_of() {  # <tool>: "<version> <archive file name> <url> <member>" for this machine
  local tool=$1 plat os arch version archive url member
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
  echo "$version $archive $url $member"
}

verified_archive() {  # <tool>: the path of its archive in downloads/, checked against the pinned SHA-256 (fetched if needed)
  local tool=$1 plat info version archive url member want got kept
  plat="$(platform)"
  info="$(archive_of "$tool")"
  read -r version archive url member <<<"$info"
  want="$(sha_for "$tool" "$plat")"
  kept="$(tools_dir)/downloads/$archive"
  if [ -f "$kept" ]; then
    got="$(sha256_of "$kept")"
    if [ "$got" = "$want" ]; then echo "$kept"; return 0; fi
    echo "pinned_tool: the kept $archive has SHA-256 $got, not the pinned $want; downloading it again" >&2
    rm -f "$kept"
  fi
  echo "pinned_tool: downloading $tool $version for $plat" >&2
  if ! curl -fsSL --retry 3 -o "$TMP/$archive" "$url"; then
    echo "pinned_tool: could not download $url" >&2
    return 1
  fi
  got="$(sha256_of "$TMP/$archive")"
  if [ "$got" != "$want" ]; then
    echo "pinned_tool: $archive has SHA-256 $got, not the pinned $want" >&2
    return 1
  fi
  mkdir -p "$(dirname "$kept")"
  mv -f "$TMP/$archive" "$kept"
  echo "$kept"
}

binary() {  # <tool>: the verified binary's path, unpacked from the verified archive on first use
  local tool=$1 info version archive url member dir bin kept
  info="$(archive_of "$tool")"
  read -r version archive url member <<<"$info"
  dir="$(tools_dir)/$tool-$version"
  bin="$dir/$tool"
  if [ -x "$bin" ]; then echo "$bin"; return 0; fi
  kept="$(verified_archive "$tool")"
  mkdir -p "$TMP/unpacked-$tool" "$dir"
  tar -xf "$kept" -C "$TMP/unpacked-$tool" "$member"
  chmod 755 "$TMP/unpacked-$tool/$member"
  mv -f "$TMP/unpacked-$tool/$member" "$bin"
  echo "$bin"
}

case ${1:-} in
  path)
    binary "${2:?usage: pinned_tool.sh path shellcheck|actionlint}" ;;
  fetch)
    shift
    [ "$#" -gt 0 ] || { echo "usage: scripts/pinned_tool.sh fetch shellcheck|actionlint..." >&2; exit 2; }
    for tool in "$@"; do verified_archive "$tool" >/dev/null; done ;;
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
    echo "usage: scripts/pinned_tool.sh shellcheck|actionlint [args...] | path shellcheck|actionlint | fetch shellcheck|actionlint..." >&2
    exit 2 ;;
esac
