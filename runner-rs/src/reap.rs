//! The parent of a Claude turn on Linux (`tico-runner --reap <status file> -- <argv>`). It is a child subreaper, so a
//! background shell the turn leaves running when it ends on its wait (hosts::claude) comes here when Claude dies, and
//! its exit status is known. It passes SIGTERM and SIGINT on to Claude and exits with Claude's status, unless the
//! runner sent KEEP: then it kills Claude outright, which runs none of its cleanup, and stays to reap its shells.
//! The status file's first line is `claude <pid>`; then each status it reaps is one line, `<pid> <code>` (minus the
//! signal for a killed one).
//! It leads the process group the runner starts it in (hosts::new_group), which Claude and all it starts share: a stop
//! reaches every one of them, while KEEP comes to this process alone.

use std::io::Write;
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};

pub const FLAG: &str = "--reap";
/// The runner's ask to end Claude and keep its shells.
pub const KEEP: i32 = libc::SIGUSR1;

static CHILD: AtomicI32 = AtomicI32::new(0);
static KEEPING: AtomicBool = AtomicBool::new(false);

extern "C" fn pass_on(signal: libc::c_int) {
    let child = CHILD.load(Ordering::SeqCst);
    if child > 0 {
        // SAFETY: kill is async-signal-safe.
        unsafe { libc::kill(child, signal) };
    }
}

extern "C" fn keep(_: libc::c_int) {
    KEEPING.store(true, Ordering::SeqCst);
    pass_on(libc::SIGKILL);
}

fn code(status: libc::c_int) -> i32 {
    if libc::WIFEXITED(status) {
        libc::WEXITSTATUS(status)
    } else if libc::WIFSIGNALED(status) {
        -libc::WTERMSIG(status)
    } else {
        -1
    }
}

/// `args`: the status file, `--`, then the command and its arguments.
pub fn main(args: &[String]) -> i32 {
    let (path, argv) = match args {
        [path, dashes, argv @ ..] if dashes == "--" && !argv.is_empty() => (path, argv),
        _ => {
            eprintln!("usage: tico-runner {FLAG} <status file> -- <command> [args...]");
            return 2;
        }
    };
    let mut status = match std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        Ok(f) => f,
        Err(e) => {
            eprintln!("tico-runner {FLAG}: {path}: {e}");
            return 2;
        }
    };
    #[cfg(target_os = "linux")]
    // SAFETY: prctl with these arguments only sets this process's flag.
    unsafe {
        libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0);
    }
    // SAFETY: handlers only touch atomics and call kill.
    unsafe {
        libc::signal(
            libc::SIGTERM,
            pass_on as extern "C" fn(libc::c_int) as libc::sighandler_t,
        );
        libc::signal(
            libc::SIGINT,
            pass_on as extern "C" fn(libc::c_int) as libc::sighandler_t,
        );
        libc::signal(
            KEEP,
            keep as extern "C" fn(libc::c_int) as libc::sighandler_t,
        );
    }
    let mut command = std::process::Command::new(&argv[0]);
    command.args(&argv[1..]);
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::process::CommandExt;
        // SAFETY: the closure only calls prctl, and resets the handlers this process set.
        unsafe {
            command.pre_exec(|| {
                // Claude dies with this process, so a stop that has to kill it leaves nothing behind.
                libc::prctl(
                    libc::PR_SET_PDEATHSIG,
                    libc::SIGKILL as libc::c_ulong,
                    0,
                    0,
                    0,
                );
                libc::signal(libc::SIGTERM, libc::SIG_DFL);
                libc::signal(libc::SIGINT, libc::SIG_DFL);
                libc::signal(KEEP, libc::SIG_DFL);
                Ok(())
            })
        };
    }
    let child = match command.spawn() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("tico-runner {FLAG}: {}: {e}", argv[0]);
            return 127;
        }
    };
    let pid = child.id() as i32;
    CHILD.store(pid, Ordering::SeqCst);
    // Who Claude is among this parent's children, which come to include what it leaves behind.
    let _ = writeln!(status, "claude {pid}");
    let _ = status.flush();
    // Claude's pipes are Claude's: the runner reads its end of the turn from their closing, which must not wait for
    // this parent.
    if let Ok(null) = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open("/dev/null")
    {
        use std::os::fd::AsRawFd;
        // SAFETY: dup2 onto this process's own standard input and output.
        unsafe {
            libc::dup2(null.as_raw_fd(), 0);
            libc::dup2(null.as_raw_fd(), 1);
        }
    }
    let mut claude: Option<i32> = None;
    loop {
        let mut raw = 0;
        // SAFETY: waits for any child of this process.
        let reaped = unsafe { libc::waitpid(-1, &mut raw, 0) };
        if reaped < 0 {
            if std::io::Error::last_os_error().raw_os_error() == Some(libc::EINTR) {
                continue;
            }
            break; // no child left
        }
        let _ = writeln!(status, "{reaped} {}", code(raw));
        let _ = status.flush();
        if reaped == pid {
            claude = Some(code(raw));
            CHILD.store(0, Ordering::SeqCst);
            if !KEEPING.load(Ordering::SeqCst) {
                break; // what Claude leaves behind goes on as it would without this parent
            }
        }
    }
    match claude {
        Some(c) if c >= 0 => c,
        Some(c) => 128 - c,
        None => 1,
    }
}

/// Claude's pid, from its parent's status file.
pub fn claude(path: &std::path::Path) -> Option<u32> {
    std::fs::read_to_string(path)
        .ok()?
        .lines()
        .find_map(|l| l.strip_prefix("claude ")?.trim().parse().ok())
}

/// The exit codes in a status file, by pid.
pub fn statuses(path: &std::path::Path) -> std::collections::HashMap<u32, i32> {
    std::fs::read_to_string(path)
        .unwrap_or_default()
        .lines()
        .filter_map(|l| {
            l.split_once(' ')
                .and_then(|(p, c)| Some((p.parse().ok()?, c.trim().parse().ok()?)))
        })
        .collect()
}

#[cfg(test)]
pub mod tests {
    use super::*;

    pub const ARGS: &str = "TICO_REAP_TEST_ARGS";

    #[test]
    fn as_parent() {
        let Ok(args) = std::env::var(ARGS) else {
            return;
        };
        let args: Vec<String> = serde_json::from_str(&args).unwrap();
        std::process::exit(main(&args));
    }
}
