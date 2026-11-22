//! What a Claude or Codex turn leaves running when it ends, found and stopped. A tool command's
//! background job, a dev server, a browser or a busy loop the bot never ended would otherwise run on after the turn,
//! on a machine other bots share.
//!
//! Codex uses the scope where available, an environment mark, and recorded ancestry
//! and sessions. Docker reads marks as the bot user while the supervisor verifies and signals
//! candidates. Claude's sweep also preserves its background-wait shells and their reaper.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

/// The turn's mark in the harness's environment: the turn's id.
pub const MARK_ENV: &str = "TICO_TURN_MARK";
/// How long the turn's leftovers have to end on a SIGTERM before they are killed.
const GRACE: Duration = Duration::from_millis(if cfg!(test) { 300 } else { 1000 });
/// SIGKILL rounds after the grace: each catches what the last one's processes forked before they died.
const PASSES: usize = 10;
/// How many distinct commands the turn's report names.
const TOP: usize = 5;
const COMMAND_CHARS: usize = 120;
#[cfg(target_os = "linux")]
const MARK_READ_TIMEOUT: Duration = Duration::from_secs(2);

/// One ended turn: how to know its processes, and what of them stays.
#[derive(Clone, Debug, Default)]
pub struct Turn {
    pub mark: String,
    /// The process groups (and sessions) its harness processes led.
    pub groups: Vec<u32>,
    /// The cgroup directories its harness processes ran in.
    pub cgroups: Vec<PathBuf>,
    pub tracking: Option<Arc<Mutex<Tracking>>>,
    /// Processes that stay, alone.
    pub keep: Vec<u32>,
    /// Processes that stay with everything under them.
    pub keep_trees: Vec<u32>,
}

/// A process the sweep stopped.
#[derive(Clone, Debug, PartialEq)]
pub struct Stopped {
    pub pid: u32,
    pub command: String,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
struct Process {
    pid: u32,
    start: u64,
    #[cfg(target_os = "macos")]
    unique_id: u64,
}

struct Proc {
    id: Process,
    ppid: u32,
    pgid: u32,
    sid: Option<u32>,
    zombie: bool,
    #[cfg(target_os = "macos")]
    parent_id: u64,
    #[cfg(target_os = "macos")]
    uid: u32,
}

#[cfg(target_os = "linux")]
fn process(pid: u32) -> Option<Proc> {
    let stat = std::fs::read(format!("/proc/{pid}/stat")).ok()?;
    let fields: Vec<_> = stat
        .rsplit(|&b| b == b')')
        .next()?
        .split(|b| b.is_ascii_whitespace())
        .filter(|f| !f.is_empty())
        .collect();
    let num = |i: usize| {
        std::str::from_utf8(fields.get(i)?)
            .ok()?
            .parse::<u32>()
            .ok()
    };
    Some(Proc {
        id: Process {
            pid,
            start: std::str::from_utf8(fields.get(19)?).ok()?.parse().ok()?,
        },
        ppid: num(1)?,
        pgid: num(2)?,
        sid: num(3),
        zombie: matches!(*fields.first()?, b"Z" | b"X"),
    })
}

#[cfg(target_os = "linux")]
fn table() -> HashMap<u32, Proc> {
    std::fs::read_dir("/proc")
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|entry| {
            let pid = entry.file_name().to_str()?.parse().ok()?;
            Some((pid, process(pid)?))
        })
        .collect()
}

#[cfg(target_os = "macos")]
fn process(pid: u32) -> Option<Proc> {
    // XNU's PROC_PIDT_BSDINFOWITHUNIQID (proc_info_private.h). The unique ID
    // stays through exec, and an orphan still carries its creator's unique ID.
    #[repr(C)]
    struct Info {
        bsd: libc::proc_bsdinfo,
        uuid: [u8; 16],
        unique_id: u64,
        parent_unique_id: u64,
        version: i32,
        original_parent_version: i32,
        reserved: [u64; 2],
    }
    const BSD_INFO_WITH_UNIQUE_ID: i32 = 18;
    let mut info = std::mem::MaybeUninit::<Info>::uninit();
    // SAFETY: libproc writes at most the given buffer size. Only use a complete result.
    let size = std::mem::size_of::<Info>() as i32;
    let got = unsafe {
        libc::proc_pidinfo(
            pid as i32,
            BSD_INFO_WITH_UNIQUE_ID,
            1,
            info.as_mut_ptr().cast(),
            size,
        )
    };
    if got != size {
        return None;
    }
    let info = unsafe { info.assume_init() };
    let sid = unsafe { libc::getsid(pid as i32) };
    Some(Proc {
        id: Process {
            pid,
            start: info.bsd.pbi_start_tvsec * 1_000_000 + info.bsd.pbi_start_tvusec,
            unique_id: info.unique_id,
        },
        ppid: info.bsd.pbi_ppid,
        pgid: info.bsd.pbi_pgid,
        sid: (sid > 0).then_some(sid as u32),
        zombie: info.bsd.pbi_status == libc::SZOMB,
        parent_id: info.parent_unique_id,
        uid: info.bsd.pbi_uid,
    })
}

#[cfg(target_os = "macos")]
fn table() -> HashMap<u32, Proc> {
    // SAFETY: libproc returns the number of pids, with a null buffer for sizing.
    let n = unsafe { libc::proc_listallpids(std::ptr::null_mut(), 0) };
    let mut pids = vec![0i32; n.max(0) as usize + 64];
    let bytes = std::mem::size_of_val(pids.as_slice()) as i32;
    let n = unsafe { libc::proc_listallpids(pids.as_mut_ptr().cast(), bytes) };
    pids.truncate(n.max(0) as usize);
    pids.into_iter()
        .filter(|&pid| pid > 1)
        .filter_map(|pid| Some((pid as u32, process(pid as u32)?)))
        .collect()
}

