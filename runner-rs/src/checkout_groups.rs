//! Per-checkout groups on the Docker runner. Every bot's files are group-shared with its turn users (the pool's
//! group), so the group alone would let one leased turn into every bot's checkout. Each checkout and worktree root
//! instead gets a group of its own (owner `bot`, mode 0770, plus an ACL entry for the supervisor's user), and a leased turn user is given only the groups of the
//! paths its turn runs in (crate::isolation::prefix_for). Below a root nothing changes: the pool's group still lets
//! that turn write the bot's files once it is inside.
//!
//! Not covered (SECURITY.md): turns on harnesses without `isolated_user` run as `bot`, the owner of every checkout,
//! as do watchers, worktree setup commands and the runner's own git; shared base clones (`repos/<base>`) stay open to
//! the pool's group, since every computer task worktree of that base needs them.
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::os::fd::AsRawFd;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use crate::isolation::{open_at, pinned_parent};

/// Group ids above the image's users and pool, below the 65534 `nogroup`.
const FIRST: u32 = 20000;
const LAST: u32 = 59999;
const FILE: &str = "turn-groups.json";

struct Groups {
    file: Option<PathBuf>,
    /// Checkout root path to its group. Kept across restarts so a root keeps its group and no two roots share one.
    by_path: BTreeMap<String, u32>,
}

static GROUPS: OnceLock<Mutex<Groups>> = OnceLock::new();
/// A leased turn user's checkout groups, read whenever a command is started as that user.
static GRANTS: OnceLock<Mutex<HashMap<u32, BTreeSet<u32>>>> = OnceLock::new();

fn groups() -> &'static Mutex<Groups> {
    GROUPS.get_or_init(|| Mutex::new(Groups { file: None, by_path: BTreeMap::new() }))
}

fn grants() -> &'static Mutex<HashMap<u32, BTreeSet<u32>>> {
    GRANTS.get_or_init(Default::default)
}

/// Load the group map from the supervisor's state directory (closed to bot code).
pub fn initialize(state_dir: &Path) {
    let file = state_dir.join(FILE);
    let by_path = std::fs::read_to_string(&file).ok()
        .and_then(|text| serde_json::from_str::<BTreeMap<String, u32>>(&text).ok())
        .unwrap_or_default()
        .into_iter().filter(|(_, g)| (FIRST..=LAST).contains(g)).collect();
    let mut g = groups().lock().unwrap();
    g.file = Some(file);
    g.by_path = by_path;
}

/// Whether roots are gated here: only where turns can run as leased users.
fn wanted() -> bool {
    crate::isolation::enabled() && crate::turn_users::available()
}

/// The supplementary groups `uid` runs with (none for `bot` or any user without a grant).
pub fn granted(uid: u32) -> Vec<u32> {
    grants().lock().unwrap().get(&uid).map(|s| s.iter().copied().collect()).unwrap_or_default()
}

/// Let a leased turn user into these checkout roots, for its commands from now on.
pub fn grant(uid: u32, gids: &[u32]) {
    if gids.is_empty() { return; }
    grants().lock().unwrap().entry(uid).or_default().extend(gids);
}

/// Before the UID can be lent to another turn.
pub fn revoke(uid: u32) {
    grants().lock().unwrap().remove(&uid);
}

/// Gate each path that is a bot-owned checkout or worktree root and return the groups now on those roots.
/// A path that is not one (a missing directory, the workspace itself, a supervisor-owned folder) is left alone.
pub async fn gate(paths: Vec<PathBuf>) -> Vec<u32> {
    if !wanted() { return vec![]; }
    tokio::task::spawn_blocking(move || gate_blocking(&paths)).await.unwrap_or_default()
}

pub fn gate_blocking(paths: &[PathBuf]) -> Vec<u32> {
    let Some((bot, _)) = crate::isolation::identity() else { return vec![] };
    let mut out = BTreeSet::new();
    for path in paths.iter().collect::<BTreeSet<_>>() {
        match gate_one(path, bot, false) {
            Ok(Some(gid)) => { out.insert(gid); }
            Ok(None) => {}
            Err(e) => crate::util::log(&format!("Tico runner: cannot give {} its own group: {e}", path.display())),
        }
    }
    out.into_iter().collect()
}

