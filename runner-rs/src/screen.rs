//! The computer screen (capability `screen_v1`): a virtual display people can watch, and with the hub's
//! `control` mode drive, from the web app. Off unless this computer's owner turned it on here: `TICO_SCREEN=1`, or
//! `tico-runner screen on` (runner.json `screen: true`). A cloud machine (cloud_machine.rs) is on by default, unless
//! runner.json says `screen` or TICO_SCREEN is set. The hub keeps its own off/view/control setting on top.
//!
//! While on, on Linux, the runner supervises an `Xvfb` on the first free display from `:99` (or exactly
//! `TICO_SCREEN_DISPLAY`'s), a window manager and two `x11vnc`, one taking input and one
//! view-only. Neither listens on TCP: each serves a unix socket in a fresh directory only the runner's user can enter,
//! so no bot and no other VNC server can stand in for either. That holds only where bots run as another user: on a
//! cloud machine they share the runner's user (cloud_machine.rs, docs/cloud-machines.md), who can enter that
//! directory, so a bot there can reach both sockets directly. The hub's mode still decides who may open a session.
//! They run with that directory as HOME, a clean
//! environment, and x11vnc's rc files, external commands and remote control off, so nothing a bot writes reaches
//! them. A session the hub opens (the stream's `screen_open`, or the 10 s poll of `runners/me/screen/sessions`
//! while on) is one websocket to the hub, with the runner's own token, piped to the socket its `control` flag picks
//! while that x11vnc is the one this runner started and still runs; the runner, not the browser, decides which. Bot turns get that `DISPLAY` while the
//! display is up, so headed browsers and computer use draw where people can see them.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::process::{Child, Command};
use tokio::sync::{watch, Notify};

use crate::client::{bearer, Client, CLIENT_VERSION};
use crate::util::{log, text_of};

pub const CAPABILITY: &str = "screen_v1";
/// The display a screen tries first; when another X server holds it, the next free one up to LAST_DISPLAY.
const FIRST_DISPLAY: u32 = 99;
const LAST_DISPLAY: u32 = 120;
/// Where X servers keep their lock files (`.X<n>-lock`) and sockets (`.X11-unix/X<n>`).
const X_ROOT: &str = "/tmp";
/// Stands for the chosen display in a program's arguments.
const AT_DISPLAY: &str = "{display}";
pub const WIDTH: u32 = 1440;
pub const HEIGHT: u32 = 900;
/// The VNC endpoints, as sockets in the private directory.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Endpoint {
    Control,
    View,
}

impl Endpoint {
    fn socket(self) -> &'static str {
        match self {
            Endpoint::Control => "control.sock",
            Endpoint::View => "view.sock",
        }
    }
}
const TOOLS: &[&str] = &["Xvfb", "x11vnc"];
/// Either window manager will do. matchbox (under 1 MB) is what the images and install.sh put in: it shows one
/// window at a time, full screen, which suits a viewer; openbox costs about 120 MB more (imlib2, Python).
const WINDOW_MANAGERS: &[&str] = &["matchbox-window-manager", "openbox"];

fn window_manager() -> Option<&'static str> {
    let path = std::env::var("PATH").unwrap_or_default();
    WINDOW_MANAGERS.iter().copied().find(|w| crate::env::which(w, &path).is_some())
}
const LOCK_EVERY: Duration = Duration::from_secs(5);
/// The hub fails a session no computer answered within 30 s; the stream's word is the fast path.
const POLL_EVERY: Duration = Duration::from_secs(10);
/// The hub ends a session at 60 minutes; this is the runner's own backstop.
const SESSION_CAP: Duration = Duration::from_secs(61 * 60);
/// How often a computer with its screen on asks whether the hub takes screens (a hub update can add it).
const HUB_EVERY: Duration = Duration::from_secs(300);
const BACKOFF_MAX_S: u64 = 60;
/// Xvfb failing this many starts in a row is reported as the reason the screen is unavailable.
const XVFB_GIVE_UP: u32 = 3;
/// How long a new Xvfb has to serve its display before it counts as a failed start.
const XVFB_WAIT: Duration = Duration::from_secs(10);

// ------------------------------------------------------------------------------------------ the local lock

/// Whether the owner turned the screen on here, and what says so. `TICO_SCREEN`, when set, wins over runner.json. On a
/// cloud machine (`cloud`) with no `screen` key in runner.json, the screen is on by default.
pub fn lock_from(config: Option<&Value>, env: Option<&str>, cloud: bool) -> (bool, &'static str) {
    let word = |s: &str| matches!(s.trim().to_ascii_lowercase().as_str(), "1" | "true" | "on" | "yes");
    if let Some(e) = env.filter(|e| !e.trim().is_empty()) {
        return (word(e), "TICO_SCREEN");
    }
    if config.is_none() && cloud {
        return (true, "cloud machine");
    }
    let on = match config {
        Some(Value::Bool(b)) => *b,
        Some(Value::String(s)) => word(s),
        Some(Value::Number(n)) => n.as_i64() == Some(1),
        _ => false,
    };
    (on, "runner config")
}

/// The lock as it stands now, read from the registration file (so `screen on|off` reaches a running runner).
pub fn lock(config_path: &Path) -> (bool, &'static str) {
    let data: Value = std::fs::read_to_string(config_path).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(Value::Null);
    lock_from(data.get("screen"), std::env::var("TICO_SCREEN").ok().as_deref(), crate::cloud_machine::detected())
}

/// The programs a screen needs that are not on PATH.
pub fn missing_tools() -> Vec<&'static str> {
    let path = std::env::var("PATH").unwrap_or_default();
    let mut missing: Vec<&'static str> = TOOLS.iter().copied().filter(|t| crate::env::which(t, &path).is_none()).collect();
    if window_manager().is_none() {
        missing.push("a window manager");
    }
    missing
}

