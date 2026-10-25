//! Docker Gateway identities. A slot is returned only after its processes and files are cleaned.
//! The supervisor owns the pool and private parent; bot code cannot allocate or replace either.
use std::collections::BTreeSet;
use std::ffi::{CStr, CString};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{DirBuilderExt, MetadataExt};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use crate::env::Env;
use crate::isolation::{open_at, pinned_parent};

pub const POOL_ENV: &str = "TICO_RUNNER_TURN_UIDS";
/// Every slot is leased or quarantined: the turn waits for one instead of failing (execute.rs take_turn_user).
pub const BUSY: &str = "All Docker turn users are busy";
const FIRST: u32 = 10100;
const LAST: u32 = 10163;
const PRIVATE: &str = "/run/tico-runner/turns";
const WALK_LIMIT: usize = 5_000_000;

fn parse(value: &str) -> Result<Vec<u32>, String> {
    let (a, b) = value.split_once('-').ok_or("turn-user pool must be a UID range")?;
    let a: u32 = a.parse().map_err(|_| "invalid turn-user pool")?;
    let b: u32 = b.parse().map_err(|_| "invalid turn-user pool")?;
    if a < FIRST || b > LAST || a > b { return Err("turn-user pool is outside the reserved UID range".into()); }
    Ok((a..=b).collect())
}

struct Pool {
    available: Mutex<BTreeSet<u32>>,
    gid: u32,
    bot: u32,
    home: PathBuf,
}

static POOL: OnceLock<Result<Option<Arc<Pool>>, String>> = OnceLock::new();

fn pool() -> Result<Option<Arc<Pool>>, String> {
    POOL.get_or_init(|| {
        let Some((bot, gid)) = crate::isolation::identity() else { return Ok(None) };
        let Some(value) = std::env::var(POOL_ENV).ok().filter(|v| !v.is_empty()) else { return Ok(None) };
        if !cfg!(target_os = "linux") { return Err("turn-user pool needs Linux".into()); }
        let ids = parse(&value)?;
        let home = PathBuf::from(std::env::var("HOME").map_err(|_| "turn-user pool needs HOME")?);
        if !home.is_absolute() || home == Path::new("/") { return Err("invalid turn-user cleanup home".into()); }
        std::fs::DirBuilder::new().mode(0o711).create(PRIVATE).or_else(|e| {
            if e.kind() == std::io::ErrorKind::AlreadyExists { Ok(()) } else { Err(e) }
        }).map_err(|e| e.to_string())?;
        crate::isolation::keep_supervisor_directory(Path::new(PRIVATE), 0o711).map_err(|e| e.to_string())?;
        let pool = Arc::new(Pool { available: Mutex::new(BTreeSet::new()), gid, bot, home });
        // Crash recovery happens before any slot can be borrowed. Failed slots stay quarantined.
        for uid in ids {
            match pool.clean(uid) {
                Ok(()) => { pool.available.lock().unwrap().insert(uid); }
                Err(e) => crate::util::log(&format!("Tico runner: turn user {uid} quarantined: {e}")),
            }
        }
        Ok(Some(pool))
    }).clone()
}

pub fn initialize() -> Result<(), String> { pool().map(|_| ()) }

/// Whether this runner lends turn users (the Docker image's pool, once initialize has run). Without one, a harness that could run as
/// its own user (`isolated_user`) runs as `bot`, as before; only a Gateway turn needs a lease and fails without it.
pub fn available() -> bool { matches!(POOL.get(), Some(Ok(Some(_)))) } // never starts the pool's sweep: initialize does

/// Existing workspace files may be owner-only. Widen only bot-owned ordinary workspace inodes,
/// as their existing owner, so a leased user has the same file access through the shared group.
pub async fn share_workspace(paths: Vec<PathBuf>) -> Result<(), String> {
    tokio::task::spawn_blocking(move || share_workspace_blocking(&paths)).await.map_err(|e| e.to_string())?
}