#[cfg(any(target_os = "linux", test))]
async fn read_mark_output(
    mut child: tokio::process::Child,
    deadline: Duration,
) -> Result<Vec<u8>, &'static str> {
    use tokio::io::AsyncReadExt;

    let mut stdout = child.stdout.take().expect("piped mark reader stdout");
    let mut output = Vec::new();
    let error = match tokio::time::timeout(deadline, async {
        tokio::try_join!(child.wait(), stdout.read_to_end(&mut output))
    })
    .await
    {
        Ok(Ok((status, _))) if status.success() => return Ok(output),
        Ok(_) => "Could not read Docker turn marks.",
        Err(_) => "Timed out reading Docker turn marks.",
    };
    // The timeout covers both the child's exit and draining the pipe. Closing the pipe and
    // killing the child also handles a helper stopped by another process under the bot UID.
    drop(stdout);
    let _ = child.kill().await;
    Err(error)
}

#[cfg(target_os = "linux")]
fn marks(turn: &Turn, procs: &HashMap<u32, Proc>) -> Result<HashMap<Process, bool>, &'static str> {
    use std::os::unix::fs::MetadataExt;
    if turn.mark.is_empty() {
        return Ok(HashMap::new());
    }
    let prefix = format!("{MARK_ENV}=");
    if let Some((uid, gid)) = crate::isolation::identity().filter(|_| turn.tracking.is_some()) {
        // Docker has no per-turn systemd scope. Read only the mark as the bot UID,
        // with a clean environment and a system interpreter, so no bot code is imported.
        let interpreter = if std::path::Path::new("/usr/local/bin/python3").is_file() {
            "/usr/local/bin/python3"
        } else {
            "/usr/bin/python3"
        };
        let mut reader = crate::isolation::command_as(Some((uid, gid)), interpreter);
        reader
            .args([
                "-I",
                "-S",
                "-c",
                r#"
import os, sys
prefix, expected = (arg.encode() for arg in sys.argv[1:])
for pid in os.listdir('/proc'):
    if not pid.isascii() or not pid.isdecimal():
        continue
    try:
        path = '/proc/' + pid
        if os.stat(path).st_uid != os.geteuid():
            continue
        stat = path + '/stat'
        with open(stat, 'rb') as f:
            start = f.read().rsplit(b')', 1)[1].split()[19]
        with open(path + '/environ', 'rb') as f:
            mark = next((v for v in f.read().split(b'\0') if v.startswith(prefix)), None)
        if mark is not None:
            with open(stat, 'rb') as f:
                if f.read().rsplit(b')', 1)[1].split()[19] == start:
                    print(pid, start.decode(), int(mark == prefix + expected))
    except (OSError, IndexError):
        pass
"#,
                &prefix,
                &turn.mark,
            ])
            .env_clear()
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(true);
        // Sweep runs on a blocking worker, leaving the runtime free to poll the child and timer.
        let output = tokio::runtime::Handle::current().block_on(async {
            let child = reader
                .spawn()
                .map_err(|_| "Could not start the Docker turn mark reader.")?;
            read_mark_output(child, MARK_READ_TIMEOUT).await
        })?;
        // The helper shares the bot UID. Independently check that its replies can
        // only authorize signals to this UID, never to the supervisor or a leased UID.
        return Ok(String::from_utf8_lossy(&output)
            .lines()
            .filter_map(|line| {
                let mut fields = line.split_whitespace();
                let pid = fields.next()?.parse().ok()?;
                let p = procs.get(&pid)?;
                let start = fields.next()?.parse::<u64>().ok()?;
                let ours = match fields.next()? {
                    "0" => false,
                    "1" => true,
                    _ => return None,
                };
                (p.id.start == start
                    && std::fs::metadata(format!("/proc/{pid}")).ok()?.uid() == uid)
                    .then_some((p.id, ours))
            })
            .collect());
    }
    let uid = unsafe { libc::geteuid() };
    Ok(procs
        .values()
        .filter(|p| !p.zombie)
        .filter_map(|p| {
            let dir = PathBuf::from(format!("/proc/{}", p.id.pid));
            if dir.metadata().ok()?.uid() != uid {
                return None;
            }
            let raw = std::fs::read(dir.join("environ")).ok()?;
            let mark = raw
                .split(|b| *b == 0)
                .find_map(|kv| kv.strip_prefix(prefix.as_bytes()))?;
            (process(p.id.pid)?.id == p.id).then_some((p.id, mark == turn.mark.as_bytes()))
        })
        .collect())
}

#[cfg(target_os = "macos")]
fn marks(turn: &Turn, procs: &HashMap<u32, Proc>) -> Result<HashMap<Process, bool>, &'static str> {
    if turn.mark.is_empty() || turn.tracking.is_none() {
        return Ok(HashMap::new());
    }
    let uid = unsafe { libc::geteuid() };
    let prefix = format!("{MARK_ENV}=");
    Ok(procs
        .values()
        .filter(|p| !p.zombie && p.uid == uid)
        .filter_map(|p| {
            let raw = procargs(p.id.pid)?;
            let (argc, entries) = procargs_entries(&raw)?;
            // Match only envp. A command argument can look like a turn mark.
            let mark = entries
                .skip(argc)
                .take_while(|entry| !entry.is_empty())
                .find_map(|entry| entry.strip_prefix(prefix.as_bytes()))?;
            let now = process(p.id.pid)?;
            (now.id == p.id && now.uid == uid && !now.zombie)
                .then_some((p.id, mark == turn.mark.as_bytes()))
        })
        .collect())
}

/// A group or session needs a verified member, or its original leader still held by the reaper.
/// Make a fresh tracker for every app-server incarnation.
#[derive(Debug, Default)]
pub struct Tracking {
    members: HashSet<Process>,
    #[cfg(target_os = "macos")]
    unique_ids: HashSet<u64>,
    #[cfg(target_os = "macos")]
    root_session: Option<u32>,
}

impl Tracking {
    pub fn new(pid: u32) -> Arc<Mutex<Self>> {
        let mut tracking = Self::default();
        if let Some(root) = process(pid) {
            tracking.remember(&root);
            #[cfg(target_os = "macos")]
            {
                tracking.root_session = root.sid.filter(|&sid| sid == pid);
            }
        }
        Arc::new(Mutex::new(tracking))
    }

    fn remember(&mut self, p: &Proc) {
        self.members.insert(p.id);
        #[cfg(target_os = "macos")]
        self.unique_ids.insert(p.id.unique_id);
    }