/// The readiness `screen` field: None when off; available with its size, or not with a short reason.
pub fn report(on: bool, os: &str, missing: &[&str], failure: Option<&str>, vnc_up: bool) -> Option<Value> {
    if !on {
        return None;
    }
    let reason = if os != "linux" {
        Some("Linux only for now".to_string())
    } else if !missing.is_empty() {
        Some(format!("Missing {}", missing.join(", ")))
    } else {
        failure.map(String::from).or_else(|| (!vnc_up).then(|| "VNC is not running".to_string()))
    };
    Some(match reason {
        Some(r) => json!({"available": false, "reason": r}),
        None => json!({"available": true, "width": WIDTH, "height": HEIGHT}),
    })
}

/// A readiness `screen` field offers the capability only when it says available.
pub fn offers(report: Option<&Value>) -> bool {
    report.is_some_and(|r| r["available"] == true)
}

/// The VNC endpoint a hub session pipes to: input only when the hub said `control: true`.
pub fn endpoint_for(session: &Value) -> Endpoint {
    if session["control"] == true {
        Endpoint::Control
    } else {
        Endpoint::View
    }
}

// ------------------------------------------------------------------------------------------ shared state

struct Shared {
    on: AtomicBool,
    up: AtomicBool,
    /// Each x11vnc this runner started is running and serving its socket.
    control_up: AtomicBool,
    view_up: AtomicBool,
    /// The private directory (0700, made fresh each time the display starts) holding the sockets and HOME.
    dir: Mutex<Option<PathBuf>>,
    /// The hub takes `screen_v1` and readiness `screen` (`runners/desired` features): an older one refuses both.
    hub: AtomicBool,
    failure: Mutex<Option<String>>,
    /// The display number the running Xvfb serves, chosen each time it starts.
    display: Mutex<Option<u32>>,
    missing: Mutex<Vec<&'static str>>,
    /// Sessions with a bridge running here, so a poll never opens a second one.
    bridging: Mutex<HashSet<String>>,
    wake: Notify,
    switch: watch::Sender<bool>,
}

fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| Shared {
        on: AtomicBool::new(false),
        up: AtomicBool::new(false),
        control_up: AtomicBool::new(false),
        view_up: AtomicBool::new(false),
        dir: Mutex::new(None),
        hub: AtomicBool::new(false),
        failure: Mutex::new(None),
        display: Mutex::new(None),
        missing: Mutex::new(vec![]),
        bridging: Mutex::new(HashSet::new()),
        wake: Notify::new(),
        switch: watch::channel(false).0,
    })
}

/// This heartbeat's readiness `screen` field.
pub fn readiness() -> Option<Value> {
    let s = shared();
    if !s.hub.load(Ordering::SeqCst) {
        return None;
    }
    let failure = s.failure.lock().unwrap().clone();
    let vnc_up = s.control_up.load(Ordering::SeqCst) && s.view_up.load(Ordering::SeqCst);
    report(s.on.load(Ordering::SeqCst), std::env::consts::OS, &s.missing.lock().unwrap(), failure.as_deref(), vnc_up)
}

/// `DISPLAY` for a bot turn while the virtual display is up.
pub fn display() -> Option<String> {
    let s = shared();
    let n = *s.display.lock().unwrap();
    n.filter(|_| s.up.load(Ordering::SeqCst)).map(|n| format!(":{n}"))
}

/// The event stream said the hub opened a session: poll for it now.
pub fn heard() {
    shared().wake.notify_one();
}

// ------------------------------------------------------------------------------------------ the supervisor

struct Proc {
    name: &'static str,
    args: Vec<String>,
    /// An x11vnc's endpoint: it serves only that socket in the private directory.
    socket: Option<Endpoint>,
    child: Option<Child>,
    started: Option<Instant>,
    failures: u32,
    next: Instant,
}

impl Proc {
    fn new(name: &'static str, args: &[&str]) -> Proc {
        Proc { name, args: args.iter().map(|a| a.to_string()).collect(), socket: None, child: None, started: None, failures: 0, next: Instant::now() }
    }

    fn vnc(socket: Endpoint, args: &[&str]) -> Proc {
        Proc { socket: Some(socket), ..Proc::new("x11vnc", args) }
    }

    /// Running, and (for an x11vnc) its socket is there.
    fn serving(&mut self, dir: &Path) -> bool {
        self.running() && self.socket.is_none_or(|e| dir.join(e.socket()).exists())
    }

    fn running(&mut self) -> bool {
        let Some(child) = self.child.as_mut() else { return false };
        match child.try_wait() {
            Ok(None) => true,
            other => {
                let lived = self.started.map(|s| s.elapsed()).unwrap_or_default();
                let status = other.ok().flatten().map(|s| s.to_string()).unwrap_or_else(|| "unknown".into());
                self.child = None;
                // A long run resets the backoff; quick deaths back off to BACKOFF_MAX_S.
                self.failures = if lived > Duration::from_secs(60) { 1 } else { self.failures + 1 };
                let wait = 1u64 << self.failures.min(6);
                self.next = Instant::now() + Duration::from_secs(wait.min(BACKOFF_MAX_S));
                log(&format!("Tico runner: screen: {} exited ({status}); starting it again in {}s", self.name, wait.min(BACKOFF_MAX_S)));
                false
            }
        }
    }