pub(crate) fn share_workspace_blocking(paths: &[PathBuf]) -> Result<(), String> {
    let status = crate::isolation::std_command(crate::env::python()).args(["-c", r#"import errno,os,stat,sys
def share(name,dir_fd=None):
 # Open relative to the pinned directory without following a link, then decide on the opened inode.
 try: fd=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=dir_fd)
 except OSError as e:
  if e.errno in (errno.ENOENT,errno.ELOOP): return
  raise
 try:
  s=os.fstat(fd)
  if s.st_uid==os.geteuid() and (stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode) and s.st_nlink==1):
   os.fchmod(fd,stat.S_IMODE(s.st_mode)|0o060|(0o010 if s.st_mode&0o111 else 0))
 finally: os.close(fd)
for root in sys.argv[1:]:
 share(root)
 for base,dirs,files,fd in os.fwalk(root,follow_symlinks=False):
  for name in dirs+files:
   s=os.stat(name,dir_fd=fd,follow_symlinks=False)
   if stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode): share(name,fd)
"#]).args(paths).env_clear().status().map_err(|e| e.to_string())?;
    if status.success() { Ok(()) } else { Err("cannot share the bot workspace with its turn user".into()) }
}

/// The relay and the harness retain this lease until the attempt's cleanup has finished.
pub struct Lease {
    pool: Arc<Pool>,
    pub uid: u32,
    pub dir: PathBuf,
    reusable: bool,
    /// The turn's own credential socket at a fixed path in `dir`, closed first when the lease ends.
    socket: Option<crate::gitcred::TurnSocket>,
}

impl Lease {
    pub fn take() -> Result<Self, String> {
        let pool = pool()?.ok_or("Tico models need the current Docker runner image (not_isolated)")?;
        let uid = pool.available.lock().unwrap().pop_first().ok_or(BUSY)?;
        let dir = Path::new(PRIVATE).join(uid.to_string());
        let made = (|| {
            std::fs::DirBuilder::new().mode(0o700).create(&dir)?;
            std::os::unix::fs::lchown(&dir, Some(uid), Some(pool.gid))
        })();
        if let Err(e) = made {
            // Fail closed: the next runner start sweeps this slot before making it available again.
            return Err(format!("cannot prepare turn user {uid}: {e}"));
        }
        Ok(Self { pool, uid, dir, reusable: false, socket: None })
    }

    pub fn identity(&self) -> (u32, u32) { (self.uid, self.pool.gid) }

    /// The lease keeps the turn's credential socket, so the socket cannot outlive it: a later borrower of this UID
    /// binds the same path, and a socket dropped after the UID's return would race that turn's.
    pub fn hold_socket(&mut self, socket: crate::gitcred::TurnSocket) { self.socket = Some(socket); }

    pub fn socket(&self) -> Option<&crate::gitcred::TurnSocket> { self.socket.as_ref() }

    /// The turn's own HOME, in its private directory and removed with it. The shared HOME is every bot's to write,
    /// and what reads it (Python's user site and `.pth` files, ssh, shells, npm and pip config) would run a sibling's
    /// code as this user, Gateway key and all. pi needs nothing from there: its agent directory is the turn's own.
    pub fn private_home(&self, env: &mut Env) -> Result<(), String> {
        let home = self.dir.join("home");
        match std::fs::DirBuilder::new().mode(0o700).create(&home) {
            Ok(()) => std::os::unix::fs::lchown(&home, Some(self.uid), Some(self.pool.gid)).map_err(|e| e.to_string())?,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {} // a fallback hop on the same lease
            Err(e) => return Err(format!("cannot make the turn user's home: {e}")),
        }
        let info = std::fs::symlink_metadata(&home).map_err(|e| e.to_string())?;
        if !info.is_dir() || info.uid() != self.uid { return Err("invalid private turn home".into()); }
        for (name, under) in [("HOME", ""), ("XDG_CONFIG_HOME", ".config"), ("XDG_CACHE_HOME", ".cache"), ("XDG_DATA_HOME", ".local/share"), ("XDG_STATE_HOME", ".local/state")] {
            env.insert(name.into(), home.join(under).display().to_string().trim_end_matches('/').to_string());
        }
        env.insert("PYTHONNOUSERSITE".into(), "1".into());
        Ok(())
    }