    fn observe(&mut self, procs: &HashMap<u32, Proc>) {
        loop {
            let mut new = Vec::new();
            let mut groups = HashSet::new();
            let mut sessions = HashSet::new();
            for p in procs
                .values()
                .filter(|p| !p.zombie && self.members.contains(&p.id))
            {
                if process(p.id.pid).is_some_and(|now| {
                    now.id == p.id && now.pgid == p.pgid && now.sid == p.sid && !now.zombie
                }) {
                    groups.insert(p.pgid);
                    sessions.extend(p.sid);
                }
            }
            for p in procs.values().filter(|p| !self.members.contains(&p.id)) {
                #[cfg(target_os = "linux")]
                let descendant = procs.get(&p.ppid).is_some_and(|parent| {
                    parent.id.start <= p.id.start && self.members.contains(&parent.id)
                });
                #[cfg(target_os = "macos")]
                let descendant = self.unique_ids.contains(&p.parent_id);
                if descendant
                    || groups.contains(&p.pgid)
                    || p.sid.is_some_and(|s| sessions.contains(&s))
                {
                    new.push(p);
                }
            }
            if new.is_empty() {
                break;
            }
            for p in new {
                self.remember(p);
            }
        }
    }

    pub fn sample(tracking: &Mutex<Self>) {
        let procs = table();
        tracking.lock().unwrap().observe(&procs);
    }

    #[cfg(target_os = "macos")]
    pub fn sample_before_reap(tracking: &Mutex<Self>) {
        let procs = table();
        let mut tracking = tracking.lock().unwrap();
        if let Some(sid) = tracking.root_session {
            // The unreaped leader pins this session number, even after an intermediate shell exits.
            // Keep identities of the survivors so later scans can follow the session safely.
            for p in procs.values().filter(|p| !p.zombie && p.sid == Some(sid)) {
                if process(p.id.pid).is_some_and(|now| now.id == p.id && now.sid == Some(sid)) {
                    tracking.remember(p);
                }
            }
        }
        tracking.observe(&procs);
    }

    #[cfg(target_os = "macos")]
    pub fn watch(tracking: &Arc<Mutex<Self>>) {
        let tracking = Arc::downgrade(tracking);
        tokio::spawn(async move {
            while let Some(current) = tracking.upgrade() {
                let _ = tokio::task::spawn_blocking(move || Self::sample(&current)).await;
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
        });
    }
}

/// The pids in a cgroup and the cgroups under it.
fn cgroup_pids(dir: &std::path::Path, into: &mut HashSet<u32>) {
    if let Ok(text) = std::fs::read_to_string(dir.join("cgroup.procs")) {
        into.extend(text.lines().filter_map(|l| l.trim().parse::<u32>().ok()));
    }
    for entry in std::fs::read_dir(dir).into_iter().flatten().flatten() {
        if entry.file_type().is_ok_and(|t| t.is_dir()) {
            cgroup_pids(&entry.path(), into);
        }
    }
}

/// Read the full command line so a credential crossing the display limit is still masked.
#[cfg(target_os = "linux")]
fn command_of(pid: u32) -> String {
    std::fs::read(format!("/proc/{pid}/cmdline"))
        .ok()
        .map(|raw| join_args(raw.split(|b| *b == 0)))
        .filter(|l| !l.is_empty())
        .or_else(|| {
            std::fs::read_to_string(format!("/proc/{pid}/comm"))
                .ok()
                .map(|c| c.trim().to_string())
        })
        .unwrap_or_default()
}

fn join_args<'a>(args: impl Iterator<Item = &'a [u8]>) -> String {
    args.filter(|a| !a.is_empty())
        .map(|a| String::from_utf8_lossy(a).into_owned())
        .collect::<Vec<_>>()
        .join(" ")
}

#[cfg(target_os = "macos")]
fn procargs(pid: u32) -> Option<Vec<u8>> {
    let mib = [libc::CTL_KERN, libc::KERN_PROCARGS2, pid as i32];
    let mut size = 0;
    let mut read = |buf: *mut libc::c_void| {
        // SAFETY: mib has three elements, and buf is null or holds size bytes.
        unsafe {
            libc::sysctl(
                mib.as_ptr().cast_mut(),
                3,
                buf,
                &mut size,
                std::ptr::null_mut(),
                0,
            ) == 0
        }
        .then_some(size)
    };
    let n = read(std::ptr::null_mut()).filter(|n| (4..=1 << 20).contains(n))?;
    let mut raw = vec![0u8; n];
    let n = read(raw.as_mut_ptr().cast())?;
    raw.truncate(n);
    Some(raw)
}

#[cfg(target_os = "macos")]
fn procargs_entries(raw: &[u8]) -> Option<(usize, impl Iterator<Item = &[u8]>)> {
    // KERN_PROCARGS2 returns argc, the executable path, padding, argv, then
    // the environment when macOS allows access to it.
    let (argc, path_and_args) = raw.split_first_chunk::<4>()?;
    let argc = u32::from_ne_bytes(*argc) as usize;
    let path_end = path_and_args.iter().position(|b| *b == 0)?;
    let tail = &path_and_args[path_end + 1..];
    let first = tail.iter().position(|b| *b != 0)?;
    Some((argc, tail[first..].split(|b| *b == 0)))
}

#[cfg(target_os = "macos")]
fn command_of(pid: u32) -> String {
    if let Some(raw) = procargs(pid) {
        if let Some((argc, entries)) = procargs_entries(&raw) {
            return join_args(entries.take(argc));
        }
    }
    let mut name = [0; 256];
    // SAFETY: proc_name writes at most the provided capacity.
    unsafe { libc::proc_name(pid as i32, name.as_mut_ptr().cast(), name.len() as u32) };
    String::from_utf8_lossy(name.split(|b| *b == 0).next().unwrap_or_default()).into_owned()
}

fn clip(text: &str, chars: usize) -> String {
    let line = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if line.chars().count() <= chars {
        line
    } else {
        format!("{}…", line.chars().take(chars - 1).collect::<String>())
    }
}

