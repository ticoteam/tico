#!/usr/bin/env bash
# Removes the runner image's unused SDKs (Android, .NET, GHC) only when / has less free space than the job needs.
# Deleting them takes 50-120 seconds of a job's time, and GitHub's ubuntu-24.04 x64 runners now start with about 85 GB
# free (145 GB disk), so on them this is a no-op. A smaller runner (or a future image) still gets the space back.
# Usage: free_disk.sh <gigabytes the job needs free>
set -euo pipefail
need=${1:?gigabytes required}
avail=$(df --output=avail -BG / | tail -n 1 | tr -dc '0-9')
if [ "${avail:-0}" -lt "$need" ]; then
  echo "${avail:-0} GB free on /, under the ${need} GB this job needs: removing unused SDKs"
  sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc
else
  echo "${avail} GB free on /, at least the ${need} GB this job needs: nothing to remove"
fi
df -h /