    fn start(&mut self, dir: &Path, display: &str) {
        if Instant::now() < self.next {
            return;
        }
        let mut cmd = Command::new(self.name);
        cmd.args(self.args.iter().map(|a| if a == AT_DISPLAY { display } else { a.as_str() }));
        if let Some(endpoint) = self.socket {
            let path = dir.join(endpoint.socket());
            let _ = std::fs::remove_file(&path); // one a previous x11vnc of ours left
            // `-rfbport 0` alone still leaves an IPv6 listener on 5900 on every interface.
            cmd.args(["-rfbport", "0", "-rfbportv6", "-1", "-unixsock"]).arg(&path);
        }
        // Nothing of the runner's environment (its token, the bots' settings) and nothing a bot can write.
        cmd.env_clear()
            .env("PATH", std::env::var("PATH").unwrap_or_else(|_| "/usr/bin:/bin".into()))
            .env("HOME", dir)
            .env("DISPLAY", display)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(true);
        #[cfg(target_os = "linux")]
        unsafe {
            // A runner that dies without stopping them takes its display with it, so the next one can have its display.
            cmd.pre_exec(|| {
                libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
                Ok(())
            });
        }
        match cmd.spawn() {
            Ok(child) => {
                self.child = Some(child);
                self.started = Some(Instant::now());
            }
            Err(e) => {
                self.failures += 1;
                self.next = Instant::now() + Duration::from_secs(BACKOFF_MAX_S);
                log(&format!("Tico runner: screen: cannot start {} ({e})", self.name));
            }
        }
    }

    /// Stop the one process this started (never anything found by name).
    async fn stop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.start_kill();
            let _ = tokio::time::timeout(Duration::from_secs(5), child.wait()).await;
        }
        self.failures = 0;
        self.next = Instant::now();
    }
}

// ------------------------------------------------------------------------------------------ the display

fn display_socket(root: &Path, n: u32) -> PathBuf {
    root.join(".X11-unix").join(format!("X{n}"))
}

/// The pid an X server wrote in its lock file (ten columns and a newline).
fn lock_pid(root: &Path, n: u32) -> Option<i32> {
    std::fs::read_to_string(root.join(format!(".X{n}-lock"))).ok()?.trim().parse().ok().filter(|p| *p > 0)
}

