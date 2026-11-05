//! `state-<id>/runner.lock`, taken with flock(2) exactly as `python -m runner_support` takes it with
//! `fcntl.flock(LOCK_EX | LOCK_NB)`, so the two engines can never run one registration at once.

use std::fs::{File, OpenOptions};
use std::os::unix::io::AsRawFd;
use std::path::Path;

pub const HELD: &str = "A runner process already holds this registration";

pub struct RunnerLock {
    _file: File,
}

/// Ok(lock) while held; Err(HELD) when another process (either engine) has it.
pub fn acquire(path: &Path) -> Result<RunnerLock, String> {
    let file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(path)
        .map_err(|e| format!("cannot open {}: {e}", path.display()))?;
    let rc = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
    if rc != 0 {
        let err = std::io::Error::last_os_error();
        if err.raw_os_error() == Some(libc::EWOULDBLOCK) {
            return Err(HELD.to_string());
        }
        return Err(format!("cannot lock {}: {err}", path.display()));
    }
    Ok(RunnerLock { _file: file })
}

/// Hold inherited descriptors across the parent's lock release, without relying on fork/exec timing.
#[cfg(test)]
pub(crate) fn inherited_files() -> impl Drop {
    use std::io::Write;
    use std::os::unix::net::UnixStream;

    struct Child { release: UnixStream, pid: libc::pid_t }
    impl Drop for Child {
        fn drop(&mut self) {
            let _ = self.release.write_all(b"x");
            // SAFETY: wait only for the child this helper starts, retrying an interrupted wait.
            while unsafe { libc::waitpid(self.pid, std::ptr::null_mut(), 0) } < 0
                && std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted {}
        }
    }
    let (release, wait) = UnixStream::pair().unwrap();
    let (release_fd, wait_fd) = (release.as_raw_fd(), wait.as_raw_fd());
    // SAFETY: the child calls only async-signal-safe functions, then exits without running Rust destructors.
    let pid = unsafe { libc::fork() };
    assert!(pid >= 0, "{}", std::io::Error::last_os_error());
    if pid == 0 {
        unsafe {
            libc::close(release_fd);
            let mut byte = 0u8;
            libc::read(wait_fd, (&mut byte as *mut u8).cast(), 1);
            libc::_exit(0);
        }
    }
    drop(wait);
    Child { release, pid }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Write};
    use std::process::{Command, Stdio};

    const PY_TRY: &str = "import fcntl,sys\nf=open(sys.argv[1],'w')\ntry:\n fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n print('got')\nexcept BlockingIOError:\n print('held')\n";
    const PY_HOLD: &str = "import fcntl,sys\nf=open(sys.argv[1],'w')\nfcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nprint('holding',flush=True)\nsys.stdin.read()\n";

    #[test]
    fn released_registration_lock_does_not_wait_for_a_forked_child() {
        let dir = std::env::temp_dir().join(format!("tico-lock-fork-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&dir).unwrap();
        let path = dir.join("runner.lock");
        let held = acquire(&path).unwrap();
        let child = inherited_files();
        assert_eq!(acquire(&path).err().as_deref(), Some(HELD));
        drop(held);
        assert!(acquire(&path).is_ok(), "a forked child retained the released registration lock");
        drop(child);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn the_lock_interlocks_with_pythons_fcntl_flock_both_ways() {
        let dir = std::env::temp_dir().join(format!("tico-lock-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("runner.lock");

        // Rust holds it: Python is refused.
        let held = acquire(&path).unwrap();
        let out = Command::new("python3").args(["-c", PY_TRY]).arg(&path).output().unwrap();
        assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "held");
        assert_eq!(acquire(&path).err().as_deref(), Some(HELD), "a second open file is refused too");
        drop(held);
        let out = Command::new("python3").args(["-c", PY_TRY]).arg(&path).output().unwrap();
        assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "got");

        // Python holds it: Rust is refused until Python exits.
        let mut python = Command::new("python3").args(["-c", PY_HOLD]).arg(&path).stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
        let mut line = String::new();
        BufReader::new(python.stdout.take().unwrap()).read_line(&mut line).unwrap();
        assert_eq!(line.trim(), "holding");
        assert_eq!(acquire(&path).err().as_deref(), Some(HELD));
        python.stdin.take().unwrap().write_all(b"").unwrap();
        python.wait().unwrap();
        assert!(acquire(&path).is_ok());
        std::fs::remove_dir_all(dir).ok();
    }
}
