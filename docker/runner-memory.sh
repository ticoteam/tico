#!/bin/sh
# The default memory cap of a runner box, in the form TICO_RUNNER_MEMORY takes ("27g"), or nothing when there is none.
# The cap is the host's memory less a reserve for the operating system, sshd, Docker and the bots' own spikes: the larger
# of 3 GiB and 12% of the total, rounded down to whole GiB. A host that would be left with less than 1 GiB gets no cap.
# scripts/install.sh runs this from the release bundle, and docker/updater.py runs the copy in its image.
# Inside a container, /proc/meminfo reports the host's total (Docker does not change it without LXCFS), so both see the same number.
meminfo=${1:-/proc/meminfo}
kb=$(awk '/^MemTotal:/ {print $2; exit}' "$meminfo" 2>/dev/null)
case "$kb" in ''|*[!0-9]*) exit 0 ;; esac
GIB=1073741824
total=$((kb * 1024))
reserve=$((total * 12 / 100))
[ "$reserve" -ge $((3 * GIB)) ] || reserve=$((3 * GIB))
gib=$(( (total - reserve) / GIB ))
[ "$gib" -ge 1 ] || exit 0
echo "${gib}g"
