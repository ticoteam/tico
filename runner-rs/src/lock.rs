//! `state-<id>/runner.lock`, taken with flock(2) exactly as `python -m runner` takes it with
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Write};
    use std::process::{Command, Stdio};

    const PY_TRY: &str = "import fcntl,sys\nf=open(sys.argv[1],'w')\ntry:\n fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n print('got')\nexcept BlockingIOError:\n print('held')\n";
    const PY_HOLD: &str = "import fcntl,sys\nf=open(sys.argv[1],'w')\nfcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nprint('holding',flush=True)\nsys.stdin.read()\n";

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
