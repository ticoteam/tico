//! Bot code as another user (runner/isolation.py): on the Docker runner the supervisor keeps runner.json and
//! its state to itself and starts every process that runs bot-controlled code (the model CLI, git in a bot's
//! checkout, a watcher, any runtime probe) as `TICO_RUNNER_BOT_UID` with no capabilities, through `setpriv`.
//! Off wherever that variable is unset (a Mac is a trust group of one user).

use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::path::{Path, PathBuf};

pub const UID_ENV: &str = "TICO_RUNNER_BOT_UID";
pub const GID_ENV: &str = "TICO_RUNNER_BOT_GID";
pub const SETPRIV: &str = "/usr/bin/setpriv";

/// (uid, gid) bot code runs as, or None when this process is not separating them.
pub fn identity() -> Option<(u32, u32)> {
    // SAFETY: geteuid has no preconditions.
    identity_from(std::env::var(UID_ENV).ok(), std::env::var(GID_ENV).ok(), unsafe { libc::geteuid() })
}

fn identity_from(uid: Option<String>, gid: Option<String>, euid: u32) -> Option<(u32, u32)> {
    let uid: i64 = uid?.trim().parse().ok()?;
    let gid: i64 = match gid {
        Some(g) if !g.is_empty() => g.trim().parse().ok()?,
        _ => uid,
    };
    (uid > 0 && uid != euid as i64 && gid >= 0 && uid <= u32::MAX as i64 && gid <= u32::MAX as i64).then_some((uid as u32, gid as u32))
}

pub fn enabled() -> bool {
    identity().is_some()
}

fn prefix_for((uid, gid): (u32, u32)) -> Vec<String> {
    // A leased turn user also gets the groups of the checkouts its turn runs in (crate::checkout_groups); every other
    // identity runs with none.
    let extra = crate::checkout_groups::granted(uid);
    let groups = if extra.is_empty() {
        "--clear-groups".into()
    } else {
        format!("--groups={}", std::iter::once(gid).chain(extra).map(|g| g.to_string()).collect::<Vec<_>>().join(","))
    };
    vec![
        SETPRIV.into(),
        format!("--reuid={uid}"),
        format!("--regid={gid}"),
        groups,
        "--inh-caps=-all".into(),
        "--ambient-caps=-all".into(),
    ]
}

fn umask_group() -> std::io::Result<()> {
    // SAFETY: umask is async-signal-safe and only changes this (forked) process's mask.
    unsafe { libc::umask(0o002) };
    Ok(())
}

/// A command that runs `program` as the bot user when isolation is on (with umask 002: the bot's files are
/// group-writable), else as itself. Arguments added later follow the program.
pub fn command(program: impl AsRef<std::ffi::OsStr>) -> tokio::process::Command {
    command_as(identity(), program)
}

/// An explicitly leased identity; None preserves the ordinary bot identity.
pub fn command_as(id: Option<(u32, u32)>, program: impl AsRef<std::ffi::OsStr>) -> tokio::process::Command {
    match id.or_else(identity).map(prefix_for) {
        None => tokio::process::Command::new(program),
        Some(p) => {
            let mut cmd = tokio::process::Command::new(&p[0]);
            cmd.args(&p[1..]).arg(program);
            // SAFETY: the closure only calls umask.
            unsafe { cmd.pre_exec(umask_group) };
            cmd
        }
    }
}

/// `command` run by `parent` (a program and its first arguments), which execs the rest: the bot-user switch, when
/// isolation is on, happens below the parent, and kills the program when the parent dies (setpriv `--pdeathsig`).
pub fn command_under_as(id: Option<(u32, u32)>, parent: &[std::ffi::OsString], program: impl AsRef<std::ffi::OsStr>) -> tokio::process::Command {
    let mut cmd = tokio::process::Command::new(&parent[0]);
    cmd.args(&parent[1..]);
    if let Some(p) = id.or_else(identity).map(prefix_for) {
        cmd.args(&p).args(["--pdeathsig", "KILL"]);
        // SAFETY: the closure only calls umask.
        unsafe { cmd.pre_exec(umask_group) };
    }
    cmd.arg(program);
    cmd
}