/// The workspace's checkouts (`<ws>/<name>`), task-lane worktrees (`<ws>/sessions/<bot>/<task>`) and computer
/// task worktrees (`<ws>/tasks/<task>/<repository>`), so a checkout no turn has run in yet is closed to leased turns too.
pub fn gate_workspace(workspace: &Path) {
    if !wanted() { return; }
    let children = |dir: &Path| -> Vec<PathBuf> {
        std::fs::read_dir(dir).map(|r| r.flatten().map(|e| e.path()).collect()).unwrap_or_default()
    };
    let mut roots = children(workspace);
    for lane in children(&workspace.join("sessions")) { roots.extend(children(&lane)); }
    // A computer task's worktrees sit one level inside its folder (`tasks/<task>/<repository>`).
    for task in children(&workspace.join("tasks")) { roots.extend(children(&task)); }
    let Some((bot, _)) = crate::isolation::identity() else { return };
    // The folders that hold other bots' checkouts are writable by the pool's group, so without the sticky bit a
    // leased turn could rename a sibling's checkout aside and put its own in its place.
    let mut holders = vec![workspace.to_path_buf(), workspace.join("sessions"), workspace.join("tasks"), workspace.join("repos")];
    holders.extend(children(&workspace.join("sessions")));
    for dir in holders {
        if let Err(e) = seal(&dir, bot) {
            crate::util::log(&format!("Tico runner: cannot keep turns from renaming entries in {}: {e}", dir.display()));
        }
    }
    gate_blocking(&roots);
}

/// Set the sticky bit on a group-writable folder, so only an entry's owner (or the folder's) can rename or remove it.
fn seal(dir: &Path, bot: u32) -> std::io::Result<()> {
    let Some((parent, name)) = pinned_parent(dir) else { return Ok(()) };
    let Some(fd) = open_at(parent.as_raw_fd(), &name, libc::O_RDONLY | libc::O_DIRECTORY) else { return Ok(()) };
    let info = stat(fd.as_raw_fd())?;
    let mode = u32::from(info.st_mode) & 0o7777;
    if mode & 0o020 == 0 || mode & 0o1000 != 0 { return Ok(()); }
    // SAFETY: geteuid has no preconditions.
    let me = unsafe { libc::geteuid() };
    if info.st_uid != me && info.st_uid != bot { return Ok(()); }
    let fd = fd.as_raw_fd();
    // As in gate_one: a mode change on the bot's folder borrows ownership on the pinned descriptor.
    // SAFETY: fchown and fchmod on the pinned descriptor.
    if info.st_uid != me && unsafe { libc::fchown(fd, me, info.st_gid) } != 0 { return Err(std::io::Error::last_os_error()); }
    let changed = if unsafe { libc::fchmod(fd, (mode | 0o1000) as libc::mode_t) } != 0 { Err(std::io::Error::last_os_error()) } else { Ok(()) };
    // SAFETY: as above.
    if info.st_uid != me && unsafe { libc::fchown(fd, info.st_uid, info.st_gid) } != 0 { return Err(std::io::Error::last_os_error()); }
    changed
}

/// Make the folder a new worktree or checkout is about to fill, already gated, so the bot's files are never open to
/// the pool's group (a leased turn could otherwise read them, or hard-link them away, while git writes them).
pub async fn prepare(path: PathBuf) {
    if !wanted() { return; }
    let Some((bot, _)) = crate::isolation::identity() else { return };
    let shown = path.display().to_string();
    let made = tokio::task::spawn_blocking(move || {
        crate::isolation::mkdir(&path, 0o700)?;
        gate_one(&path, bot, true)
    }).await;
    match made {
        Ok(Ok(_)) => {}
        Ok(Err(e)) => crate::util::log(&format!("Tico runner: cannot give {shown} its own group before filling it: {e}")),
        Err(e) => crate::util::log(&format!("Tico runner: cannot give {shown} its own group before filling it: {e}")),
    }
}