/// The turn's processes still running, spared ones aside.
fn leftovers(
    turn: &Turn,
    procs: &HashMap<u32, Proc>,
    marks: &HashMap<Process, bool>,
) -> Vec<Process> {
    let tracked = turn.tracking.as_ref().map(|tracking| {
        let mut tracking = tracking.lock().unwrap();
        tracking.observe(procs);
        tracking
    });
    let mut cgrouped = HashSet::new();
    for dir in &turn.cgroups {
        cgroup_pids(dir, &mut cgrouped);
    }
    let me = std::process::id();
    // A group's number stays its own while one member is left. Once none is, it can go to a new process, which may
    // lead a group of its own (another turn's harness): a group whose leader's pid is taken by some process other than
    // a spared one names no leftover of this turn.
    let groups: Vec<u32> = turn
        .groups
        .iter()
        .copied()
        .filter(|g| turn.keep.contains(g) || !procs.contains_key(g))
        .collect();
    let spared_tree = |pid: u32| {
        let mut at = pid;
        // Bounded: a pid table read mid-change can hold a cycle.
        for _ in 0..procs.len().min(4096) {
            if turn.keep_trees.contains(&at) {
                return true;
            }
            match procs.get(&at) {
                Some(p) if p.ppid > 1 && p.ppid != at => at = p.ppid,
                _ => return false,
            }
        }
        false
    };
    let mut found: Vec<Process> = procs
        .iter()
        .filter(|(&pid, p)| {
            if pid <= 1 || pid == me || p.zombie || turn.keep.contains(&pid) {
                return false;
            }
            let ours = marks.get(&p.id).copied().unwrap_or_else(|| {
                cgrouped.contains(&pid)
                    || tracked.as_ref().is_some_and(|t| t.members.contains(&p.id))
                    || groups.iter().any(|&g| p.pgid == g || p.sid == Some(g))
            });
            ours && !spared_tree(pid)
        })
        .map(|(_, p)| p.id)
        .collect();
    found.sort_unstable_by_key(|p| p.pid);
    found
}

#[cfg(target_os = "linux")]
fn signal(process_id: Process, signal: i32) -> bool {
    use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
    // Open first, then check the start time. If the PID changed before open the check
    // rejects it, and if it changes after the check the fd still refers to the old process.
    let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, process_id.pid, 0) };
    if fd < 0 {
        return false;
    }
    let fd = unsafe { OwnedFd::from_raw_fd(fd as i32) };
    if !process(process_id.pid).is_some_and(|p| p.id == process_id && !p.zombie) {
        return false;
    }
    unsafe {
        libc::syscall(
            libc::SYS_pidfd_send_signal,
            fd.as_raw_fd(),
            signal,
            std::ptr::null::<libc::siginfo_t>(),
            0,
        ) == 0
    }
}

#[cfg(target_os = "macos")]
fn signal(process_id: Process, signal: i32) -> bool {
    // macOS has no pidfd. Revalidate the kernel start time immediately before kill.
    process(process_id.pid).is_some_and(|p| p.id == process_id && !p.zombie)
        && unsafe { libc::kill(process_id.pid as i32, signal) == 0 }
}

fn scrub_command(command: &str, redactor: Option<&crate::redact::Redactor>) -> String {
    let clean = redactor.map_or(std::borrow::Cow::Borrowed(command), |r| {
        r.scrub_text(command)
    });
    crate::redact::scrub_credentials(&clean, &[])
}

fn running(id: Process) -> bool {
    process(id.pid).is_some_and(|p| p.id == id && !p.zombie)
}

/// Stops what `turn` left running. Blocking, with a short grace and bounded SIGKILL passes.
fn sweep(
    turn: &Turn,
    redactor: Option<&crate::redact::Redactor>,
) -> (Vec<Stopped>, Option<&'static str>) {
    sweep_with_marks(turn, redactor, marks)
}