fn alive(pid: i32) -> bool {
    // Signal 0 only checks; EPERM is a live process of another user.
    let sent = unsafe { libc::kill(pid, 0) };
    sent == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

/// Some process serves the display's socket, by path or by the abstract name a Linux X server also takes. A socket
/// file left by a server that is gone is no one's. Without /proc, any socket file counts.
fn listening(root: &Path, n: u32) -> bool {
    let path = display_socket(root, n).display().to_string();
    let named = format!("@{path}");
    match std::fs::read_to_string("/proc/net/unix") {
        Ok(table) => table.lines().any(|l| l.split_whitespace().nth(7).is_some_and(|p| p == path || p == named)),
        Err(_) => Path::new(&path).exists(),
    }
}

/// Who holds a display: a live process named in its lock file, or one serving its socket.
#[derive(Debug, PartialEq)]
struct Holder {
    pid: Option<i32>,
    name: Option<String>,
}

fn holder(root: &Path, n: u32) -> Option<Holder> {
    let pid = lock_pid(root, n).filter(|p| alive(*p));
    if pid.is_none() && !listening(root, n) {
        return None;
    }
    let name = pid.and_then(|p| std::fs::read_to_string(format!("/proc/{p}/comm")).ok()).map(|c| c.trim().to_string()).filter(|c| !c.is_empty());
    Some(Holder { pid, name })
}

fn in_use(n: u32, holder: &Holder) -> String {
    let who = match (&holder.name, holder.pid) {
        (Some(name), Some(pid)) => format!(" ({name}, pid {pid})"),
        (None, Some(pid)) => format!(" (pid {pid})"),
        _ => String::new(),
    };
    format!("Display :{n} is in use by another X server{who}. Stop it or set TICO_SCREEN_DISPLAY to a free display.")
}

/// `TICO_SCREEN_DISPLAY`: unset or blank searches; otherwise exactly that display, a colon and a number.
fn display_override(env: Option<&str>) -> Result<Option<u32>, String> {
    let Some(value) = env.map(str::trim).filter(|v| !v.is_empty()) else { return Ok(None) };
    let digits = value.strip_prefix(':').filter(|d| (1..=5).contains(&d.len()) && d.bytes().all(|b| b.is_ascii_digit()));
    match digits.and_then(|d| d.parse().ok()) {
        Some(n) => Ok(Some(n)),
        None => Err("TICO_SCREEN_DISPLAY is not a display: write a colon and a number, like :101".into()),
    }
}

/// The display a new Xvfb takes: `TICO_SCREEN_DISPLAY`'s, or the first free one from FIRST_DISPLAY to LAST_DISPLAY.
fn choose(root: &Path, env: Option<&str>) -> Result<u32, String> {
    if let Some(n) = display_override(env)? {
        return match holder(root, n) {
            Some(h) => Err(in_use(n, &h)),
            None => Ok(n),
        };
    }
    (FIRST_DISPLAY..=LAST_DISPLAY).find(|n| holder(root, *n).is_none()).ok_or_else(|| {
        format!("Displays :{FIRST_DISPLAY} to :{LAST_DISPLAY} are all in use by other X servers. Stop one or set TICO_SCREEN_DISPLAY to a free display.")
    })
}

/// The display's server is this Xvfb: its lock file names our child (an X server writes it before its socket).
fn ours(root: &Path, n: u32, pid: Option<u32>) -> bool {
    pid.is_some_and(|p| lock_pid(root, n) == i32::try_from(p).ok())
}

/// x11vnc without its rc file, external commands, remote control (over X properties, which any X client can set)
/// or name lookups. Access is the socket's directory.
const VNC_ARGS: &[&str] = &["-display", AT_DISPLAY, "-norc", "-nocmds", "-noremote", "-safer", "-nolookup", "-shared", "-forever", "-nopw", "-quiet"];

fn children() -> Vec<Proc> {
    let size = format!("{WIDTH}x{HEIGHT}x24");
    let view: Vec<&str> = VNC_ARGS.iter().copied().chain(["-viewonly"]).collect();
    vec![
        Proc::new("Xvfb", &[AT_DISPLAY, "-screen", "0", &size, "-nolisten", "tcp"]),
        Proc::new(window_manager().unwrap_or(WINDOW_MANAGERS[0]), &[]),
        Proc::vnc(Endpoint::Control, VNC_ARGS),
        Proc::vnc(Endpoint::View, &view),
    ]
}

/// A new directory only this runner's user can enter: `/tmp/tico-screen-<random>`, 0700, never one that existed.
fn private_dir() -> std::io::Result<PathBuf> {
    use std::os::unix::fs::DirBuilderExt;
    let dir = std::env::temp_dir().join(format!("tico-screen-{}", uuid::Uuid::new_v4().simple()));
    std::fs::DirBuilder::new().mode(0o700).create(&dir)?;
    Ok(dir)
}

static STOP: Notify = Notify::const_new();
static DONE: Notify = Notify::const_new();
static STARTED: AtomicBool = AtomicBool::new(false);

/// Follow the local lock for as long as the runner runs: start and restart the display while on (Linux), stop
/// it and every bridge when turned off, and open the hub's sessions.
pub fn start(config_path: PathBuf, client: Client) {
    STARTED.store(true, Ordering::SeqCst);
    tokio::spawn(sessions(client.clone()));
    tokio::spawn(async move {
        let s = shared();
        let mut procs = children();
        let mut said = false;
        let mut asked: Option<Instant> = None;
        loop {
            let (on, _) = lock(&config_path);
            if !on {
                asked = None;
            } else if asked.is_none_or(|at| at.elapsed() >= HUB_EVERY) {
                // A blip keeps what the hub last said; a hub without `runners/desired` takes no screen.
                match client.get("runners/desired").await {
                    Ok(answer) => s.hub.store(answer["features"]["screen_v1"] == true, Ordering::SeqCst),
                    Err(e) if e.status == 404 || e.status == 405 => s.hub.store(false, Ordering::SeqCst),
                    Err(_) => {}
                }
                asked = Some(Instant::now());
            }
            let missing = if on { missing_tools() } else { vec![] };
            *s.missing.lock().unwrap() = missing.clone();
            let usable = on && std::env::consts::OS == "linux" && missing.is_empty();
            if on != s.on.swap(on, Ordering::SeqCst) {
                log(&format!("Tico runner: screen turned {} on this computer", if on { "on" } else { "off" }));
            }
            if usable {
                said = false;
                tick(&mut procs).await;
            } else {
                if on && !said {
                    said = true;
                    log(&format!("Tico runner: screen is on but unavailable: {}", text_of(&readiness().unwrap_or_default()["reason"])));
                }
                stop_all(&mut procs).await;
                procs = children(); // the window manager is looked up again when next turned on
            }
            s.switch.send_replace(s.up.load(Ordering::SeqCst));
            tokio::select! {
                _ = tokio::time::sleep(LOCK_EVERY) => {}
                _ = STOP.notified() => break,
            }
        }
        stop_all(&mut procs).await;
        s.switch.send_replace(false);
        DONE.notify_one();
    });
}

async fn tick(procs: &mut [Proc]) {
    tick_in(procs, Path::new(X_ROOT), XVFB_WAIT).await
}

/// One supervisor step, with X servers' files under `root`.
async fn tick_in(procs: &mut [Proc], root: &Path, wait: Duration) {
    let s = shared();
    let dir = s.dir.lock().unwrap().clone();
    let dir = match dir {
        Some(d) => d,
        None => match private_dir() {
            Ok(d) => {
                *s.dir.lock().unwrap() = Some(d.clone());
                d
            }
            Err(e) => {
                *s.failure.lock().unwrap() = Some(format!("cannot make the screen's directory ({e})"));
                return;
            }
        },
    };
    let (xvfb, rest) = procs.split_first_mut().unwrap();
    if !xvfb.running() {
        s.up.store(false, Ordering::SeqCst);
        for p in rest.iter_mut() {
            p.stop().await; // they draw on a display that is gone
        }
        // A display another X server holds is never started on, so its socket is never taken for ours.
        let n = match choose(root, std::env::var("TICO_SCREEN_DISPLAY").ok().as_deref()) {
            Ok(n) => n,
            Err(reason) => {
                note(reason);
                return;
            }
        };
        if xvfb.failures < XVFB_GIVE_UP {
            *s.failure.lock().unwrap() = None; // a display that was in use may be free now
        }
        *s.display.lock().unwrap() = Some(n);
        xvfb.start(&dir, &format!(":{n}"));
        if xvfb.child.is_none() {
            note_xvfb(xvfb, n);
            return;
        }
        // Up once its socket is there and its lock names it; Xvfb removes a stale lock from a display whose server is gone.
        let pid = xvfb.child.as_ref().and_then(|c| c.id());
        let ready = || display_socket(root, n).exists() && ours(root, n, pid);
        let deadline = Instant::now() + wait;
        while !ready() && Instant::now() < deadline && xvfb.running() {
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        // An Xvfb that cannot serve the display exits within moments.
        tokio::time::sleep(Duration::from_millis(500)).await;
        if !xvfb.running() || !ready() {
            if let Some(child) = xvfb.child.as_mut() {
                let _ = child.start_kill(); // still running but not serving: count it as a failed start
                let _ = tokio::time::timeout(Duration::from_secs(5), child.wait()).await;
                xvfb.running();
            }
            note_xvfb(xvfb, n);
            return;
        }
        log(&format!("Tico runner: screen display :{n} is up ({WIDTH}x{HEIGHT})"));
    }
    let Some(n) = *s.display.lock().unwrap() else { return };
    let display = format!(":{n}");
    *s.failure.lock().unwrap() = None;
    s.up.store(true, Ordering::SeqCst);
    let mut started = false;
    for p in rest.iter_mut() {
        if !p.running() {
            p.start(&dir, &display);
            started = true;
        }
    }
    // A new x11vnc serves its socket within moments; until both do, no session opens and readiness says why.
    let deadline = Instant::now() + Duration::from_secs(if started { 5 } else { 0 });
    loop {
        let (control, view) = (rest[1].serving(&dir), rest[2].serving(&dir));
        s.control_up.store(control, Ordering::SeqCst);
        s.view_up.store(view, Ordering::SeqCst);
        if (control && view) || Instant::now() >= deadline {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}

/// An Xvfb failing on a free display: said once it has failed XVFB_GIVE_UP starts in a row.
fn note_xvfb(xvfb: &Proc, n: u32) {
    if xvfb.failures >= XVFB_GIVE_UP {
        note(format!("Xvfb :{n} did not start"));
    }
}

/// Readiness gives this as the reason; the log says it once each time it changes.
fn note(reason: String) {
    let mut failure = shared().failure.lock().unwrap();
    if failure.as_deref() != Some(reason.as_str()) {
        log(&format!("Tico runner: screen: {reason}"));
        *failure = Some(reason);
    }
}

async fn stop_all(procs: &mut [Proc]) {
    let s = shared();
    s.up.store(false, Ordering::SeqCst);
    s.control_up.store(false, Ordering::SeqCst);
    s.view_up.store(false, Ordering::SeqCst);
    for p in procs.iter_mut().rev() {
        p.stop().await;
    }
    *s.display.lock().unwrap() = None;
    if let Some(dir) = s.dir.lock().unwrap().take() {
        let _ = std::fs::remove_dir_all(dir); // the one this runner made: its sockets and the children's HOME
    }
}

/// Stop the display and bridges this runner started, before the process exits.
pub async fn shutdown() {
    if STARTED.load(Ordering::SeqCst) {
        let done = DONE.notified();
        STOP.notify_one();
        let _ = tokio::time::timeout(Duration::from_secs(10), done).await;
    }
}

// ------------------------------------------------------------------------------------------ sessions

/// While the display is up: the hub's pending sessions, every POLL_EVERY and at once on `screen_open`.
async fn sessions(client: Client) {
    let s = shared();
    let mut said: Option<String> = None;
    loop {
        tokio::select! {
            _ = tokio::time::sleep(POLL_EVERY) => {}
            _ = s.wake.notified() => {}
        }
        if !s.up.load(Ordering::SeqCst) || !s.hub.load(Ordering::SeqCst) {
            continue;
        }
        let rows = match client.get("runners/me/screen/sessions").await {
            Ok(v) => {
                said = None;
                v["sessions"].as_array().cloned().unwrap_or_default()
            }
            Err(e) => {
                let text = crate::runner::describe(&e);
                if said.as_ref() != Some(&text) {
                    log(&format!("Tico runner: screen session poll failed ({text}); will retry"));
                    said = Some(text);
                }
                continue;
            }
        };
        for row in rows {
            let id = text_of(&row["session_id"]);
            if id.is_empty() || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
                continue;
            }
            if !s.bridging.lock().unwrap().insert(id.clone()) {
                continue;
            }
            let client = client.clone();
            let endpoint = endpoint_for(&row);
            tokio::spawn(async move {
                if let Err(e) = bridge(&client, &id, endpoint).await {
                    log(&format!("Tico runner: screen session {id} ended: {e}"));
                }
                shared().bridging.lock().unwrap().remove(&id);
            });
        }
    }
}

/// The hub's websocket for one session: `wss://<hub>/api/v2/runners/me/screen/<id>/ws`.
pub fn ws_url(hub: &str, session_id: &str) -> Result<String, String> {
    let rest = if let Some(r) = hub.strip_prefix("https://") {
        format!("wss://{r}")
    } else if let Some(r) = hub.strip_prefix("http://") {
        format!("ws://{r}")
    } else {
        return Err(format!("not an http(s) hub URL: {hub}"));
    };
    Ok(format!("{}/api/v2/runners/me/screen/{session_id}/ws", rest.trim_end_matches('/')))
}

/// Pipe one hub session to the local VNC port until either side closes, the screen goes off or the cap passes.
async fn bridge(client: &Client, session_id: &str, endpoint: Endpoint) -> Result<(), String> {
    use tokio_tungstenite::tungstenite::{client::IntoClientRequest, Message};
    // Only to the x11vnc this runner started, while it runs: fail closed otherwise.
    let s = shared();
    let up = match endpoint {
        Endpoint::Control => &s.control_up,
        Endpoint::View => &s.view_up,
    };
    let dir = s.dir.lock().unwrap().clone();
    let Some(dir) = dir.filter(|_| up.load(Ordering::SeqCst)) else { return Err(format!("{endpoint:?} VNC is not running")) };
    let vnc = tokio::net::UnixStream::connect(dir.join(endpoint.socket())).await.map_err(|e| format!("{endpoint:?} VNC: {e}"))?;
    let token = bearer(&client.token).map_err(|e| e.detail)?;
    let mut request = ws_url(&client.url, session_id)?.into_client_request().map_err(|e| e.to_string())?;
    let headers = request.headers_mut();
    headers.insert("Authorization", format!("Bearer {token}").parse().map_err(|_| "bad token".to_string())?);
    headers.insert("User-Agent", format!("Tico-Client/{CLIENT_VERSION}").parse().unwrap());
    let (ws, _) = tokio::time::timeout(Duration::from_secs(15), tokio_tungstenite::connect_async_tls_with_config(request, None, false, Some(crate::tls::websocket_connector())))
        .await
        .map_err(|_| "the hub did not answer".to_string())?
        .map_err(|e| format!("websocket: {e}"))?;
    let (mut to_hub, mut from_hub) = ws.split();
    let (mut vnc_in, mut vnc_out) = vnc.into_split();
    let mut on = shared().switch.subscribe();
    let cap = tokio::time::sleep(SESSION_CAP);
    tokio::pin!(cap);
    let mut buf = vec![0u8; 64 * 1024];
    let result = loop {
        tokio::select! {
            read = vnc_in.read(&mut buf) => match read {
                Ok(0) => break Ok(()),
                Ok(n) => {
                    if let Err(e) = to_hub.send(Message::Binary(buf[..n].to_vec().into())).await {
                        break Err(format!("websocket: {e}"));
                    }
                }
                Err(e) => break Err(format!("VNC: {e}")),
            },
            frame = from_hub.next() => match frame {
                Some(Ok(Message::Binary(data))) => {
                    if let Err(e) = vnc_out.write_all(&data).await {
                        break Err(format!("VNC: {e}"));
                    }
                }
                Some(Ok(Message::Close(_))) | None => break Ok(()),
                Some(Ok(_)) => {} // pings are answered by tungstenite; text is not part of the protocol
                Some(Err(e)) => break Err(format!("websocket: {e}")),
            },
            changed = on.changed() => {
                if changed.is_err() || !*on.borrow() {
                    break Ok(());
                }
            }
            _ = &mut cap => break Ok(()),
        }
    };
    let _ = to_hub.send(Message::Close(None)).await;
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_lock_is_off_unless_turned_on_and_the_environment_wins() {
        assert_eq!(lock_from(None, None, false), (false, "runner config"));
        assert_eq!(lock_from(Some(&json!(true)), None, false), (true, "runner config"));
        assert!(!lock_from(Some(&json!("off")), None, false).0);
        assert_eq!(lock_from(Some(&json!(true)), Some("0"), false), (false, "TICO_SCREEN"));
        assert_eq!(lock_from(None, Some("1"), false), (true, "TICO_SCREEN"));
        assert_eq!(lock_from(Some(&json!(true)), Some(" "), false), (true, "runner config"));
    }

    #[test]
    fn a_cloud_machine_has_its_screen_on_unless_its_owner_chose_otherwise() {
        // No choice made: on, with its own source.
        assert_eq!(lock_from(None, None, true), (true, "cloud machine"));
        // An explicit choice in runner.json (`screen off` included) stands.
        assert_eq!(lock_from(Some(&json!(false)), None, true), (false, "runner config"));
        // TICO_SCREEN wins over the cloud default, both ways.
        assert_eq!(lock_from(None, Some("0"), true), (false, "TICO_SCREEN"));
        assert_eq!(lock_from(None, Some("1"), true), (true, "TICO_SCREEN"));
        // Anywhere else the screen stays off without a choice.
        assert_eq!(lock_from(None, None, false), (false, "runner config"));
    }

    #[test]
    fn the_capability_is_offered_only_when_on_and_usable() {
        assert_eq!(report(false, "linux", &[], None, true), None);
        let up = report(true, "linux", &[], None, true);
        assert!(offers(up.as_ref()));
        assert_eq!(up.unwrap(), json!({"available": true, "width": 1440, "height": 900}));
        let mac = report(true, "macos", &[], None, true);
        assert!(!offers(mac.as_ref()));
        assert_eq!(mac.unwrap()["reason"], "Linux only for now");
        assert!(!offers(report(true, "linux", &["x11vnc"], None, true).as_ref()));
        assert!(!offers(report(true, "linux", &[], Some("Xvfb :99 did not start"), true).as_ref()));
        // Either x11vnc down (or one not ours on its endpoint): no capability, and readiness says so.
        assert_eq!(report(true, "linux", &[], None, false).unwrap()["reason"], "VNC is not running");
    }

    #[test]
    fn only_an_explicit_control_session_reaches_the_input_endpoint() {
        assert_eq!(endpoint_for(&json!({"session_id": "a", "control": true})), Endpoint::Control);
        assert_eq!(endpoint_for(&json!({"session_id": "a", "control": false})), Endpoint::View);
        assert_eq!(endpoint_for(&json!({"session_id": "a", "control": "true"})), Endpoint::View);
        assert_eq!(endpoint_for(&json!({"session_id": "a"})), Endpoint::View);
        assert_eq!(ws_url("https://hub.acme.example", "s1").unwrap(), "wss://hub.acme.example/api/v2/runners/me/screen/s1/ws");
        assert_eq!(ws_url("http://127.0.0.1:8000/", "s1").unwrap(), "ws://127.0.0.1:8000/api/v2/runners/me/screen/s1/ws");
    }

    /// A fresh directory standing in for /tmp, with the `.X11-unix` an X server makes.
    fn x_root() -> PathBuf {
        let root = std::env::temp_dir().join(format!("tico-xroot-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join(".X11-unix")).unwrap();
        root
    }

    fn write_lock(root: &Path, n: u32, pid: u32) {
        std::fs::write(root.join(format!(".X{n}-lock")), format!("{pid:>10}\n")).unwrap();
    }

    /// Another X server on display n: a lock naming a live process (this test) and a socket it serves.
    #[cfg(target_os = "linux")]
    fn hold(root: &Path, n: u32) -> std::os::unix::net::UnixListener {
        write_lock(root, n, std::process::id());
        std::os::unix::net::UnixListener::bind(display_socket(root, n)).unwrap()
    }

    #[cfg(target_os = "linux")]
    fn dead_pid() -> u32 {
        let mut child = std::process::Command::new("true").spawn().unwrap();
        let pid = child.id();
        child.wait().unwrap();
        pid
    }

    #[cfg(target_os = "linux")]
    fn own_name() -> String {
        std::fs::read_to_string("/proc/self/comm").map(|c| c.trim().to_string()).unwrap_or_default()
    }

    #[test]
    fn the_display_override_is_a_colon_and_a_number() {
        assert_eq!(display_override(None), Ok(None));
        assert_eq!(display_override(Some("")), Ok(None));
        assert_eq!(display_override(Some("  ")), Ok(None));
        assert_eq!(display_override(Some(":101")), Ok(Some(101)));
        assert_eq!(display_override(Some(" :0 ")), Ok(Some(0)));
        for junk in ["junk", "101", ":", ":1a", ":-1", ": 1", ":101.0", "localhost:101", ":123456"] {
            let err = display_override(Some(junk)).unwrap_err();
            assert!(err.starts_with("TICO_SCREEN_DISPLAY is not a display"), "{junk}: {err}");
        }
        // Readiness says so in place of starting anything.
        let err = choose(&x_root(), Some("junk")).unwrap_err();
        assert_eq!(report(true, "linux", &[], Some(&err), true).unwrap()["reason"], err);
    }

    #[test]
    #[cfg(target_os = "linux")] // /proc
    fn a_display_another_x_server_holds_is_passed_over() {
        let root = x_root();
        assert_eq!(choose(&root, None), Ok(FIRST_DISPLAY));
        let _first = hold(&root, FIRST_DISPLAY);
        assert_eq!(choose(&root, None), Ok(FIRST_DISPLAY + 1));
        // A live lock alone holds a display; so does a served socket with no lock (or a lock it can't name).
        write_lock(&root, FIRST_DISPLAY + 1, std::process::id());
        let _second = std::os::unix::net::UnixListener::bind(display_socket(&root, FIRST_DISPLAY + 2)).unwrap();
        assert_eq!(choose(&root, None), Ok(FIRST_DISPLAY + 3));
        assert_eq!(holder(&root, FIRST_DISPLAY + 2), Some(Holder { pid: None, name: None }));
    }

    #[test]
    #[cfg(target_os = "linux")] // /proc
    fn a_stale_lock_and_socket_leave_the_display_free() {
        let root = x_root();
        write_lock(&root, FIRST_DISPLAY, dead_pid());
        drop(std::os::unix::net::UnixListener::bind(display_socket(&root, FIRST_DISPLAY)).unwrap());
        assert!(display_socket(&root, FIRST_DISPLAY).exists(), "the socket file stays behind");
        // A process another test forks at that moment holds the socket until it execs.
        let deadline = Instant::now() + Duration::from_secs(5);
        while listening(&root, FIRST_DISPLAY) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(holder(&root, FIRST_DISPLAY), None);
        assert_eq!(choose(&root, None), Ok(FIRST_DISPLAY));
        // A lock that names no pid (or pid 0, which would signal our own process group) is no one's.
        std::fs::write(root.join(format!(".X{FIRST_DISPLAY}-lock")), "         0\n").unwrap();
        assert_eq!(choose(&root, None), Ok(FIRST_DISPLAY));
    }

    #[test]
    #[cfg(target_os = "linux")] // /proc
    fn the_override_is_used_exactly_and_says_who_holds_it() {
        let root = x_root();
        assert_eq!(choose(&root, Some(":101")), Ok(101));
        let _held = hold(&root, FIRST_DISPLAY);
        let reason = choose(&root, Some(":99")).unwrap_err();
        let me = std::process::id();
        assert_eq!(
            reason,
            format!("Display :99 is in use by another X server ({}, pid {me}). Stop it or set TICO_SCREEN_DISPLAY to a free display.", own_name())
        );
        assert!(!reason.contains("did not start"));
        assert!(!offers(report(true, "linux", &[], Some(&reason), true).as_ref()));
        // The override never searches: a held :101 is reported even with :102 free.
        let _other = hold(&root, 101);
        assert!(choose(&root, Some(":101")).unwrap_err().starts_with("Display :101 is in use by another X server ("));
    }

    #[test]
    fn the_reason_names_the_holder_only_when_it_can() {
        let text = |pid, name: Option<&str>| in_use(99, &Holder { pid, name: name.map(String::from) });
        assert_eq!(text(Some(1234), Some("Xvfb")), "Display :99 is in use by another X server (Xvfb, pid 1234). Stop it or set TICO_SCREEN_DISPLAY to a free display.");
        assert_eq!(text(Some(1234), None), "Display :99 is in use by another X server (pid 1234). Stop it or set TICO_SCREEN_DISPLAY to a free display.");
        assert_eq!(text(None, None), "Display :99 is in use by another X server. Stop it or set TICO_SCREEN_DISPLAY to a free display.");
        // The hub keeps 200 characters of a reason (ScreenReadiness); a process name is at most 15.
        let longest = in_use(99999, &Holder { pid: Some(4_194_304), name: Some("x".repeat(15)) });
        assert!(longest.len() <= 200, "{}", longest.len());
    }

    #[test]
    fn every_display_in_use_says_so() {
        let root = x_root();
        for n in FIRST_DISPLAY..=LAST_DISPLAY {
            write_lock(&root, n, std::process::id());
        }
        assert_eq!(
            choose(&root, None),
            Err("Displays :99 to :120 are all in use by other X servers. Stop one or set TICO_SCREEN_DISPLAY to a free display.".into())
        );
        assert_eq!(choose(&root, Some(":121")), Ok(121));
    }

    #[test]
    fn a_display_is_ours_only_when_its_lock_names_our_xvfb() {
        let root = x_root();
        assert!(!ours(&root, 99, Some(4321)), "no lock yet");
        write_lock(&root, 99, 4321);
        assert!(ours(&root, 99, Some(4321)));
        assert!(!ours(&root, 99, Some(4322)), "another X server's lock and socket are never taken for ours");
        assert!(!ours(&root, 99, None));
    }

    /// Each program draws on display n: its DISPLAY, and for Xvfb and x11vnc their arguments.
    #[cfg(target_os = "linux")]
    fn all_on(procs: &[Proc], n: u32) {
        for p in procs {
            let pid = p.child.as_ref().and_then(|c| c.id()).unwrap_or_else(|| panic!("{} is not running", p.name));
            let read = |f: &str| std::fs::read(format!("/proc/{pid}/{f}")).map(|b| String::from_utf8_lossy(&b).replace('\0', " ")).unwrap();
            assert!(read("environ").contains(&format!("DISPLAY=:{n} ")), "{} environ", p.name);
            if p.name != procs[1].name {
                assert!(read("cmdline").contains(&format!(" :{n} ")), "{}: {}", p.name, read("cmdline"));
            }
        }
    }

    /// The other X server, stopped however the test ends.
    #[cfg(target_os = "linux")]
    struct Other(std::process::Child);

    #[cfg(target_os = "linux")]
    impl Drop for Other {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    /// An "Xvfb" that stays alive without serving a display of its own: it records its pid, then (when `foreign`)
    /// writes the display's lock naming another live process (this test) and puts a socket file in place.
    #[cfg(target_os = "linux")]
    fn stalled_xvfb(root: &Path, foreign: bool) -> Proc {
        let r = root.display();
        let other = if foreign { format!("printf '%10d\n' {} > {r}/.X$n-lock; : > {r}/.X11-unix/X$n; ", std::process::id()) } else { String::new() };
        let script = format!("n=${{1#:}}; echo $$ > {r}/xvfb.pid; {other}exec sleep 30");
        Proc::new("sh", &["-c", &script, "sh", AT_DISPLAY])
    }

    /// The supervisor's own checks after a start: a display is up only when its lock names our Xvfb, and an Xvfb that
    /// stays alive without serving is stopped and counted as a failed start, also on the next tick.
    #[tokio::test]
    #[cfg(target_os = "linux")]
    async fn an_xvfb_that_does_not_serve_its_own_display_is_never_taken_as_up() {
        let s = shared();
        s.hub.store(true, Ordering::SeqCst);
        s.on.store(true, Ordering::SeqCst);
        for foreign in [false, true] {
            let root = x_root();
            let mut procs = children();
            procs[0] = stalled_xvfb(&root, foreign);
            tick_in(&mut procs, &root, Duration::from_millis(300)).await;
            let pid: i32 = std::fs::read_to_string(root.join("xvfb.pid")).unwrap().trim().parse().unwrap();
            assert!(procs[0].child.is_none(), "foreign={foreign}: the stalled Xvfb is still ours");
            assert!(!alive(pid), "foreign={foreign}: the stalled Xvfb (pid {pid}) was not stopped");
            assert_eq!(procs[0].failures, 1, "foreign={foreign}");
            // The next tick neither takes it as up nor starts anything on it.
            tick_in(&mut procs, &root, Duration::from_millis(300)).await;
            assert_eq!(display(), None, "foreign={foreign}");
            assert!(!s.up.load(Ordering::SeqCst), "foreign={foreign}");
            assert!(!offers(readiness().as_ref()), "foreign={foreign}: {:?}", readiness());
            assert!(procs[1..].iter().all(|p| p.child.is_none()), "foreign={foreign}: a window manager or x11vnc started");
            assert_eq!(procs[0].failures, 1, "foreign={foreign}");
            if foreign {
                // :99 is now the other server's, so the next start moves on to :100.
                assert!(holder(&root, FIRST_DISPLAY).is_some_and(|h| h.pid == Some(std::process::id() as i32)));
                assert_eq!(*s.display.lock().unwrap(), Some(FIRST_DISPLAY + 1));
            }
            stop_all(&mut procs).await;
            *s.failure.lock().unwrap() = None;
        }
    }

    /// The real programs on this computer's /tmp: another Xvfb holds the first free display, and the screen comes up
    /// on the next one; then TICO_SCREEN_DISPLAY picks one exactly. Needs Xvfb, x11vnc and a window manager:
    /// `cargo test screen::tests::live -- --ignored`.
    #[tokio::test]
    #[ignore]
    #[cfg(target_os = "linux")]
    async fn live_the_screen_takes_the_next_free_display_or_the_override() {
        let root = Path::new(X_ROOT);
        let taken = choose(root, None).unwrap();
        let mut other = Other(std::process::Command::new("Xvfb").args([format!(":{taken}").as_str(), "-nolisten", "tcp"]).spawn().unwrap());
        let deadline = Instant::now() + Duration::from_secs(10);
        while !(display_socket(root, taken).exists() && holder(root, taken).is_some()) {
            assert!(other.0.try_wait().unwrap().is_none(), "the other Xvfb on :{taken} exited");
            assert!(Instant::now() < deadline, "the other Xvfb never served :{taken}");
            std::thread::sleep(Duration::from_millis(50));
        }
        let s = shared();
        s.hub.store(true, Ordering::SeqCst);
        s.on.store(true, Ordering::SeqCst);
        let mut procs = children();
        tick(&mut procs).await;
        let next = s.display.lock().unwrap().unwrap();
        assert!(next > taken, "{next} after {taken}");
        assert_eq!(readiness().unwrap()["available"], true, "{:?}", readiness());
        assert_eq!(display(), Some(format!(":{next}")));
        all_on(&procs, next);
        stop_all(&mut procs).await;
        assert_eq!(display(), None);

        // TICO_SCREEN_DISPLAY on the held display: the reason names it, and nothing starts.
        std::env::set_var("TICO_SCREEN_DISPLAY", format!(":{taken}"));
        let mut procs = children();
        tick(&mut procs).await;
        let reason = text_of(&readiness().unwrap()["reason"]);
        assert!(reason.starts_with(&format!("Display :{taken} is in use by another X server (Xvfb, pid {})", other.0.id())), "{reason}");
        assert!(procs[0].child.is_none());
        drop(other);

        // And on a free one: Xvfb, the window manager, both x11vnc and bot turns use exactly it.
        let free = (taken + 2..LAST_DISPLAY).find(|n| holder(root, *n).is_none()).unwrap();
        std::env::set_var("TICO_SCREEN_DISPLAY", format!(":{free}"));
        tick(&mut procs).await;
        std::env::remove_var("TICO_SCREEN_DISPLAY");
        assert_eq!(readiness().unwrap()["available"], true, "{:?}", readiness());
        assert_eq!(display(), Some(format!(":{free}")));
        all_on(&procs, free);
        stop_all(&mut procs).await;
    }
}