/// `command` for std's blocking Command.
pub fn std_command(program: impl AsRef<std::ffi::OsStr>) -> std::process::Command {
    std_command_as(identity(), program)
}

pub fn std_command_as(id: Option<(u32, u32)>, program: impl AsRef<std::ffi::OsStr>) -> std::process::Command {
    use std::os::unix::process::CommandExt;
    match id.or_else(identity).map(prefix_for) {
        None => std::process::Command::new(program),
        Some(p) => {
            let mut cmd = std::process::Command::new(&p[0]);
            cmd.args(&p[1..]).arg(program);
            // SAFETY: the closure only calls umask.
            unsafe { cmd.pre_exec(umask_group) };
            cmd
        }
    }
}

fn lchown(path: &Path, uid: u32, gid: u32) -> std::io::Result<()> {
    std::os::unix::fs::lchown(path, Some(uid), Some(gid))
}

/// Hand a file the supervisor made to the bot user. A no-op when isolation is off.
pub fn chown(path: &Path) {
    if let Some((uid, gid)) = identity() {
        let _ = lchown(path, uid, gid);
    }
}

/// Hand the opened inode over without replacing a lock or following a swapped path.
pub fn chown_file(file: &std::fs::File) -> std::io::Result<()> {
    use std::os::fd::AsRawFd;
    if let Some((uid, gid)) = identity() {
        // SAFETY: fchown on a descriptor this process owns; the file's mode is preserved.
        if unsafe { libc::fchown(file.as_raw_fd(), uid, gid) } != 0 {
            return Err(std::io::Error::last_os_error());
        }
    }
    Ok(())
}

/// `mkdir -p` where every directory it creates belongs to the bot user when isolation is on.
pub fn mkdir(path: &Path, mode: u32) -> std::io::Result<PathBuf> {
    let missing: Vec<PathBuf> = path.ancestors().take_while(|p| !p.as_os_str().is_empty() && !p.exists()).map(Path::to_path_buf).collect();
    std::fs::DirBuilder::new().recursive(true).mode(mode).create(path)?;
    for part in missing {
        chown(&part);
    }
    Ok(path.to_path_buf())
}

/// Where per-turn host state lives: `default` unless isolated, where the supervisor's state is closed to bot
/// code, so it is a bot-owned `$HOME/.tico-host`.
#[allow(dead_code)] // the Gemini and Antigravity hosts keep their state here
pub fn bot_state(default: &Path) -> PathBuf {
    if !enabled() {
        return default.to_path_buf();
    }
    let home = std::env::var("HOME").ok().filter(|h| !h.is_empty()).unwrap_or_else(|| "/home/runner".into());
    let dir = PathBuf::from(home).join(".tico-host");
    let _ = mkdir(&dir, 0o700);
    dir
}

/// A directory a turn can read files the supervisor prepared for it (vault files).
pub fn turn_dir() -> PathBuf {
    let base = std::env::var("TICO_RUNNER_TURN_DIR").ok().filter(|d| !d.is_empty()).unwrap_or_else(|| "/run/tico-runner/turn".into());
    let base = PathBuf::from(base);
    let _ = std::fs::DirBuilder::new().recursive(true).mode(0o755).create(&base);
    base
}

/// Give the bot user ordinary files/directories under `paths` that the supervisor made, all of them before
/// returning (a checkout git is about to use). Directory descriptors pin traversal; neither path components nor
/// entries follow symlinks. Foreign hardlink aliases stay private.
pub fn adopt(paths: &[PathBuf]) {
    let Some((uid, gid)) = identity() else { return };
    Adoption::new(paths).run(uid, gid, Budget::UNBOUNDED, &mut |_| {});
}

/// What one synchronous pass over a provider's store may walk before its thread starts or its sign-in runs.
const STORE_ENTRIES: usize = 20_000;
const STORE_TIME: std::time::Duration = std::time::Duration::from_secs(2);
/// Deeper directories stay the supervisor's (and are logged): no CLI store nests this far.
const STORE_DEPTH: usize = 64;
/// How long the background continuation of an oversized store may run before the next call retries it.
const CONTINUE_TIME: std::time::Duration = std::time::Duration::from_secs(600);

