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

/// Existing workspace files may be owner-only. Widen only bot-owned ordinary workspace inodes,
/// as their existing owner, so a leased user has the same file access through the shared group.
pub async fn share_workspace(paths: Vec<PathBuf>) -> Result<(), String> {
    tokio::task::spawn_blocking(move || share_workspace_blocking(&paths)).await.map_err(|e| e.to_string())?
}

fn share_workspace_blocking(paths: &[PathBuf]) -> Result<(), String> {
    let status = crate::isolation::std_command(crate::env::python()).args(["-c", r#"import os,stat,sys
def share(path):
 fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd)
  if s.st_uid==os.geteuid() and (stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode) and s.st_nlink==1):
   os.fchmod(fd,stat.S_IMODE(s.st_mode)|0o060|(0o010 if s.st_mode&0o111 else 0))
 finally: os.close(fd)
for root in sys.argv[1:]:
 share(root)
 for base,dirs,files,fd in os.fwalk(root,follow_symlinks=False):
  for name in dirs+files:
   p=os.path.join(base,name)
   if os.path.islink(p): continue
   s=os.stat(name,dir_fd=fd,follow_symlinks=False)
   if stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode): share(p)
"#]).args(paths).env_clear().status().map_err(|e| e.to_string())?;
    if status.success() { Ok(()) } else { Err("cannot share the bot workspace with its turn user".into()) }
}

/// The relay and the harness retain this lease until the attempt's cleanup has finished.
pub struct Lease {
    pool: Arc<Pool>,
    pub uid: u32,
    pub dir: PathBuf,
    reusable: bool,
}

impl Lease {
    pub fn take() -> Result<Self, String> {
        let pool = pool()?.ok_or("Tico models need the current Docker runner image (not_isolated)")?;
        let uid = pool.available.lock().unwrap().pop_first().ok_or("All Docker turn users are busy; retry this turn shortly")?;
        let dir = Path::new(PRIVATE).join(uid.to_string());
        let made = (|| {
            std::fs::DirBuilder::new().mode(0o700).create(&dir)?;
            std::os::unix::fs::lchown(&dir, Some(uid), Some(pool.gid))
        })();
        if let Err(e) = made {
            // Fail closed: the next runner start sweeps this slot before making it available again.
            return Err(format!("cannot prepare turn user {uid}: {e}"));
        }
        Ok(Self { pool, uid, dir, reusable: false })
    }

    pub fn identity(&self) -> (u32, u32) { (self.uid, self.pool.gid) }

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
        // kill(-1) as the slot user can reach only its own processes, including detached descendants.
        let status = crate::isolation::std_command_as(Some((uid, gid)), "/bin/kill")
            .args(["-KILL", "--", "-1"]).env_clear().status().map_err(|e| e.to_string())?;
        if !status.success() { return Err("cannot kill turn-user processes".into()); }
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
            if destination.is_none() && unsafe { libc::unlinkat(parent, name.as_ptr(), 0) } != 0 { return Err(std::io::Error::last_os_error()); }
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