/// `fresh` also takes an empty folder that has no `.git` yet (prepare).
fn gate_one(path: &Path, bot: u32, fresh: bool) -> std::io::Result<Option<u32>> {
    let Some((parent, name)) = pinned_parent(path) else { return Ok(None) };
    let Some(fd) = open_at(parent.as_raw_fd(), &name, libc::O_RDONLY | libc::O_DIRECTORY) else { return Ok(None) };
    let info = stat(fd.as_raw_fd())?;
    // A checkout or worktree root holds `.git` (a directory, or a file naming the main checkout's).
    let dot_git = std::ffi::CString::new(".git").unwrap();
    let mut probe = std::mem::MaybeUninit::<libc::stat>::uninit();
    // SAFETY: fstatat on a descriptor this process owns, without following a link.
    let checkout = unsafe { libc::fstatat(fd.as_raw_fd(), dot_git.as_ptr(), probe.as_mut_ptr(), libc::AT_SYMLINK_NOFOLLOW) } == 0
        || (fresh && std::fs::read_dir(format!("/proc/self/fd/{}", fd.as_raw_fd())).is_ok_and(|mut d| d.next().is_none()));
    // SAFETY: geteuid has no preconditions.
    let me = unsafe { libc::geteuid() };
    // A root this runner was gating when it stopped is still the supervisor's, with a checkout group: finish it.
    let interrupted = info.st_uid == me && (FIRST..=LAST).contains(&info.st_gid);
    if !(info.st_uid == bot || interrupted) || !checkout { return Ok(None); }
    // Spelled one way however the caller wrote it. Never resolved through a link: the open above refuses one, so a
    // bot that swaps its checkout for a link to another's cannot get that checkout's group for its turn.
    let key = path.components().collect::<PathBuf>().display().to_string();
    // Held through the ownership change too, so two turns starting in one checkout never interleave it.
    let mut g = groups().lock().unwrap();
    let current = (FIRST..=LAST).contains(&info.st_gid).then_some(info.st_gid);
    let holder = |gid: u32| g.by_path.iter().find(|(_, v)| **v == gid).map(|(k, _)| k.clone());
    // The group already on the root stays (a running turn holds it) unless another root has it in the map.
    let gid = match current.filter(|c| holder(*c).is_none_or(|k| k == key)) {
        Some(c) => c,
        None => match g.by_path.get(&key) {
            Some(gid) => *gid,
            None => {
                let used: BTreeSet<u32> = g.by_path.values().copied().collect();
                (FIRST..=LAST).find(|c| !used.contains(c) && !system_group(*c))
                    .ok_or_else(|| std::io::Error::other("no free checkout group"))?
            }
        },
    };
    if g.by_path.get(&key) != Some(&gid) {
        g.by_path.insert(key, gid);
        save(&g)?;
    }
    let mode = u32::from(info.st_mode) & 0o7777;
    let wanted_mode = (mode & 0o7000) | 0o770;
    let acl = supervisor_acl(me);
    let fd = fd.as_raw_fd();
    if mode == wanted_mode && !interrupted && read_acl(fd).as_deref() == Some(acl.as_slice()) {
        // SAFETY: fchown on the pinned descriptor.
        if info.st_gid != gid && unsafe { libc::fchown(fd, bot, gid) } != 0 { return Err(std::io::Error::last_os_error()); }
        return Ok(Some(gid));
    }
    // The supervisor may change any owner (CAP_CHOWN) but only its own inode's mode and ACL (no CAP_FOWNER), so the
    // change borrows ownership on the pinned descriptor and hands it back.
    // SAFETY: fchown and fchmod on the pinned descriptor.
    if unsafe { libc::fchown(fd, me, gid) } != 0 { return Err(std::io::Error::last_os_error()); }
    let changed = write_acl(fd, &acl).and_then(|()| {
        // SAFETY: as above.
        if unsafe { libc::fchmod(fd, wanted_mode as libc::mode_t) } != 0 { Err(std::io::Error::last_os_error()) } else { Ok(()) }
    });
    // Without the ACL the root keeps the shared group, which the supervisor's own git needs (below).
    let group = if changed.is_ok() { gid } else { info.st_gid };
    // SAFETY: as above.
    if unsafe { libc::fchown(fd, bot, group) } != 0 { return Err(std::io::Error::last_os_error()); }
    changed.map(|()| Some(gid))
}

