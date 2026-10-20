#!/bin/sh
# The producer changes a directory-mounted file. A private, validated snapshot belongs to one child only.
set -u
config=${TICO_CADDY_CONFIG:-/generated/caddy.json}
state=${TICO_CADDY_STATE_DIR:-/tmp/tico-caddy}
binary=${TICO_CADDY_BIN:-/usr/bin/caddy}
port=${TICO_CADDY_READY_PORT:-20203}
stop_ticks=${TICO_CADDY_STOP_TICKS:-5}
ready_ticks=${TICO_CADDY_READY_TICKS:-10}
validate_ticks=${TICO_CADDY_VALIDATE_TICKS:-10}
child='' validator='' digest='' generation=''

for path in "$config" "$state" "$binary"; do
    case "$path" in /*) ;; *) exit 64 ;; esac
    case "$path" in *[!A-Za-z0-9_./-]*|*/../*|*/./*) exit 64 ;; esac
done
for number in "$port" "$stop_ticks" "$ready_ticks" "$validate_ticks"; do
    case "$number" in ''|*[!0-9]*) exit 64 ;; esac
done
[ "$port" -ge 1024 ] && [ "$port" -le 65535 ] || exit 64
for number in "$stop_ticks" "$ready_ticks" "$validate_ticks"; do
    [ "$number" -ge 1 ] && [ "$number" -le 30 ] || exit 64
done
url=http://127.0.0.1:$port/__tico_caddy_ready

file_digest() {
    [ -f "$1" ] && [ ! -L "$1" ] && [ -r "$1" ] || return 1
    # A valid snapshot is at most 256 KiB; an oversized replacement cannot match its digest.
    _sum=$(head -c 262145 "$1" 2>/dev/null | sha256sum 2>/dev/null) || return 1
    _sum=${_sum%% *}
    [ "${#_sum}" -eq 64 ] || return 1
    case "$_sum" in *[!0-9a-f]*) return 1 ;; esac
    printf '%s' "$_sum"
}

marker_generation() {
    # The renderer embeds exactly one literal marker, in the private readiness response.
    _markers=$(grep -Eo '"tico-caddy-generation:[0-9a-f]{64}"' "$1" 2>/dev/null) || return 1
    [ "${#_markers}" -eq 88 ] || return 1
    _markers=${_markers#\"tico-caddy-generation:}
    printf '%s' "${_markers%\"}"
}

ready() {
    # Hash the complete response bytes, including any newline/NUL. No URL/body is logged.
    _want=$(printf '%s|exit:0' "tico-caddy-generation:$1" | sha256sum)
    _want=${_want%% *}
    _got=$({ timeout -s KILL 1 wget -q -T 1 -O - "$url" 2>/dev/null; printf '|exit:%s' "$?"; } \
        | head -c 128 | sha256sum)
    _got=${_got%% *}
    [ "$_got" = "$_want" ]
}

owned() {
    kill -0 "$1" 2>/dev/null && kill -0 "$2" 2>/dev/null || return 1
    if [ -r "/proc/$1/stat" ]; then
        IFS= read -r _stat < "/proc/$1/stat" || return 1
        _stat=${_stat##*) }
        _stat=${_stat#* }
        _parent=${_stat%% *}
    elif [ "$(uname -s)" = Darwin ]; then
        # The actual Darwin Caddy test binary has no procfs; the Linux image uses the branch above.
        _parent=$(ps -o ppid= -p "$1" 2>/dev/null | tr -d '[:space:]')
    else
        return 1
    fi
    [ "$_parent" = "$2" ]
}

health() {
    [ -f "$state/applied" ] && [ ! -L "$state/applied" ] || return 1
    _proof=$(cat "$state/applied") || return 1
    _fields=$(printf '%s\n' "$_proof" | sed -n \
        's/^{"file_sha256":"\([0-9a-f]\{64\}\)","generation":"\([0-9a-f]\{64\}\)","pid":\([0-9][0-9]*\),"supervisor_pid":\([0-9][0-9]*\)}$/\1 \2 \3 \4/p')
    [ -n "$_fields" ] || return 1
    # Only the four hex/decimal fields matched above are intentionally split.
    # shellcheck disable=SC2086
    set -- $_fields
    [ "$#" -eq 4 ] && [ "$3" -gt 1 ] && [ "$4" -gt 0 ] || return 1
    [ "$(file_digest "$config")" = "$1" ] && [ "$(marker_generation "$config")" = "$2" ] || return 1
    owned "$3" "$4" && ready "$2"
}

case "${1:-}" in
    health) [ "$#" -eq 1 ] || exit 64; health; exit $? ;;
    '') [ "$#" -eq 0 ] || exit 64 ;;
    *) exit 64 ;;
esac

umask 077
[ ! -L "$state" ] && mkdir -p "$state" && chmod 700 "$state" || exit 1
stop_pid() {
    _stop_pid=$1
    [ -n "$_stop_pid" ] || return 0
    if owned "$_stop_pid" "$$"; then kill -TERM "$_stop_pid" 2>/dev/null || :; fi
    _ticks=0
    while owned "$_stop_pid" "$$" && [ "$_ticks" -lt "$stop_ticks" ]; do
        sleep 1
        _ticks=$((_ticks + 1))
    done
    if owned "$_stop_pid" "$$"; then kill -KILL "$_stop_pid" 2>/dev/null || :; fi
    wait "$_stop_pid" 2>/dev/null || :
}

cleanup() {
    rm -f "$state/applied" "$state/applied.$$"
    stop_pid "$validator"; validator=
    stop_pid "$child"; child=
}
shutdown() { trap '' TERM INT HUP; cleanup; exit 0; }
trap cleanup 0
trap shutdown TERM INT HUP
rm -f "$state/applied"
[ -x "$binary" ] || exit 1
for tool in sha256sum wget timeout head grep sed wc tr; do command -v "$tool" >/dev/null 2>&1 || exit 1; done

failure() {
    # Retain the last safe failure even after a later successful generation. Raw validator output is discarded.
    printf '{"phase":"%s","rc":%s,"file_sha256":"%s","generation":"%s","output_sha256":"%s"}\n' \
        "$1" "$2" "$digest" "$generation" "${3:-}" > "$state/failure.$$"
    mv -f "$state/failure.$$" "$state/last-failure"
    printf 'tico-caddy: %s failed (rc %s)\n' "$1" "$2" >&2
}

seen=
while :; do
    if [ -n "$child" ] && ! kill -0 "$child" 2>/dev/null; then
        rm -f "$state/applied"
        if wait "$child"; then code=0; else code=$?; fi
        child=
        failure child_exit "$code"
        exit 1
    fi
    desired=$(file_digest "$config") || desired=missing
    if [ "$desired" != "$seen" ]; then
        # Removal, invalid replacement and optional-site changes all retire the old listener first.
        rm -f "$state/applied"
        stop_pid "$child"; child=
        digest='' generation=''
        seen=$desired
        if [ "$desired" = missing ]; then
            failure missing_config 1
            sleep 1
            continue
        fi
        digest=$desired
        if ! head -c 262145 "$config" > "$state/candidate.$$" 2>/dev/null; then
            failure snapshot 1
            seen=
            sleep 1
            continue
        fi
        size=$(wc -c < "$state/candidate.$$" | tr -d '[:space:]')
        if [ "$size" -gt 262144 ]; then
            failure config_size 1
            rm -f "$state/candidate.$$"
            continue
        fi
        if [ "$(file_digest "$state/candidate.$$")" != "$digest" ] \
                || [ "$(file_digest "$config")" != "$digest" ]; then
            failure unstable_snapshot 1
            rm -f "$state/candidate.$$"
            seen=
            sleep 1
            continue
        fi
        generation=$(marker_generation "$state/candidate.$$") || generation=
        if [ "${#generation}" -ne 64 ]; then
            failure generation_marker 1
            rm -f "$state/candidate.$$"
            sleep 1
            continue
        fi
        "$binary" validate --config "$state/candidate.$$" > "$state/validation.$$" 2>&1 &
        validator=$!
        ticks=0
        while kill -0 "$validator" 2>/dev/null && [ "$ticks" -lt "$validate_ticks" ]; do
            sleep 1
            ticks=$((ticks + 1))
        done
        if kill -0 "$validator" 2>/dev/null; then
            stop_pid "$validator"
            code=124
        elif wait "$validator"; then code=0; else code=$?; fi
        validator=
        output=$(file_digest "$state/validation.$$") || output=
        rm -f "$state/validation.$$"
        if [ "$code" -ne 0 ]; then
            failure validate "$code" "$output"
            rm -f "$state/candidate.$$"
            continue
        fi
        if [ "$(file_digest "$config")" != "$digest" ]; then
            failure superseded 1
            seen=
            continue
        fi
        mv -f "$state/candidate.$$" "$state/active.json" || exit 1
        "$binary" run --config "$state/active.json" &
        child=$!
        ticks=0
        applied=0
        while [ "$ticks" -lt "$ready_ticks" ] && kill -0 "$child" 2>/dev/null; do
            [ "$(file_digest "$config")" = "$digest" ] || break
            if ready "$generation" && owned "$child" "$$"; then
                applied=1
                break
            fi
            sleep 1
            ticks=$((ticks + 1))
        done
        if [ "$applied" -eq 1 ] && [ "$(file_digest "$config")" = "$digest" ] && owned "$child" "$$"; then
            printf '{"file_sha256":"%s","generation":"%s","pid":%s,"supervisor_pid":%s}\n' \
                "$digest" "$generation" "$child" "$$" > "$state/applied.$$"
            mv -f "$state/applied.$$" "$state/applied" || exit 1
        else
            failure readiness 1
            stop_pid "$child"; child=
            if [ "$(file_digest "$config")" != "$digest" ]; then seen=; fi
        fi
    fi
    sleep 1
done