fn sweep_with_marks(
    turn: &Turn,
    redactor: Option<&crate::redact::Redactor>,
    mut read_marks: impl FnMut(
        &Turn,
        &HashMap<u32, Proc>,
    ) -> Result<HashMap<Process, bool>, &'static str>,
) -> (Vec<Stopped>, Option<&'static str>) {
    let mut mark_error = None;
    let mut known_marks = HashMap::new();
    let mut scan = || {
        let procs = table();
        if mark_error.is_none() {
            match read_marks(turn, &procs) {
                // Keep verified identities for escalation even if a later read times out.
                Ok(found) => known_marks.extend(found),
                Err(error) => mark_error = Some(error),
            }
        }
        leftovers(turn, &procs, &known_marks)
    };
    let mut signaled = Vec::new();
    let mut seen = HashSet::new();
    let mut stop = |p: Process, sig| {
        let command = (!seen.contains(&p)).then(|| command_of(p.pid));
        if signal(p, sig) {
            if let Some(command) = command {
                seen.insert(p);
                signaled.push((p, command));
            }
        }
    };
    let mut left = scan();
    for &p in &left {
        stop(p, libc::SIGTERM);
    }
    let until = Instant::now() + GRACE;
    while !left.is_empty() && Instant::now() < until {
        std::thread::sleep(Duration::from_millis(50));
        // A full scan can read every process's environment. Wait on the known
        // identities first, then look for anything they left behind.
        if !left.iter().copied().any(running) {
            left = scan();
        }
    }
    if !left.is_empty() {
        for _ in 0..PASSES {
            let left = scan();
            if left.is_empty() {
                break;
            }
            for p in left {
                stop(p, libc::SIGKILL);
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }
    let stopped = signaled
        .into_iter()
        .filter(|(id, _)| !running(*id))
        .map(|(id, command)| Stopped {
            pid: id.pid,
            command: clip(&scrub_command(&command, redactor), COMMAND_CHARS),
        })
        .collect();
    (stopped, mark_error)
}

/// Stop the leftovers off the async runtime and prepare the diagnostic.
pub async fn finish(
    target: Turn,
    host: &str,
    redactor: Option<Arc<crate::redact::Redactor>>,
) -> Option<Value> {
    let (stopped, mark_error) =
        tokio::task::spawn_blocking(move || sweep(&target, redactor.as_deref()))
            .await
            .ok()?;
    let mut diagnostic = None;
    if !stopped.is_empty() {
        crate::util::log(&format!(
            "{host}: turn ended: stopped {} processes it left running ({})",
            stopped.len(),
            programs(&stopped)
        ));
        diagnostic = Some(report(&stopped));
    }
    if let Some(error) = mark_error {
        let incomplete = format!("Codex process cleanup incomplete. {error}");
        crate::util::log(&format!("{host}: {incomplete}"));
        let note = diagnostic.get_or_insert_with(|| json!({}));
        note["text"] = json!(match note["text"].as_str() {
            Some(text) => format!("{text} {incomplete}"),
            None => incomplete,
        });
    }
    diagnostic
}

/// What the turn's diagnostics say of a sweep: how many it stopped and its most common commands, each cut short.
/// Commands have already been scrubbed in full.
pub fn report(stopped: &[Stopped]) -> Value {
    let mut counts: Vec<(String, usize)> = vec![];
    for s in stopped {
        let command = clip(&s.command, 80);
        match counts.iter_mut().find(|(c, _)| *c == command) {
            Some((_, n)) => *n += 1,
            None => counts.push((command, 1)),
        }
    }
    // Stable: equal counts keep the order they were found in.
    counts.sort_by_key(|c| std::cmp::Reverse(c.1));
    let top: Vec<Value> = counts
        .iter()
        .take(TOP)
        .map(|(c, n)| json!({"command": c, "count": n}))
        .collect();
    let named = counts
        .iter()
        .take(TOP)
        .map(|(c, n)| format!("{} ({n})", if c.is_empty() { "?" } else { c }))
        .collect::<Vec<_>>()
        .join("; ");
    let n = stopped.len();
    json!({
        "text": format!("Turn ended: stopped {n} process{} it left running: {named}.", if n == 1 { "" } else { "es" }),
        "leftover_processes": {"count": n, "top": top},
    })
}

/// The scrubbed program names alone, for the runner's log.
fn programs(stopped: &[Stopped]) -> String {
    let mut names = Vec::new();
    for s in stopped {
        let first = s.command.split_whitespace().next().unwrap_or("?");
        let name = first.rsplit('/').next().unwrap_or(first);
        if !names.contains(&name) {
            names.push(name);
        }
    }
    names.join(", ")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn alive(pid: u32) -> bool {
        process(pid).is_some_and(|p| !p.zombie)
    }

    fn read_pid(path: &std::path::Path) -> Option<u32> {
        std::fs::read_to_string(path).ok()?.trim().parse().ok()
    }

    fn wait_for(paths: &[std::path::PathBuf]) -> Vec<u32> {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let pids: Vec<Option<u32>> = paths.iter().map(|p| read_pid(p)).collect();
            if pids.iter().all(Option::is_some) || Instant::now() > deadline {
                return pids.into_iter().map(|p| p.expect("a pid file")).collect();
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    /// A long-lived harness with a tool server it started before the turn, and the turn's own processes: a background
    /// job, one that left the session (Linux: `setsid`), and one that double-forked away. After the sweep the turn's
    /// are gone and the harness, its tool server and a process of another turn are not.
    #[test]
    fn a_turns_leftovers_go_and_the_harness_its_tool_server_and_other_turns_stay() {
        let dir =
            std::env::temp_dir().join(format!("turn-sweep-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let setsid = if cfg!(target_os = "linux") {
            "setsid sh -c 'echo $$ > detached.pid; exec sleep 900' </dev/null >/dev/null 2>&1 &\n"
        } else {
            "echo 0 > detached.pid\n"
        };
        let harness = dir.join("harness");
        crate::util::write_script(
            &harness,
            &format!(
                "#!/bin/sh\nsleep 900 </dev/null >/dev/null 2>&1 &\necho $! > mcp.pid\n\
                 sh -c 'while :; do sleep 1; done' </dev/null >/dev/null 2>&1 &\necho $! > loop.pid\n\
                 {setsid}( sleep 900 </dev/null >/dev/null 2>&1 & echo $! > forked.pid )\n\
                 exec sleep 900\n"
            ),
        );
        let mark = uuid::Uuid::new_v4().to_string();
        let mut child = {
            use std::os::unix::process::CommandExt;
            let mut c = std::process::Command::new(&harness);
            c.current_dir(&dir)
                .env(MARK_ENV, &mark)
                .stdin(std::process::Stdio::null());
            // SAFETY: setsid in a just-forked child, as hosts::new_group.
            unsafe {
                c.pre_exec(|| {
                    libc::setsid();
                    Ok(())
                })
            };
            c.spawn().unwrap()
        };
        // Another turn's process, in no group of this one: it carries a mark of its own.
        let mut other = std::process::Command::new("sleep")
            .arg("900")
            .env(MARK_ENV, "another-turn")
            .spawn()
            .unwrap();
        let names = ["mcp.pid", "loop.pid", "detached.pid", "forked.pid"];
        let pids = wait_for(&names.map(|n| dir.join(n)));
        let (mcp, job, detached, forked) = (pids[0], pids[1], pids[2], pids[3]);
        let harness_pid = child.id();
        let turn = Turn {
            mark: mark.clone(),
            groups: vec![harness_pid],
            cgroups: vec![],
            keep: vec![harness_pid, mcp],
            keep_trees: vec![],
            tracking: None,
        };
        let (stopped, _) = sweep(&turn, None);
        let gone: Vec<u32> = stopped.iter().map(|s| s.pid).collect();
        let survivors = (
            alive(harness_pid),
            alive(mcp),
            alive(other.id()),
            alive(job),
            alive(forked),
            detached > 0 && alive(detached),
        );
        let _ = other.kill();
        let _ = other.wait();
        // SAFETY: the group this test's harness leads.
        unsafe { libc::kill(-(harness_pid as i32), libc::SIGKILL) };
        let _ = child.wait();
        for pid in [mcp, job, detached, forked].into_iter().filter(|p| *p > 0) {
            // SAFETY: processes this test's harness started.
            unsafe { libc::kill(pid as i32, libc::SIGKILL) };
        }
        std::fs::remove_dir_all(&dir).unwrap();
        let (harness_up, mcp_up, other_up, job_up, forked_up, detached_up) = survivors;
        assert!(harness_up, "the harness was stopped");
        assert!(mcp_up, "the harness's tool server was stopped");
        assert!(other_up, "another turn's process was stopped");
        assert!(!job_up, "the turn's background job outlived it");
        assert!(!forked_up, "a double-forked job outlived the turn");
        assert!(!detached_up, "a setsid job outlived the turn");
        assert!(gone.contains(&job) && gone.contains(&forked), "{stopped:?}");
        if cfg!(target_os = "linux") {
            assert!(gone.contains(&detached), "{stopped:?}");
        }
        let report = report(&stopped);
        assert_eq!(report["leftover_processes"]["count"], stopped.len());
        assert!(
            report["text"].as_str().unwrap().contains("sleep 900"),
            "{report}"
        );
    }

    #[tokio::test]
    async fn codex_sweeps_and_reports_leftovers_after_reaping_its_app_server() {
        exercise_codex_sweep(false).await;
    }

    #[tokio::test]
    async fn a_crashed_codex_server_is_swept_before_its_replacement_starts() {
        exercise_codex_sweep(true).await;
    }

    #[cfg(target_os = "macos")]
    #[tokio::test]
    async fn a_protected_orphan_is_swept_from_the_session_recorded_at_launch() {
        use std::process::Stdio;
        use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

        let mut command = tokio::process::Command::new("/bin/sh");
        crate::hosts::new_group(&mut command);
        let mut shell = command
            .args([
                "-c",
                "read go; (/bin/sleep 900 </dev/null >/dev/null 2>&1 & echo $!); exit 0",
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let tracking = Tracking::new(shell.id().unwrap());
        let mut stdin = shell.stdin.take().unwrap();
        let mut stdout = BufReader::new(shell.stdout.take().unwrap()).lines();
        let at_exit = tracking.clone();
        let (_stopper, reaped) = crate::hosts::own_with_before_reap(
            shell,
            Some(move || Tracking::sample_before_reap(&at_exit)),
        );
        // The intermediate parent and its job are both created after launch recording.
        // No periodic scan runs in this test, so only the unreaped leader anchors the session.
        stdin.write_all(b"go\n").await.unwrap();
        let pid = stdout.next_line().await.unwrap().unwrap().parse().unwrap();
        assert!(reaped.await.unwrap().unwrap().success());
        let job = process(pid).unwrap();
        let (stopped, _) = sweep(
            &Turn {
                mark: uuid::Uuid::new_v4().to_string(),
                tracking: Some(tracking.clone()),
                ..Default::default()
            },
            None,
        );
        let survived = signal(job.id, libc::SIGKILL);
        assert_eq!(job.ppid, 1, "the job must be orphaned before the sweep");
        assert!(
            !tracking.lock().unwrap().unique_ids.contains(&job.parent_id),
            "the intermediate shell must never be observed"
        );
        assert!(!survived, "the protected job survived");
        assert_eq!(stopped.iter().map(|p| p.pid).collect::<Vec<_>>(), [pid]);
    }

    #[tokio::test]
    async fn a_stopped_mark_reader_times_out_and_is_reaped() {
        let mut child = tokio::process::Command::new("/bin/sh")
            .args(["-c", "kill -STOP $$"])
            .stdout(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        let pid = child.id().unwrap();
        // Wait for the stop without reaping so the deadline always sees a stopped child.
        let stopped = tokio::task::spawn_blocking(move || {
            let mut status = 0;
            (unsafe { libc::waitpid(pid as i32, &mut status, libc::WUNTRACED) } == pid as i32)
                && libc::WIFSTOPPED(status)
        })
        .await
        .unwrap();
        if !stopped {
            let _ = child.kill().await;
        }
        assert!(stopped);
        assert_eq!(
            read_mark_output(child, Duration::from_millis(20)).await,
            Err("Timed out reading Docker turn marks.")
        );
        // Only the reader owns this child. waitpid reports ECHILD once it was reaped.
        let result = unsafe { libc::waitpid(pid as i32, std::ptr::null_mut(), libc::WNOHANG) };
        assert_eq!(result, -1);
        assert_eq!(
            std::io::Error::last_os_error().raw_os_error(),
            Some(libc::ECHILD)
        );
    }

    #[test]
    fn a_mark_scan_timeout_preserves_the_job_for_sigkill() {
        use std::io::{BufRead, BufReader};
        use std::os::unix::process::ExitStatusExt;
        use std::process::{Command, Stdio};

        let mut child = Command::new("/bin/sh")
            .args(["-c", "trap '' TERM; echo ready; exec /bin/sleep 900"])
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let mut ready = String::new();
        BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut ready)
            .unwrap();
        assert_eq!(ready.trim(), "ready");
        let id = process(child.id()).unwrap().id;
        let mut scans = 0;
        let timeout = "Timed out reading Docker turn marks.";
        let (stopped, error) = sweep_with_marks(&Turn::default(), None, |_, _| {
            scans += 1;
            if scans == 1 {
                Ok(HashMap::from([(id, true)]))
            } else {
                Err(timeout)
            }
        });
        let survived = running(id);
        if survived {
            let _ = child.kill();
        }
        let status = child.wait().unwrap();
        assert!(!survived);
        assert_eq!(status.signal(), Some(libc::SIGKILL));
        assert_eq!(stopped.iter().map(|p| p.pid).collect::<Vec<_>>(), [id.pid]);
        assert_eq!(error, Some(timeout));
        assert_eq!(scans, 2);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn a_marked_job_is_swept_after_its_unsampled_shell_exits() {
        use std::os::unix::process::CommandExt;
        use std::process::{Command, Stdio};

        let mark = uuid::Uuid::new_v4().to_string();
        let mut shell = Command::new("/bin/sh");
        shell
            .args([
                "-c",
                r#"python3 -c 'import os,time; print(os.getpid(), flush=True); os.close(1); time.sleep(900)' &"#,
            ])
            .env(MARK_ENV, &mark)
            .stdin(Stdio::null())
            .stderr(Stdio::null());
        // SAFETY: setsid in the just-forked shell, as Codex does for tool commands.
        unsafe {
            shell.pre_exec(|| {
                if libc::setsid() == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            })
        };
        // This returns after the shell exits and the background Python has exec'd and printed its PID.
        let out = shell.output().unwrap();
        let pid = std::str::from_utf8(&out.stdout)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        let job = process(pid).unwrap();
        let (stopped, _) = sweep(
            &Turn {
                mark,
                tracking: Some(Arc::new(Mutex::new(Tracking::default()))),
                ..Default::default()
            },
            None,
        );
        let survived = signal(job.id, libc::SIGKILL);
        assert!(out.status.success());
        assert_eq!(job.ppid, 1, "the shell must exit before the sweep");
        assert!(!survived, "the detached job survived");
        assert_eq!(stopped.iter().map(|p| p.pid).collect::<Vec<_>>(), [pid]);
    }

    async fn exercise_codex_sweep(restart: bool) {
        let dir = std::env::temp_dir().join(format!("turn-sweep-codex-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        #[cfg(target_os = "linux")]
        if let Some((uid, gid)) = crate::isolation::identity() {
            std::os::unix::fs::lchown(&dir, Some(uid), Some(gid)).unwrap();
        }
        let pids_file = dir.join("pids.json");
        crate::util::write_script(
            &dir.join("codex"),
            r#"#!/usr/bin/env python3
import json, os, subprocess, sys
for line in sys.stdin:
    msg = json.loads(line)
    if 'id' not in msg:
        continue
    result = {}
    if msg['method'] == 'thread/start':
        # Apply the shell's explicit environment, without inheriting the app-server's mark.
        env = msg['params']['config']['shell_environment_policy']['set']
        assert env['TICO_TURN_MARK'] == os.environ['TICO_TURN_MARK'] != 'old-mark'
        pids = [os.getpid()]
        cwd = os.path.dirname(os.environ['FAKE_PIDS'])
        with open(os.path.join(os.environ['CODEX_HOME'], 'auth.json'), 'w') as f:
            json.dump({'tokens': {'refresh_token': os.environ['FAKE_SECRET']}}, f)
        os.mkfifo(os.path.join(cwd, 'hold'))
        # A plain tool, a job in a new group of the app-server session, and a
        # new session like Codex's pipe/PTY executor. On macOS these have no mark.
        for setup in ({}, {'preexec_fn': lambda: os.setpgid(0, 0)}, {'start_new_session': True}):
            args = ['/bin/sleep', '900'] if setup else [
                '/bin/sh', '-c', 'read x < hold', os.environ['FAKE_SECRET']]
            child = subprocess.Popen(args, cwd=cwd,
                                     env=env if sys.platform == 'linux' else {},
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     **setup)
            pids.append(child.pid)
        with open(os.environ['FAKE_PIDS'], 'w') as f:
            json.dump(pids, f)
        result = {'thread': {'id': 'th'}}
    print(json.dumps({'id': msg['id'], 'result': result}), flush=True)
"#,
        );
        let secret = "rt_synthetic_refresh_token_example";
        let env: crate::env::Env = [
            (
                "PATH".into(),
                format!("{}:{}", dir.display(), std::env::var("PATH").unwrap()),
            ),
            ("CODEX_HOME".into(), dir.display().to_string()),
            ("FAKE_PIDS".into(), pids_file.display().to_string()),
            ("FAKE_SECRET".into(), secret.into()),
            (MARK_ENV.into(), "old-mark".into()),
        ]
        .into();
        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        let codex = crate::hosts::codex::CodexHost::on_home(env.clone(), tx, None);
        let host = crate::hosts::Host::Codex(codex.clone());
        host.set_attempt("attempt", Some(Arc::new(crate::redact::Redactor::new(&[]))));
        host.start().await.unwrap();
        host.start_thread(
            "ops",
            &crate::hosts::Settings {
                env,
                ..Default::default()
            },
        )
        .await
        .unwrap();
        let pids: Vec<u32> = serde_json::from_slice(&std::fs::read(pids_file).unwrap()).unwrap();
        #[cfg(target_os = "linux")]
        if crate::isolation::enabled() {
            let err = std::fs::read(format!("/proc/{}/environ", pids[3])).unwrap_err();
            assert_eq!(err.kind(), std::io::ErrorKind::PermissionDenied);
        }
        let identities: Vec<_> = pids.iter().map(|&pid| process(pid).unwrap().id).collect();
        let mut other = crate::isolation::std_command("/bin/sleep")
            .arg("900")
            .env(MARK_ENV, "another-turn")
            .spawn()
            .unwrap();
        let before_replacement = if restart {
            assert!(signal(identities[0], libc::SIGKILL));
            tokio::time::timeout(Duration::from_secs(3), async {
                while codex.alive() {
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            })
            .await
            .unwrap();
            host.start().await.unwrap();
            pids.iter()
                .copied()
                .filter(|&pid| alive(pid))
                .collect::<Vec<_>>()
        } else {
            Vec::new()
        };
        #[cfg(target_os = "linux")]
        if crate::isolation::enabled() && !restart {
            assert!(signal(identities[0], libc::SIGKILL));
            tokio::time::timeout(Duration::from_secs(3), async {
                while alive(pids[0]) {
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            })
            .await
            .unwrap();
        }
        let reports = codex.finish_turn().await;
        let survivors: Vec<_> = identities
            .into_iter()
            .filter(|&p| signal(p, libc::SIGKILL))
            .collect();
        let other_stayed = other.try_wait().unwrap().is_none();
        let _ = other.kill();
        let _ = other.wait();
        std::fs::remove_dir_all(dir).unwrap();
        assert!(
            before_replacement.is_empty(),
            "old processes survived replacement: {before_replacement:?}"
        );
        assert!(survivors.is_empty(), "processes survived: {survivors:?}");
        assert!(other_stayed, "another process was stopped");
        assert_eq!(reports.len(), 1, "{reports:?}");
        let report = &reports[0];
        assert_eq!(report["leftover_processes"]["count"], pids.len() - 1);
        assert_eq!(
            report["leftover_processes"]["top"][0],
            json!({"command": "/bin/sleep 900", "count": 2})
        );
        let text = report.to_string();
        assert!(text.contains(crate::redact::MASK), "{text}");
        assert!(!text.contains("rt_synthetic"), "{text}");
        assert!(codex.finish_turn().await.is_empty());
    }

    #[test]
    fn a_reused_pid_is_not_signaled() {
        let mut child = std::process::Command::new("/bin/sleep")
            .arg("900")
            .spawn()
            .unwrap();
        let current = process(child.id()).unwrap().id;
        let stale = Process {
            start: current.start - 1,
            ..current
        };
        let signaled = signal(stale, libc::SIGKILL);
        let still_running = child.try_wait().unwrap().is_none();
        let _ = child.kill();
        let _ = child.wait();
        assert!(!signaled && still_running);
    }

    #[test]
    fn the_diagnostic_masks_known_and_shaped_secrets_across_the_display_limit() {
        let secret = "synthetic short-prefix long-tail ".repeat(4);
        let r = crate::redact::Redactor::new(std::slice::from_ref(&secret));
        let leading = format!("tool {} ", ".".repeat(59));
        let commands = [
            (format!("{leading}{secret}end"), Some(&r)),
            (format!("{leading}token={}", "abcd".repeat(33)), None),
        ];
        let stopped: Vec<_> = commands
            .into_iter()
            .map(|(cmd, redactor)| Stopped {
                pid: 42,
                command: scrub_command(&cmd, redactor),
            })
            .collect();
        let report = report(&stopped).to_string();
        assert!(!report.contains("synthetic"), "{report}");
        assert!(!report.contains("abcd"), "{report}");
        assert!(report.contains(crate::redact::MASK), "{report}");
    }

    #[cfg(target_os = "linux")]
    #[tokio::test]
    #[ignore = "requires the disposable runner container and its production UID and capability split"]
    async fn docker_codex_sweeps_the_bot_uid_without_supervisor_environ_access() {
        assert_eq!(std::env::var("TICO_TEST_DISPOSABLE").as_deref(), Ok("1"));
        assert_eq!(unsafe { libc::geteuid() }, 10002);
        assert_eq!(crate::isolation::identity(), Some((10003, 10002)));
        let status = std::fs::read_to_string("/proc/self/status").unwrap();
        let caps = status
            .lines()
            .find_map(|line| line.strip_prefix("CapEff:"))
            .unwrap();
        assert_eq!(u64::from_str_radix(caps.trim(), 16).unwrap(), 0xe3);
        exercise_codex_sweep(false).await;
    }

    /// A kept shell stays with what it runs; the reaping parent stays alone.
    #[test]
    fn a_kept_tree_stays_whole() {
        use std::os::unix::process::CommandExt;

        let dir =
            std::env::temp_dir().join(format!("turn-sweep-keep-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let shell = dir.join("shell");
        crate::util::write_script(
            &shell,
            "#!/bin/sh\nsleep 900 </dev/null >/dev/null 2>&1 &\necho $! > worker.pid\nwhile :; do sleep 1; done\n",
        );
        let mark = uuid::Uuid::new_v4().to_string();
        let mut kept = std::process::Command::new(&shell)
            .current_dir(&dir)
            .env(MARK_ENV, &mark)
            .process_group(0)
            .spawn()
            .unwrap();
        let mut stray = std::process::Command::new("sleep")
            .arg("900")
            .env(MARK_ENV, &mark)
            .process_group(kept.id() as i32)
            .spawn()
            .unwrap();
        let worker = wait_for(&[dir.join("worker.pid")])[0];
        let (stopped, _) = sweep(
            &Turn {
                mark,
                groups: vec![kept.id()],
                keep: vec![kept.id()],
                keep_trees: vec![kept.id()],
                ..Default::default()
            },
            None,
        );
        let _ = stray.kill();
        let _ = stray.wait();
        let (kept_up, worker_up) = (alive(kept.id()), alive(worker));
        let _ = kept.kill();
        let _ = kept.wait();
        // SAFETY: the kept shell's own job.
        unsafe { libc::kill(worker as i32, libc::SIGKILL) };
        std::fs::remove_dir_all(&dir).unwrap();
        assert!(
            kept_up && worker_up,
            "the kept shell or its job was stopped"
        );
        assert_eq!(
            stopped.iter().map(|s| s.pid).collect::<Vec<_>>(),
            vec![stray.id()]
        );
    }

    /// The turn's harness is gone and its pid now belongs to another turn's harness, which leads a group of its own
    /// under that number: nothing of that group is this turn's.
    #[test]
    fn a_group_whose_leaders_pid_went_to_another_process_is_left_alone() {
        let dir = std::env::temp_dir().join(format!(
            "turn-sweep-reuse-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let harness = dir.join("harness");
        crate::util::write_script(
            &harness,
            "#!/bin/sh\nsleep 900 </dev/null >/dev/null 2>&1 &\necho $! > tool.pid\nexec sleep 900\n",
        );
        let mut other = {
            use std::os::unix::process::CommandExt;
            let mut c = std::process::Command::new(&harness);
            c.current_dir(&dir).stdin(std::process::Stdio::null());
            // SAFETY: setsid in a just-forked child, as hosts::new_group.
            unsafe {
                c.pre_exec(|| {
                    libc::setsid();
                    Ok(())
                })
            };
            c.spawn().unwrap()
        };
        let tool = wait_for(&[dir.join("tool.pid")])[0];
        let (stopped, _) = sweep(
            &Turn {
                mark: uuid::Uuid::new_v4().to_string(),
                groups: vec![other.id()],
                ..Default::default()
            },
            None,
        );
        let (leader_up, tool_up) = (alive(other.id()), alive(tool));
        // SAFETY: the group this test's harness leads.
        unsafe { libc::kill(-(other.id() as i32), libc::SIGKILL) };
        let _ = other.wait();
        std::fs::remove_dir_all(&dir).unwrap();
        assert!(stopped.is_empty(), "{stopped:?}");
        assert!(leader_up && tool_up, "another turn's group was stopped");
    }

    #[test]
    fn the_report_counts_and_cuts_commands_and_the_log_names_programs_only() {
        let stopped: Vec<Stopped> = (0..18)
            .map(|i| Stopped {
                pid: 100 + i,
                command: "sh -c while :; do :; done".into(),
            })
            .chain([Stopped {
                pid: 7,
                command: format!("/usr/bin/node server.js --token {}", "x".repeat(200)),
            }])
            .collect();
        let r = report(&stopped);
        assert_eq!(r["leftover_processes"]["count"], 19);
        assert_eq!(
            r["leftover_processes"]["top"][0],
            json!({"command": "sh -c while :; do :; done", "count": 18})
        );
        assert!(
            r["leftover_processes"]["top"][1]["command"]
                .as_str()
                .unwrap()
                .chars()
                .count()
                <= 80
        );
        assert!(r["text"].as_str().unwrap().starts_with(
            "Turn ended: stopped 19 processes it left running: sh -c while :; do :; done (18);"
        ));
        assert_eq!(programs(&stopped), "sh, node");
    }
}
