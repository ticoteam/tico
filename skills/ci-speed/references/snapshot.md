# Caching installed apt packages as a snapshot

Restoring cached `.deb` files still leaves dpkg the unpacking, about 25 to 45 seconds for a browser's ~100 system
packages. A snapshot stores the files the install left on disk. Restoring it is a `tar -x` (0.6 to 8 seconds) and
`ldconfig`.

## Cache key

Name everything that changes what gets installed:
- the runner image version (`$ImageVersion` on hosted runners)
- the package list
- the lock that fixes the program's version
- the install script itself

The snapshot replaces dpkg's own records (`/var/lib/dpkg/status`). It is only valid on the exact image it was made
on.

## Build it on a miss

```bash
dpkg-query -W -f='${Package}:${Architecture} ${Version}\n' | sort > before
sudo apt-get update
touch marker; sleep 1
sudo apt-get -o Dpkg::Options::=--force-unsafe-io install -y --no-install-recommends "${packages[@]}"
dpkg-query -W -f='${Package}:${Architecture} ${Version}\n' | sort > after
{
  # Every file of each package apt added or upgraded (version in the diff, so upgrades count).
  comm -13 before after | cut -d' ' -f1 | xargs -r dpkg -L
  # What install triggers wrote outside those lists. dpkg keeps packaged mtimes, so select by ctime.
  sudo find /etc /var/lib/dpkg /var/lib/ucf /var/cache/fontconfig /usr/lib/x86_64-linux-gnu /usr/share/glib-2.0 \
    /usr/share/mime /usr/share/icons /usr/share/applications /usr/share/info /usr/lib/locale /usr/share/fonts \
    -xdev -cnewer marker \( -type f -o -type l -o -type d \) -print 2>/dev/null || true
  # update-alternatives links can sit anywhere; each changed record names them (awk once per file).
  sudo find /var/lib/dpkg/alternatives -cnewer marker -type f -exec awk 'NF == 0 {exit} /^\// {print}' {} \; || true
} | sort -u | grep -v -E '^/var/lib/dpkg/(lock|triggers/Lock)' > candidates || true
while IFS= read -r p; do if [ -e "$p" ] || [ -L "$p" ]; then printf '%s\n' "$p"; fi; done < candidates > files
sudo tar --no-recursion -cf installed.tar -T files   # plain tar: the cache compresses it
```

Building the list takes about 2.5 seconds. A ctime scan of all of `/usr` finds the same files but takes 104 to
180 seconds.

## Restore it on a hit

```bash
sudo tar -xpf installed.tar -C /
sudo ldconfig
```

## Check it once, before relying on it

- **Compare the list with a full scan** (`find /usr /etc /var/lib/dpkg ... -cnewer marker`) in one throwaway run.
  Only dpkg's lock files should differ. The first attempt missed two things, both fixed above:
  - X11 font indexes under `/usr/share/fonts`
  - alternatives links (a default font, a sound font)
- **On fresh runners that restored it**, check that:
  - `sudo dpkg --audit` and `sudo apt-get check` are clean
  - every listed package shows `ii` in `dpkg-query -W -f='${db:Status-Abbrev} ${Package}\n'`
  - the real program runs. For browsers, launch each one and take a screenshot of a page with CJK text, an emoji and
    a `<video>`.
