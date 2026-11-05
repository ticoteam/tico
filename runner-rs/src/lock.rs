//! `state-<id>/runner.lock`, taken with flock(2) exactly as `python -m runner_support` takes it with
//! `fcntl.flock(LOCK_EX | LOCK_NB)`, so the two engines can never run one registration at once.

use std::fs::{File, OpenOptions};
use std::os::unix::io::AsRawFd;
use std::path::Path;

pub const HELD: &str = "A runner process already holds this registration";

pub struct RunnerLock {
    _file: File,
}

impl Drop for RunnerLock {
    fn drop(&mut self) {
        // A concurrent spawn can inherit the open description until exec closes it. Ownership ends here,
        // even while that child's inherited descriptor remains open.
        unsafe { libc::flock(self._file.as_raw_fd(), libc::LOCK_UN); }
    }
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Write};
    use std::process::{Command, Stdio};

    const PY_TRY: &str = "import fcntl,sys\nf=open(sys.argv[1],'w')\ntry:\n fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n print('got')\nexcept BlockingIOError:\n print('held')\n";
    const PY_HOLD: &str = "import fcntl,sys\nf=open(sys.argv[1],'w')\nfcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nprint('holding',flush=True)\nsys.stdin.read()\n";

    #[test]
    fn dropping_the_owner_releases_a_lock_even_while_a_child_inherits_its_descriptor() {
        let dir = std::env::temp_dir().join(format!("tico-lock-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("runner.lock");
        let held = acquire(&path).unwrap();
        let mut barrier = [0; 2];
        assert_eq!(unsafe { libc::pipe(barrier.as_mut_ptr()) }, 0);
        // Keep the fork's inherited descriptor open deterministically. The child uses only async-signal-safe
        // syscalls before _exit, so other test threads and their runtime locks cannot affect its shutdown.
        let pid = unsafe {
            let pid = libc::fork();
            if pid == 0 {
                libc::close(barrier[1]);
                let mut byte = 0u8;
                while libc::read(barrier[0], (&mut byte as *mut u8).cast(), 1) < 0 {}
                libc::_exit(0);
            }
            pid
        };
        assert!(pid > 0);
        unsafe { libc::close(barrier[0]); }
        assert!(acquire(&path).is_err());
        drop(held);
        let next = acquire(&path);
        // EOF releases only this test's child, after the next acquisition observed its inherited descriptor.
        unsafe { libc::close(barrier[1]); }
        let mut status = 0;
        let waited = loop {
            let result = unsafe { libc::waitpid(pid, &mut status, 0) };
            if result != -1 || std::io::Error::last_os_error().kind() != std::io::ErrorKind::Interrupted {
                break result;
            }
        };
        assert_eq!(waited, pid);
        assert_eq!(status, 0);
        assert!(next.is_ok(), "the child must not extend the owner's lock lifetime");
        drop(next);
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
