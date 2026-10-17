#!/bin/sh
# sqlite.sh build DEST | install SRC | check: the SQLite every image runs, at SQLITE_VERSION from docker/versions.env.
#   build    download the amalgamation (refused unless it matches SQLITE_SHA3_256) and compile libsqlite3.so.0 into DEST
#   install  put SRC/libsqlite3.so.0 in place of Debian's copy, the one Python's _sqlite3 links, then check
#   check    fail unless Python's sqlite3 reports SQLITE_VERSION with the features Tico uses
# Debian's library stays diverted (dpkg-divert), so an apt install or upgrade later cannot put the old one back.
set -eu
# shellcheck disable=SC1091
. /tmp/versions.env
case "$1" in
  build)
    dl=$(mktemp -d)
    curl -fsSL --retry 3 -o "$dl/s.zip" "https://www.sqlite.org/$SQLITE_YEAR/sqlite-amalgamation-$SQLITE_ID.zip"
    python3 - "$dl/s.zip" "$SQLITE_SHA3_256" <<'PY'
import hashlib, sys, zipfile
path, want = sys.argv[1:]
with open(path, "rb") as f:
    got = hashlib.sha3_256(f.read()).hexdigest()
if got != want:
    raise SystemExit(f"{path}: sha3-256 {got}, expected {want}")
zipfile.ZipFile(path).extractall(sys.argv[1] + ".d")
PY
    # Debian's feature set (its libsqlite3-0 pragma compile_options), so nothing Tico relies on changes.
    # ENABLE_UPDATE_DELETE_LIMIT is left out: it needs the full source tree, and Tico has no DELETE/UPDATE ... LIMIT.
    mkdir -p "$2"
    gcc -O2 -fPIC -shared -Wl,-soname,libsqlite3.so.0 -o "$2/libsqlite3.so.0" \
      -DSQLITE_THREADSAFE=1 -DSQLITE_USE_URI -DSQLITE_SECURE_DELETE -DSQLITE_SOUNDEX -DSQLITE_LIKE_DOESNT_MATCH_BLOBS \
      -DSQLITE_DEFAULT_RECURSIVE_TRIGGERS -DSQLITE_MAX_SCHEMA_RETRY=25 -DSQLITE_MAX_VARIABLE_NUMBER=250000 \
      -DSQLITE_ENABLE_COLUMN_METADATA -DSQLITE_ENABLE_DBSTAT_VTAB -DSQLITE_ENABLE_MATH_FUNCTIONS \
      -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_FTS3_PARENTHESIS -DSQLITE_ENABLE_FTS3_TOKENIZER \
      -DSQLITE_ENABLE_FTS4 -DSQLITE_ENABLE_FTS5 \
      -DSQLITE_ENABLE_PREUPDATE_HOOK -DSQLITE_ENABLE_RTREE -DSQLITE_ENABLE_SESSION -DSQLITE_ENABLE_STMTVTAB \
      -DSQLITE_ENABLE_UNLOCK_NOTIFY \
      "$dl/s.zip.d/sqlite-amalgamation-$SQLITE_ID/sqlite3.c" -lm ;;
  install)
    ext=$(python -c 'import _sqlite3; print(_sqlite3.__file__)')
    lib=$(readlink -f "$(ldd "$ext" | awk '$1 == "libsqlite3.so.0" { print $3 }')")
    [ -f "$lib" ] || { echo "$ext does not link libsqlite3.so.0" >&2; exit 1; }
    # Debian's copy is kept outside every loader directory: beside the new one it has the same SONAME, and
    # ldconfig (which apt's libc-bin trigger reruns) would link libsqlite3.so.0 back to it.
    mkdir -p /usr/lib/tico-sqlite-debian
    dpkg-divert --quiet --local --rename --divert "/usr/lib/tico-sqlite-debian/${lib##*/}" --add "$lib"
    install -m 0644 "$2/libsqlite3.so.0" "$lib"
    ldconfig
    for found in $(ldconfig -p | awk '$1 == "libsqlite3.so.0" { print $NF }'); do
      [ "$(readlink -f "$found")" = "$lib" ] || { echo "the loader still finds $found" >&2; exit 1; }
    done
    exec sh "$0" check ;;
  check)
    python - "$SQLITE_VERSION" <<'PY'
import sqlite3, sys
want = sys.argv[1]
if sqlite3.sqlite_version != want:
    raise SystemExit(f"Python's sqlite3 is SQLite {sqlite3.sqlite_version}, expected {want}")
c = sqlite3.connect(":memory:")
options = {row[0] for row in c.execute("pragma compile_options")}
missing = {"ENABLE_FTS5", "ENABLE_DBSTAT_VTAB", "ENABLE_MATH_FUNCTIONS", "MAX_VARIABLE_NUMBER=250000"} - options
if missing:
    raise SystemExit(f"SQLite {want} lacks {sorted(missing)}")
print(f"SQLite {want} ok")
PY
    ;;
  *) echo "usage: sqlite.sh build DEST | install SRC | check" >&2; exit 1 ;;
esac