    /// Called only after the relay listener and all in-flight model calls have actually stopped.
    pub fn permit_reuse(&mut self) { self.reusable = true; }

    /// Only the Gateway hop gets the relay environment and private files. Subscription hosts stay unchanged.
    pub fn move_files(&self, env: &mut Env, files: &mut Vec<PathBuf>, id: (u32, u32), directory: &Path) -> Result<(), String> {
        use std::io::{Read, Write};
        use std::os::unix::fs::OpenOptionsExt;
        for file in files {
            if file.parent() == Some(directory) { continue; }
            let destination = directory.join(format!("credential-{}", uuid::Uuid::new_v4().simple()));
            let (parent, name) = pinned_parent(file).ok_or("invalid credential path")?;
            let fd = open_at(parent.as_raw_fd(), &name, libc::O_RDONLY | libc::O_NONBLOCK).ok_or("cannot open turn credential")?;
            let input = std::fs::File::from(fd);
            let info = input.metadata().map_err(|e| e.to_string())?;
            if !info.is_file() || info.nlink() != 1 || ![self.uid, self.pool.bot].contains(&info.uid()) {
                return Err("turn credential is not an owned plain file".into());
            }
            let mut body = Vec::new();
            input.take(16 * 1024 * 1024 + 1).read_to_end(&mut body).map_err(|e| e.to_string())?;
            if body.len() > 16 * 1024 * 1024 { return Err("turn credential file is too large".into()); }
            let mut out = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600)
                .custom_flags(libc::O_NOFOLLOW).open(&destination).map_err(|e| e.to_string())?;
            out.write_all(&body).map_err(|e| e.to_string())?;
            if unsafe { libc::fchown(out.as_raw_fd(), id.0, id.1) } != 0 { return Err(std::io::Error::last_os_error().to_string()); }
            let old = file.display().to_string();
            for value in env.values_mut() {
                if value == &old { *value = destination.display().to_string(); }
            }
            std::fs::remove_file(&*file).map_err(|e| e.to_string())?;
            *file = destination;
        }
        Ok(())
    }
}

impl Drop for Lease {
    fn drop(&mut self) {
        self.socket.take();
        crate::checkout_groups::revoke(self.uid);
        match self.pool.clean(self.uid) {
            Ok(()) if self.reusable => { self.pool.available.lock().unwrap().insert(self.uid); }
            Ok(()) => crate::util::log(&format!("Tico runner: turn user {} quarantined after interrupted cleanup", self.uid)),
            Err(e) => crate::util::log(&format!("Tico runner: turn user {} quarantined after cleanup: {e}", self.uid)),
        }
    }
}

impl Pool {
    fn clean(&self, uid: u32) -> Result<(), String> {
        stop_processes(uid, self.gid)?;
        let private = Path::new(PRIVATE).join(uid.to_string());
        // A lease creates this supervisor-parented marker before doing anything as its UID, and removes
        // it only after all sweeps succeed. Unused clean slots need no repeated whole-home traversal.
        match std::fs::symlink_metadata(&private) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(e) => return Err(e.to_string()),
            Ok(info) if !info.is_dir() => return Err("invalid private turn directory".into()),
            Ok(_) => {}
        }
        // Restore owner-only workspace files to bot for the next turn. Sweep the whole shared home too:
        // leaving an inode owned by this UID there would give its next borrower owner rights.
        sweep(&self.home, uid, Some((self.bot, self.gid)))?;
        for path in [PathBuf::from("/tmp"), PathBuf::from("/var/tmp"), PathBuf::from("/dev/shm"), private.clone()] {
            sweep(&path, uid, None)?;
        }
        // A crash between mkdir and chown can leave an empty supervisor-owned marker.
        if private.exists() { std::fs::remove_dir(&private).map_err(|e| e.to_string())?; }
        Ok(())
    }
}