/// `adopt` for a provider CLI's login or state home (credentials, settings and possibly years of sessions), which
/// is handed over on startup, sign-in and thread-start paths. One bounded breadth-first pass hands over the roots
/// and the shallowest entries first, where a CLI saves its credentials, settings and new sessions. A store larger
/// than that pass is handed off explicitly: the rest continues at once on one background thread per store, and the
/// log says so; a continuation that cannot finish in time is logged and retried by the next call. True when the
/// store was handed over completely before returning.
pub fn adopt_store(paths: &[PathBuf]) -> bool {
    let Some((uid, gid)) = identity() else { return true };
    let deadline = std::time::Instant::now() + STORE_TIME;
    adopt_store_with(paths, uid, gid, Budget { entries: STORE_ENTRIES, depth: STORE_DEPTH, until: Some(deadline) })
}

fn adopt_store_with(paths: &[PathBuf], uid: u32, gid: u32, budget: Budget) -> bool {
    use std::sync::Mutex;
    static CONTINUING: Mutex<Vec<Vec<PathBuf>>> = Mutex::new(Vec::new());
    let named = || paths.iter().map(|p| p.display().to_string()).collect::<Vec<_>>().join(", ");
    let mut walk = Adoption::new(paths);
    if walk.run(uid, gid, budget, &mut |_| {}) {
        walk.report_depth(&named());
        return true;
    }
    let key = paths.to_vec();
    {
        let mut continuing = CONTINUING.lock().unwrap();
        if continuing.contains(&key) {
            crate::util::log(&format!("Tico runner: still handing {} to the bot user in the background", named()));
            return false;
        }
        continuing.push(key.clone());
    }
    crate::util::log(&format!("Tico runner: {} is larger than one ownership pass ({} entries handed over first, \
                               shallowest first); handing over the rest in the background", named(), walk.visited));
    let label = named();
    let finish = move |walk: &mut Adoption| {
        let deadline = std::time::Instant::now() + CONTINUE_TIME;
        let done = walk.run(uid, gid, Budget { entries: usize::MAX, depth: budget.depth, until: Some(deadline) }, &mut |_| {});
        CONTINUING.lock().unwrap().retain(|k| *k != key);
        if done {
            walk.report_depth(&label);
            crate::util::log(&format!("Tico runner: handed {label} to the bot user ({} entries)", walk.visited));
        } else {
            crate::util::log(&format!("Tico runner: handing {label} to the bot user is still incomplete after {} entries; \
                                       the next start, sign-in or thread retries it", walk.visited));
        }
    };
    let spawned = std::thread::Builder::new().name("tico-adopt".into()).spawn(move || finish(&mut walk));
    if let Err(e) = spawned {
        CONTINUING.lock().unwrap().retain(|k| k != paths);
        crate::util::log(&format!("Tico runner: could not continue handing {} to the bot user ({e}); the next call retries it", named()));
    }
    false
}

pub(crate) fn open_at(parent: i32, name: &std::ffi::CStr, flags: i32) -> Option<std::os::fd::OwnedFd> {
    use std::os::fd::FromRawFd;
    // SAFETY: name is NUL-terminated; the returned descriptor has one owner and is close-on-exec.
    let fd = unsafe { libc::openat(parent, name.as_ptr(), flags | libc::O_NOFOLLOW | libc::O_CLOEXEC) };
    (fd >= 0).then(|| unsafe { std::os::fd::OwnedFd::from_raw_fd(fd) })
}