/// The root's ACL: its owner, the supervisor's user and its group get everything, others nothing. The supervisor
/// runs git in checkouts as itself (crate::gitrun), and git checks with access(2), which ignores the supervisor's
/// capabilities: it reaches the checkout through this entry, as it did through the shared group, and a turn user
/// (another UID with the same group) gains nothing from it.
fn supervisor_acl(supervisor: u32) -> Vec<u8> {
    const UNDEFINED: u32 = u32::MAX;
    let mut out = 2u32.to_le_bytes().to_vec();
    // Sorted by tag as the kernel requires: owner, named user, owning group, mask, other.
    for (tag, perm, id) in [(0x01u16, 7u16, UNDEFINED), (0x02, 7, supervisor), (0x04, 7, UNDEFINED), (0x10, 7, UNDEFINED), (0x20, 0, UNDEFINED)] {
        out.extend(tag.to_le_bytes());
        out.extend(perm.to_le_bytes());
        out.extend(id.to_le_bytes());
    }
    out
}

const ACL_NAME: &std::ffi::CStr = c"system.posix_acl_access";

#[cfg(target_os = "linux")]
fn read_acl(fd: i32) -> Option<Vec<u8>> {
    let mut buf = vec![0u8; 256];
    // SAFETY: the buffer is as long as the length passed.
    let n = unsafe { libc::fgetxattr(fd, ACL_NAME.as_ptr(), buf.as_mut_ptr().cast(), buf.len()) };
    (n >= 0).then(|| { buf.truncate(n as usize); buf })
}

#[cfg(target_os = "linux")]
fn write_acl(fd: i32, acl: &[u8]) -> std::io::Result<()> {
    // SAFETY: the value is as long as the length passed.
    if unsafe { libc::fsetxattr(fd, ACL_NAME.as_ptr(), acl.as_ptr().cast(), acl.len(), 0) } != 0 {
        return Err(std::io::Error::other(format!("no ACL on this filesystem, so the checkout stays shared: {}", std::io::Error::last_os_error())));
    }
    Ok(())
}

#[cfg(not(target_os = "linux"))]
fn read_acl(_: i32) -> Option<Vec<u8>> { None }

#[cfg(not(target_os = "linux"))]
fn write_acl(_: i32, _: &[u8]) -> std::io::Result<()> { Err(std::io::Error::other("checkout groups need Linux")) }

fn stat(fd: i32) -> std::io::Result<libc::stat> {
    let mut raw = std::mem::MaybeUninit::<libc::stat>::uninit();
    // SAFETY: fstat on a descriptor this process owns.
    if unsafe { libc::fstat(fd, raw.as_mut_ptr()) } != 0 { return Err(std::io::Error::last_os_error()); }
    Ok(unsafe { raw.assume_init() })
}

/// A group the image already names (its members would get into the checkout).
fn system_group(gid: u32) -> bool {
    // SAFETY: getgrgid reads the group database; only the null check is used.
    !unsafe { libc::getgrgid(gid) }.is_null()
}

fn save(g: &Groups) -> std::io::Result<()> {
    let Some(file) = &g.file else { return Ok(()) };
    let temporary = file.with_extension("json.tmp");
    {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let mut out = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&temporary)?;
        out.write_all(serde_json::to_string(&g.by_path).map_err(std::io::Error::other)?.as_bytes())?;
        out.sync_all()?;
    }
    std::fs::rename(&temporary, file)
}