fn processes(uid: u32) -> Result<Vec<i32>, String> {
    let mut result = vec![];
    for entry in std::fs::read_dir("/proc").map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let Ok(pid) = entry.file_name().to_string_lossy().parse::<i32>() else { continue };
        let meta = match entry.metadata() {
            Ok(info) => info,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Err(e) => return Err(e.to_string()),
        };
        if meta.uid() != uid { continue; }
        // Zombies cannot execute or retain open descriptors; their parent still needs to reap them.
        let stat = std::fs::read_to_string(entry.path().join("stat")).unwrap_or_default();
        if stat.rsplit_once(") ").is_some_and(|(_, tail)| tail.starts_with('Z')) { continue; }
        result.push(pid);
    }
    Ok(result)
}

fn stop_processes(uid: u32, gid: u32) -> Result<(), String> {
    for _ in 0..10 {
        if processes(uid)?.is_empty() { return Ok(()); }
        // kill(-1) as the slot user reaches only its own processes, detached descendants included, in one call a
        // forking process cannot outrun. Linux spares the caller (and setpriv execs kill, leaving no wrapper), but
        // the helper dying of SIGKILL would not be a failure either: /proc, checked again above, decides.
        use std::os::unix::process::ExitStatusExt;
        let status = crate::isolation::std_command_as(Some((uid, gid)), "/bin/kill")
            .args(["-KILL", "--", "-1"]).env_clear().status().map_err(|e| e.to_string())?;
        if !status.success() && status.signal() != Some(libc::SIGKILL) { return Err(format!("cannot kill turn-user processes: {status}")); }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    Err("turn-user processes are still alive".into())
}

/// Pinned, no-follow traversal. Only the slot's inodes change ownership or are removed. A bounded walk
/// or any IO error quarantines the slot instead of returning it with unknown leftovers.
fn sweep(path: &Path, uid: u32, destination: Option<(u32, u32)>) -> Result<(), String> {
    let Some((parent, name)) = pinned_parent(path) else { return Err("invalid cleanup root".into()) };
    let mut budget = WALK_LIMIT;
    walk(parent.as_raw_fd(), &name, uid, destination, &mut budget).map_err(|e| e.to_string())
}

fn walk(parent: i32, name: &CStr, uid: u32, destination: Option<(u32, u32)>, budget: &mut usize) -> std::io::Result<()> {
    if *budget == 0 { return Err(std::io::Error::other("turn cleanup traversal limit reached")); }
    *budget -= 1;
    let mut raw = std::mem::MaybeUninit::<libc::stat>::uninit();
    if unsafe { libc::fstatat(parent, name.as_ptr(), raw.as_mut_ptr(), libc::AT_SYMLINK_NOFOLLOW) } != 0 {
        let e = std::io::Error::last_os_error();
        return if e.kind() == std::io::ErrorKind::NotFound { Ok(()) } else { Err(e) };
    }
    let info = unsafe { raw.assume_init() };
    let directory = info.st_mode & libc::S_IFMT == libc::S_IFDIR;
    if directory {
        let fd = open_at(parent, name, libc::O_RDONLY | libc::O_DIRECTORY).ok_or_else(std::io::Error::last_os_error)?;
        let duplicate = unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_DUPFD_CLOEXEC, 0) };
        if duplicate < 0 { return Err(std::io::Error::last_os_error()); }
        let stream = unsafe { libc::fdopendir(duplicate) };
        if stream.is_null() { unsafe { libc::close(duplicate); } return Err(std::io::Error::last_os_error()); }
        struct Directory(*mut libc::DIR);
        impl Drop for Directory { fn drop(&mut self) { unsafe { libc::closedir(self.0); } } }
        let stream = Directory(stream);
        loop {
            #[cfg(target_os = "linux")]
            unsafe { *libc::__errno_location() = 0; }
            let entry = unsafe { libc::readdir(stream.0) };
            if entry.is_null() {
                #[cfg(target_os = "linux")]
                if unsafe { *libc::__errno_location() } != 0 { return Err(std::io::Error::last_os_error()); }
                break;
            }
            let child = unsafe { CStr::from_ptr((*entry).d_name.as_ptr()) }.to_owned();
            if matches!(child.to_bytes(), b"." | b"..") { continue; }
            walk(fd.as_raw_fd(), &child, uid, destination, budget)?;
        }
        // Reclassify the opened inode, not a potentially replaced directory entry.
        if unsafe { libc::fstat(fd.as_raw_fd(), raw.as_mut_ptr()) } != 0 { return Err(std::io::Error::last_os_error()); }
        if unsafe { raw.assume_init() }.st_uid == uid {
            let (owner, group) = destination.unwrap_or_else(|| unsafe { (libc::geteuid(), libc::getegid()) });
            if unsafe { libc::fchown(fd.as_raw_fd(), owner, group) } != 0 { return Err(std::io::Error::last_os_error()); }
            if destination.is_none() && unsafe { libc::unlinkat(parent, name.as_ptr(), libc::AT_REMOVEDIR) } != 0 {
                return Err(std::io::Error::last_os_error());
            }
        }
    } else if info.st_uid == uid {
        // O_PATH pins symlinks and special files without opening their targets (Linux only).
        #[cfg(target_os = "linux")]
        {
            let fd = open_at(parent, name, libc::O_PATH).ok_or_else(std::io::Error::last_os_error)?;
            if unsafe { libc::fstat(fd.as_raw_fd(), raw.as_mut_ptr()) } != 0 { return Err(std::io::Error::last_os_error()); }
            let pinned = unsafe { raw.assume_init() };
            if pinned.st_uid != uid { return Ok(()); }
            if pinned.st_mode & libc::S_IFMT == libc::S_IFDIR { return Err(std::io::Error::other("cleanup entry changed type")); }
            let (owner, group) = destination.unwrap_or_else(|| unsafe { (libc::geteuid(), libc::getegid()) });
            let empty = CString::new("").unwrap();
            if unsafe { libc::fchownat(fd.as_raw_fd(), empty.as_ptr(), owner, group, libc::AT_EMPTY_PATH | libc::AT_SYMLINK_NOFOLLOW) } != 0 {
                return Err(std::io::Error::last_os_error());
            }
            if destination.is_none() {
                // Another name for this inode may survive the unlink. Now supervisor-owned, it is stripped of every
                // permission (the turn's umask left it group-readable) so no sibling can read it through that name.
                if pinned.st_mode & libc::S_IFMT != libc::S_IFLNK {
                    let path = CString::new(format!("/proc/self/fd/{}", fd.as_raw_fd())).unwrap();
                    if unsafe { libc::chmod(path.as_ptr(), 0) } != 0 { return Err(std::io::Error::last_os_error()); }
                }
                if unsafe { libc::unlinkat(parent, name.as_ptr(), 0) } != 0 { return Err(std::io::Error::last_os_error()); }
                // Those other names are leftovers this sweep may never reach: quarantine instead of reusing the UID.
                if pinned.st_nlink > 1 { return Err(std::io::Error::other("turn-owned file has another name")); }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_reserved_non_supervisor_ids_can_be_leased() {
        assert_eq!(parse("10100-10163").unwrap().len(), 64);
        for invalid in ["", "0-0", "10002-10100", "10003-10100", "10100-10164", "10102-10101", "10100", "10100-10100-10101"] {
            assert!(parse(invalid).is_err(), "{invalid}");
        }
    }
}