/// The pinned parent directory of `path` and its last name, opened one component at a time without
/// following a link. None for an empty path, / or . (never an ownership-handoff root).
pub(crate) fn pinned_parent(path: &Path) -> Option<(std::os::fd::OwnedFd, std::ffi::CString)> {
    use std::ffi::CString;
    use std::os::fd::AsRawFd;
    use std::os::unix::ffi::OsStrExt;
    use std::path::Component;
    let start = CString::new(if path.is_absolute() { "/" } else { "." }).ok()?;
    let mut fd = open_at(libc::AT_FDCWD, &start, libc::O_RDONLY | libc::O_DIRECTORY)?;
    let parts: Vec<_> = path.components().filter(|p| !matches!(p, Component::RootDir | Component::CurDir)).collect();
    for (index, part) in parts.iter().enumerate() {
        let name = match part {
            Component::Normal(s) => CString::new(s.as_bytes()).ok()?,
            Component::ParentDir => CString::new("..").ok()?,
            _ => return None,
        };
        if index + 1 == parts.len() { return Some((fd, name)); }
        fd = open_at(fd.as_raw_fd(), &name, libc::O_RDONLY | libc::O_DIRECTORY)?;
    }
    None
}

/// Keep a directory the supervisor's own with exactly `mode`, through a descriptor pinned without following a
/// link: one the bot user was given back is reclaimed, so the bot cannot rename what is in it.
pub fn keep_supervisor_directory(path: &Path, mode: u32) -> std::io::Result<()> {
    use std::os::fd::AsRawFd;
    let invalid = || std::io::Error::new(std::io::ErrorKind::InvalidInput, "not a plain directory");
    let (parent, name) = pinned_parent(path).ok_or_else(invalid)?;
    let fd = open_at(parent.as_raw_fd(), &name, libc::O_RDONLY | libc::O_DIRECTORY).ok_or_else(invalid)?;
    let mut info = std::mem::MaybeUninit::<libc::stat>::uninit();
    // SAFETY: fstat, fchown and fchmod on a descriptor this process owns.
    if unsafe { libc::fstat(fd.as_raw_fd(), info.as_mut_ptr()) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    let info = unsafe { info.assume_init() };
    let (uid, gid) = unsafe { (libc::geteuid(), libc::getegid()) };
    if (info.st_uid, info.st_gid) != (uid, gid) && unsafe { libc::fchown(fd.as_raw_fd(), uid, gid) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    if u32::from(info.st_mode) & 0o7777 != mode && unsafe { libc::fchmod(fd.as_raw_fd(), mode as libc::mode_t) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

/// Open `path` read-only through descriptors without following a link in any component, ancestors included, so a
/// bot-owned folder on the way cannot redirect a supervisor read. The one exception is a root-owned link (a system
/// alias such as macOS /var or /tmp, which no bot user can make). `flags` apply to the last component only.
pub fn open_unfollowed(path: &Path, flags: i32) -> std::io::Result<std::fs::File> {
    use std::collections::VecDeque;
    use std::ffi::{CString, OsStr};
    use std::os::fd::AsRawFd;
    use std::os::unix::ffi::OsStrExt;
    use std::path::Component;
    let invalid = || std::io::Error::new(std::io::ErrorKind::InvalidInput, "not a plain path");
    let parts = |path: &Path| -> std::io::Result<Vec<CString>> {
        path.components().filter_map(|part| match part {
            Component::RootDir | Component::CurDir => None,
            Component::Normal(s) => Some(CString::new(s.as_bytes()).map_err(|_| invalid())),
            Component::ParentDir => Some(Ok(CString::new("..").unwrap())),
            Component::Prefix(_) => Some(Err(invalid())),
        }).collect()
    };
    let start = |absolute: bool| {
        open_at(libc::AT_FDCWD, if absolute { c"/" } else { c"." }, libc::O_RDONLY | libc::O_DIRECTORY).ok_or_else(std::io::Error::last_os_error)
    };
    let mut fd = start(path.is_absolute())?;
    let mut pending: VecDeque<CString> = parts(path)?.into();
    let mut links = 0;
    while let Some(name) = pending.pop_front() {
        let last = pending.is_empty();
        match open_at(fd.as_raw_fd(), &name, libc::O_RDONLY | if last { flags } else { libc::O_DIRECTORY }) {
            Some(next) if last => return Ok(next.into()),
            Some(next) => { fd = next; continue; }
            None => {}
        }
        let error = std::io::Error::last_os_error();
        let mut info = std::mem::MaybeUninit::<libc::stat>::uninit();
        // SAFETY: fstatat and readlinkat on a pinned descriptor and a NUL-terminated name, into owned buffers.
        let system_link = unsafe { libc::fstatat(fd.as_raw_fd(), name.as_ptr(), info.as_mut_ptr(), libc::AT_SYMLINK_NOFOLLOW) } == 0
            && unsafe { info.assume_init() }.st_mode & libc::S_IFMT == libc::S_IFLNK
            && unsafe { info.assume_init() }.st_uid == 0;
        if !system_link || links >= 40 {
            return Err(error);
        }
        links += 1;
        let mut target = vec![0u8; libc::PATH_MAX as usize];
        let n = unsafe { libc::readlinkat(fd.as_raw_fd(), name.as_ptr(), target.as_mut_ptr().cast(), target.len()) };
        if n <= 0 || n as usize >= target.len() {
            return Err(error);
        }
        let target = Path::new(OsStr::from_bytes(&target[..n as usize]));
        if target.is_absolute() {
            fd = start(true)?;
        }
        for part in parts(target)?.into_iter().rev() {
            pending.push_front(part);
        }
    }
    Err(invalid())
}

/// How far one adoption pass may go: entries looked at, directory depth below a root, and a deadline.
#[derive(Clone, Copy)]
struct Budget {
    entries: usize,
    depth: usize,
    until: Option<std::time::Instant>,
}

impl Budget {
    const UNBOUNDED: Budget = Budget { entries: usize::MAX, depth: usize::MAX, until: None };
}

struct Directory(*mut libc::DIR);
impl Drop for Directory {
    fn drop(&mut self) { unsafe { libc::closedir(self.0); } }
}

/// Open and hand over one ordinary file or directory `name` in the pinned `parent`, returning its descriptor and
/// status. Links, special files and foreign hardlink aliases are left alone.
fn take(parent: i32, name: &std::ffi::CStr, uid: u32, gid: u32) -> Option<(std::os::fd::OwnedFd, libc::stat)> {
    use std::os::fd::AsRawFd;
    let mut info = std::mem::MaybeUninit::<libc::stat>::uninit();
    // Classify without following a link or opening a special file. The opened inode is checked again below.
    if unsafe { libc::fstatat(parent, name.as_ptr(), info.as_mut_ptr(), libc::AT_SYMLINK_NOFOLLOW) } != 0 { return None; }
    let kind = unsafe { info.assume_init() }.st_mode & libc::S_IFMT;
    if kind != libc::S_IFDIR && kind != libc::S_IFREG { return None; }
    let flags = libc::O_RDONLY | libc::O_NONBLOCK | if kind == libc::S_IFDIR { libc::O_DIRECTORY } else { 0 };
    let fd = open_at(parent, name, flags)?;
    if unsafe { libc::fstat(fd.as_raw_fd(), info.as_mut_ptr()) } != 0 { return None; }
    let info = unsafe { info.assume_init() };
    let kind = info.st_mode & libc::S_IFMT;
    if kind != libc::S_IFDIR && kind != libc::S_IFREG { return None; }
    if kind == libc::S_IFREG && info.st_uid != uid && info.st_nlink != 1 { return None; }
    if info.st_uid != uid {
        // Ownership changes the opened inode, never a name that the bot can replace between syscalls.
        let _ = unsafe { libc::fchown(fd.as_raw_fd(), uid, gid) };
    }
    Some((fd, info))
}

/// A resumable breadth-first adoption. Every directory is reopened from its path one component at a time without
/// following a link, so what a bounded pass leaves is just the queue, and the shallowest entries go first.
struct Adoption {
    queue: std::collections::VecDeque<(PathBuf, usize)>,
    done: std::collections::HashSet<(u64, u64)>,
    visited: usize,
    too_deep: usize,
}

impl Adoption {
    fn new(paths: &[PathBuf]) -> Self {
        Self { queue: paths.iter().map(|p| (p.clone(), 0)).collect(), done: Default::default(), visited: 0, too_deep: 0 }
    }

    /// Walk until nothing is left (true) or `budget` runs out (false: the rest stays queued for another run).
    fn run(&mut self, uid: u32, gid: u32, budget: Budget, before_directory: &mut impl FnMut(&std::ffi::CStr)) -> bool {
        let mut used = 0;
        while let Some((path, depth)) = self.queue.pop_front() {
            if !self.step(&path, depth, uid, gid, &budget, &mut used, before_directory) {
                // An unfinished directory is read again from the start; its finished subdirectories are skipped.
                self.queue.push_front((path, depth));
                return false;
            }
        }
        true
    }

    fn step(&mut self, path: &Path, depth: usize, uid: u32, gid: u32, budget: &Budget, used: &mut usize,
            before_directory: &mut impl FnMut(&std::ffi::CStr)) -> bool {
        use std::ffi::{CStr, OsStr};
        use std::os::fd::AsRawFd;
        use std::os::unix::ffi::OsStrExt;
        let mut spend = |visited: &mut usize| {
            if *used >= budget.entries || budget.until.is_some_and(|t| std::time::Instant::now() >= t) { return false; }
            *used += 1;
            *visited += 1;
            true
        };
        if !spend(&mut self.visited) { return false; }
        let Some((parent, name)) = pinned_parent(path) else { return true };
        let Some((fd, info)) = take(parent.as_raw_fd(), &name, uid, gid) else { return true };
        let key = (info.st_dev as u64, info.st_ino as u64);
        if info.st_mode & libc::S_IFMT != libc::S_IFDIR || self.done.contains(&key) { return true; }
        if depth >= budget.depth {
            self.too_deep += 1;
            self.done.insert(key);
            return true;
        }
        before_directory(&name);
        // fdopendir owns its descriptor. Keep the original pinned for all openat calls and avoid leaking the
        // enumeration descriptor into a concurrently spawned model process.
        let duplicate = unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_DUPFD_CLOEXEC, 0) };
        if duplicate < 0 { return true; }
        let stream = unsafe { libc::fdopendir(duplicate) };
        if stream.is_null() { unsafe { libc::close(duplicate); } return true; }
        let stream = Directory(stream);
        loop {
            let entry = unsafe { libc::readdir(stream.0) };
            if entry.is_null() { break; }
            let child = unsafe { CStr::from_ptr((*entry).d_name.as_ptr()) }.to_owned();
            if child.to_bytes() == b"." || child.to_bytes() == b".." { continue; }
            if !spend(&mut self.visited) { return false; }
            if let Some((_, status)) = take(fd.as_raw_fd(), &child, uid, gid) {
                if status.st_mode & libc::S_IFMT == libc::S_IFDIR {
                    self.queue.push_back((path.join(OsStr::from_bytes(child.to_bytes())), depth + 1));
                }
            }
        }
        self.done.insert(key);
        true
    }

    fn report_depth(&self, named: &str) {
        if self.too_deep > 0 {
            crate::util::log(&format!("Tico runner: {} folder(s) in {named} nest deeper than {STORE_DEPTH} levels; their \
                                       contents stay the supervisor's", self.too_deep));
        }
    }
}

/// The state directory's modes: 0700, or under isolation 0711 with every entry but the public ones private, so
/// the `gh` shim (state/bin) and the read-only mirrors stay reachable for the bot user (state.py State).
pub fn protect_state(directory: &Path) -> std::io::Result<()> {
    if !enabled() {
        return std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o700));
    }
    for entry in std::fs::read_dir(directory)?.flatten() {
        let name = entry.file_name();
        let Ok(meta) = std::fs::symlink_metadata(entry.path()) else { continue };
        if name == "mirrors" || meta.file_type().is_symlink() {
            continue;
        }
        let mode = if name == "bin" { 0o755 } else if meta.is_dir() { 0o700 } else { 0o600 };
        let _ = std::fs::set_permissions(entry.path(), std::fs::Permissions::from_mode(mode));
    }
    std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o711))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explicit_turn_identity_applies_to_direct_and_reaped_commands() {
        let direct = command_as(Some((10142, 10002)), "synthetic-cli");
        let parent = vec![std::ffi::OsString::from("synthetic-reaper")];
        let reaped = command_under_as(Some((10142, 10002)), &parent, "synthetic-cli");
        for command in [&direct, &reaped] {
            let args: Vec<_> = command.as_std().get_args().map(|a| a.to_string_lossy().into_owned()).collect();
            assert!(args.contains(&"--reuid=10142".into()) && args.contains(&"--regid=10002".into()));
            assert_eq!(args.last().map(String::as_str), Some("synthetic-cli"));
        }
        assert!(reaped.as_std().get_args().any(|a| a == "--pdeathsig"));
    }


    #[cfg(target_os = "linux")]
    struct Fixture(PathBuf);
    #[cfg(target_os = "linux")]
    impl Fixture {
        fn new() -> Self {
            assert_eq!(unsafe { libc::geteuid() }, 0, "disposable supervisor fixture");
            assert!(std::env::var_os("TICO_TEST_DISPOSABLE").is_some());
            assert_eq!(identity(), Some((10003, 10002)));
            let path = std::env::temp_dir().join(format!("tico-adopt-contract-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir(&path).unwrap();
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
            Self(path)
        }
    }
    #[cfg(target_os = "linux")]
    impl Drop for Fixture { fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); } }

    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "requires a disposable Linux two-user environment"]
    fn adoption_pins_a_directory_when_the_bot_replaces_its_name() {
        use std::os::unix::fs::MetadataExt;
        let f = Fixture::new();
        let root = f.0.join("managed");
        let child = root.join("swap");
        std::fs::create_dir_all(&child).unwrap();
        std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o700)).unwrap();
        lchown(&root, 10003, 10002).unwrap(); // the bot can replace a child's name
        let original = child.join("ordinary");
        std::fs::write(&original, "managed fixture").unwrap();
        std::fs::set_permissions(&original, std::fs::Permissions::from_mode(0o640)).unwrap();
        let outside = f.0.join("private");
        std::fs::create_dir(&outside).unwrap();
        std::fs::set_permissions(&outside, std::fs::Permissions::from_mode(0o700)).unwrap();
        let private = outside.join("control.json");
        std::fs::write(&private, "supervisor fixture").unwrap();
        std::fs::set_permissions(&private, std::fs::Permissions::from_mode(0o600)).unwrap();
        let parked = root.join("parked");
        let mut swapped = false;
        Adoption::new(&[root]).run(10003, 10002, Budget::UNBOUNDED, &mut |name| {
            if name.to_bytes() != b"swap" || swapped { return; }
            swapped = true;
            let result = std_command("/usr/bin/python3")
                .args(["-c", "import os,sys; os.rename(sys.argv[1],sys.argv[2]); os.symlink(sys.argv[3],sys.argv[1])"])
                .arg(&child).arg(&parked).arg(&outside).output().unwrap();
            assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
        });
        assert!(swapped);
        assert_eq!(private.metadata().unwrap().uid(), 0, "an escaped target remains private");
        assert_eq!(private.metadata().unwrap().permissions().mode() & 0o777, 0o600);
        let adopted = parked.join("ordinary").metadata().unwrap();
        assert_eq!((adopted.uid(), adopted.gid()), (10003, 10002), "the pinned original is handed off");
        assert_eq!(adopted.permissions().mode() & 0o777, 0o640);
    }

    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "requires a disposable Linux two-user environment"]
    fn adoption_rejects_ancestor_links_aliases_and_special_files() {
        use std::os::unix::fs::MetadataExt;
        use std::os::unix::ffi::OsStrExt;
        let f = Fixture::new();
        let root = f.0.join("managed");
        let outside = f.0.join("private");
        std::fs::create_dir_all(outside.join("child")).unwrap();
        std::fs::create_dir(&root).unwrap();
        let private = outside.join("child/control.json");
        std::fs::write(&private, "supervisor fixture").unwrap();
        std::fs::set_permissions(&private, std::fs::Permissions::from_mode(0o600)).unwrap();
        std::os::unix::fs::symlink(&outside, root.join("ancestor")).unwrap();
        adopt(&[root.join("ancestor/child")]);
        assert_eq!(private.metadata().unwrap().uid(), 0, "an intermediate symlink is never followed");
        std::fs::hard_link(&private, root.join("alias")).unwrap();
        let ordinary = root.join("ordinary");
        std::fs::write(&ordinary, "managed fixture").unwrap();
        std::fs::set_permissions(&ordinary, std::fs::Permissions::from_mode(0o640)).unwrap();
        let fifo = root.join("fifo");
        let name = std::ffi::CString::new(fifo.as_os_str().as_bytes()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
        adopt(&[root]);
        assert_eq!(private.metadata().unwrap().uid(), 0, "a foreign hardlink alias grants nothing");
        assert_eq!(fifo.metadata().unwrap().uid(), 0, "special files are skipped");
        assert_eq!(ordinary.metadata().unwrap().uid(), 10003);
        assert_eq!(ordinary.metadata().unwrap().permissions().mode() & 0o777, 0o640);
    }

    #[test]
    fn a_bounded_pass_hands_over_the_shallowest_entries_and_resumes() {
        // Runs as any user: handing a tree to its own owner changes nothing, so this checks the walk itself.
        let (uid, gid) = unsafe { (libc::geteuid(), libc::getegid()) };
        // Adoption follows no link at all, so start from a link-free path (macOS's /var is one).
        let root = std::fs::canonicalize(std::env::temp_dir()).unwrap().join(format!("tico-adopt-bound-{}", uuid::Uuid::new_v4().simple()));
        for i in 0..30 {
            let folder = root.join(format!("d{i}"));
            std::fs::create_dir_all(&folder).unwrap();
            for j in 0..40 { std::fs::write(folder.join(format!("f{j}")), "").unwrap(); }
        }
        std::fs::create_dir_all(root.join("deep/l1/l2/l3/l4/l5")).unwrap();
        let mut walk = Adoption::new(std::slice::from_ref(&root));
        let mut seen = Vec::new();
        let budget = Budget { entries: 100, depth: 4, until: None };
        assert!(!walk.run(uid, gid, budget, &mut |n| seen.push(n.to_bytes().to_vec())), "an oversized tree stops at the bound");
        assert_eq!(walk.visited, 100);
        assert_eq!(seen[0], root.file_name().unwrap().as_encoded_bytes(), "the store root goes first");
        assert!(seen.len() < 5, "the first pass ends early: {}", seen.len());
        // The rest is handed off and finished; nothing is skipped except folders past the depth bound, which are counted.
        assert!(walk.run(uid, gid, Budget { entries: usize::MAX, ..budget }, &mut |n| seen.push(n.to_bytes().to_vec())));
        let seen: std::collections::HashSet<String> = seen.iter().map(|n| String::from_utf8_lossy(n).into_owned()).collect();
        let mut expected: std::collections::HashSet<String> = (0..30).map(|i| format!("d{i}")).collect();
        expected.extend([root.file_name().unwrap().to_string_lossy().into_owned(), "deep".into(), "l1".into(), "l2".into()]);
        assert_eq!(seen, expected);
        assert_eq!(walk.too_deep, 1, "l3 is past the bound and reported");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn off_without_a_bot_uid_and_for_the_supervisor_itself() {
        let s = |v: &str| Some(v.to_string());
        assert_eq!(identity_from(None, None, 501), None);
        assert_eq!(identity_from(s("501"), None, 501), None, "the supervisor's own uid is no separation");
        assert_eq!(identity_from(s("0"), None, 501), None);
        assert_eq!(identity_from(s("bot"), None, 501), None);
        assert_eq!(identity_from(s("10003"), None, 10002), Some((10003, 10003)));
        assert_eq!(identity_from(s("10003"), s("10002"), 10002), Some((10003, 10002)));
        let p = prefix_for((10003, 10002));
        assert_eq!(p, ["/usr/bin/setpriv", "--reuid=10003", "--regid=10002", "--clear-groups", "--inh-caps=-all", "--ambient-caps=-all"]);
        // A leased turn user gets exactly its own checkouts' groups, a sibling none, and the UID's next borrower none.
        crate::checkout_groups::grant(10150, &[20001, 20002]);
        assert_eq!(prefix_for((10150, 10002))[3], "--groups=10002,20001,20002");
        assert_eq!(prefix_for((10151, 10002))[3], "--clear-groups");
        crate::checkout_groups::revoke(10150);
        assert_eq!(prefix_for((10150, 10002))[3], "--clear-groups");
    }
}
