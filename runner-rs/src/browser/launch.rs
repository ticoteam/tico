//! Finding and starting Chrome: the copy the system installs and updates (`/Applications/Google Chrome.app` on a
//! Mac; on a Linux PATH `google-chrome`, else Debian's `chromium`, which the runner image installs because Google
//! publishes no Linux arm64 Chrome), never one the runner downloads. Each account has its own profile, with CDP only
//! over the pipe (`--remote-debugging-pipe`), and the computer's own language, time zone and user agent. Linux uses
//! headless mode when no display is available; other sessions have a window.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use serde_json::{json, Value};

use super::cdp;
use crate::util::text_of;

pub const MAC_CHROME: &str = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const LINUX_NAMES: &[&str] = &["google-chrome", "google-chrome-stable", "chromium"];
pub const WIDTH: u32 = 1440;
pub const HEIGHT: u32 = 900;

/// Chrome on this computer: Google Chrome's app on a Mac; on Linux the first of `LINUX_NAMES` on PATH, and no
/// display is required (it uses headless mode when there is none).
pub fn chrome() -> Option<PathBuf> {
    match std::env::consts::OS {
        "macos" => Some(PathBuf::from(MAC_CHROME)).filter(|p| p.is_file()),
        "linux" => {
            let path = std::env::var("PATH").unwrap_or_default();
            LINUX_NAMES.iter().find_map(|n| crate::env::which(n, &path))
        }
        _ => None,
    }
}

/// The X display a Linux Chrome draws on.
pub fn display() -> Option<String> {
    std::env::var("DISPLAY")
        .ok()
        .filter(|d| !d.is_empty())
        .or_else(crate::screen::display)
}

/// Chrome's version, read once per path (a Mac reads the app's Info.plist; Linux asks the binary).
pub fn version(exe: &Path) -> Option<String> {
    static SEEN: OnceLock<Mutex<Option<(PathBuf, Option<String>)>>> = OnceLock::new();
    let seen = SEEN.get_or_init(|| Mutex::new(None));
    if let Some((p, v)) = seen.lock().unwrap().as_ref() {
        if p == exe {
            return v.clone();
        }
    }
    let found = if std::env::consts::OS == "macos" {
        let plist = exe
            .ancestors()
            .nth(2)
            .map(|contents| contents.join("Info.plist"))?;
        plist_version(&std::fs::read_to_string(plist).ok()?)
    } else {
        let out = std::process::Command::new(exe)
            .arg("--version")
            .stdin(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .output()
            .ok()?;
        String::from_utf8_lossy(&out.stdout)
            .split_whitespace()
            .find(|w| w.chars().next().is_some_and(|c| c.is_ascii_digit()))
            .map(String::from)
    };
    *seen.lock().unwrap() = Some((exe.to_path_buf(), found.clone()));
    found
}

fn plist_version(text: &str) -> Option<String> {
    let at = text.find("<key>CFBundleShortVersionString</key>")?;
    let rest = &text[at..];
    let start = rest.find("<string>")? + "<string>".len();
    let end = rest[start..].find("</string>")?;
    Some(rest[start..start + end].trim().to_string()).filter(|v| !v.is_empty())
}

/// The Chrome command line after the executable. `proxy` is the turn's Browse-through proxy for a session on the
/// bot's own computer; the browser host never has one.
pub fn flags(exe: &Path, profile: &Path, proxy: Option<&str>) -> Vec<String> {
    let headless = std::env::consts::OS == "linux" && display().is_none();
    let version = if headless { version(exe) } else { None };
    flags_for(
        profile,
        proxy,
        std::env::consts::OS,
        !headless,
        version.as_deref(),
    )
}

/// The user agent a windowed Chrome `version` sends on Linux. Headless mode sends "HeadlessChrome/..." instead,
/// which sign-in pages take for a robot; its client hints already name Chromium alone.
fn linux_user_agent(version: &str) -> Option<String> {
    let major = version
        .split('.')
        .next()
        .filter(|m| !m.is_empty() && m.len() <= 4 && m.bytes().all(|b| b.is_ascii_digit()))?;
    Some(format!(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    ))
}

fn flags_for(
    profile: &Path,
    proxy: Option<&str>,
    platform: &str,
    has_display: bool,
    version: Option<&str>,
) -> Vec<String> {
    let mut f = vec![
        format!("--user-data-dir={}", profile.display()),
        "--remote-debugging-pipe".into(),
        "--no-first-run".into(),
        "--no-default-browser-check".into(),
        "--disable-search-engine-choice-screen".into(),
        // Pipe mode marks the page as automated (`navigator.webdriver`); sites refuse such sign-ins.
        "--disable-blink-features=AutomationControlled".into(),
        format!("--window-size={WIDTH},{HEIGHT}"),
        // Several account windows sit on one screen, most of them covered: a covered window still paints, so a
        // screenshot of it is taken at once and its page timers run at normal speed.
        "--disable-backgrounding-occluded-windows".into(),
        "--disable-renderer-backgrounding".into(),
    ];
    if platform == "linux" {
        if !has_display {
            f.push("--headless=new".into());
            if let Some(ua) = version.and_then(linux_user_agent) {
                f.push(format!("--user-agent={ua}"));
            }
        }
        f.push("--password-store=basic".into());
        f.push("--disable-dev-shm-usage".into());
    }
    if let Some(p) = proxy.filter(|p| plain(p)) {
        f.push(format!("--proxy-server={p}"));
        f.push("--force-webrtc-ip-handling-policy=disable_non_proxied_udp".into());
    }
    f
}

fn plain(v: &str) -> bool {
    !v.is_empty() && v.len() < 500 && !v.chars().any(|c| c.is_whitespace() || c.is_control())
}

/// Before a start: a profile that was not closed cleanly says so in its Preferences, and Chrome would then offer to
/// restore pages. Only the clean-exit keys are touched: Chrome keeps persistent cookies and expires session-only
/// cookies on a clean restart. A file that is not JSON is left for Chrome to deal with.
pub fn fix_prefs(profile: &Path) -> std::io::Result<()> {
    let dir = profile.join("Default");
    let file = dir.join("Preferences");
    match std::fs::symlink_metadata(&file) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            std::fs::create_dir_all(&dir)?;
            std::fs::write(
                &file,
                json!({"profile": {"exit_type": "Normal", "exited_cleanly": true}}).to_string(),
            )
        }
        Err(e) => Err(e),
        Ok(m) if !m.is_file() => Ok(()),
        Ok(_) => {
            let text = std::fs::read_to_string(&file)?;
            let Some(fixed) = fixed_prefs(&text) else {
                return Ok(());
            };
            let tmp = dir.join(format!(
                ".Preferences.tico-{}",
                uuid::Uuid::new_v4().simple()
            ));
            std::fs::write(&tmp, fixed)?;
            std::fs::rename(&tmp, &file).inspect_err(|_| {
                let _ = std::fs::remove_file(&tmp);
            })
        }
    }
}

/// The Preferences text with the exit marked clean and a missing startup choice set, or None when nothing changes.
pub fn fixed_prefs(text: &str) -> Option<String> {
    let mut prefs: Value = serde_json::from_str(text).ok()?;
    let root = prefs.as_object_mut()?;
    let mut changed = false;
    let profile = root.entry("profile").or_insert_with(|| json!({}));
    if let Some(p) = profile.as_object_mut() {
        if p.get("exit_type").is_some_and(|t| t != "Normal") {
            p.insert("exit_type".into(), json!("Normal"));
            changed = true;
        }
        if p.get("exited_cleanly") == Some(&json!(false)) {
            p.insert("exited_cleanly".into(), json!(true));
            changed = true;
        }
    }
    changed.then(|| prefs.to_string())
}

/// The runner that started this profile's Chrome died without closing it: a Chrome still holding the profile is
/// ended (only one whose command line names this profile), and a lock left by one that is gone is cleared. A second
/// Chrome on a held profile hands itself to the first and exits, and the pipe would go nowhere.
pub fn clear_orphan(profile: &Path) {
    let lock = profile.join("SingletonLock");
    let Ok(target) = std::fs::read_link(&lock) else {
        return;
    };
    let target = target.to_string_lossy().to_string();
    let pid = target
        .rsplit_once('-')
        .and_then(|(_, p)| p.parse::<i32>().ok())
        .filter(|p| *p > 1);
    if let Some(pid) = pid {
        if holds_profile(pid, profile) {
            // SAFETY: kill with a checked pid and a signal number has no memory effects.
            unsafe { libc::kill(pid, libc::SIGTERM) };
            for _ in 0..50 {
                std::thread::sleep(Duration::from_millis(100));
                // SAFETY: signal 0 only checks the process exists.
                if unsafe { libc::kill(pid, 0) } != 0 {
                    break;
                }
            }
            if holds_profile(pid, profile) {
                // SAFETY: as above.
                unsafe { libc::kill(pid, libc::SIGKILL) };
                std::thread::sleep(Duration::from_millis(300));
            }
        }
    }
    for name in ["SingletonLock", "SingletonSocket", "SingletonCookie"] {
        let _ = std::fs::remove_file(profile.join(name));
    }
}

/// Whether process `pid` is a Chrome started on `profile`.
fn holds_profile(pid: i32, profile: &Path) -> bool {
    let out = std::process::Command::new("ps")
        .args(["-ww", "-o", "command=", "-p", &pid.to_string()])
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output();
    let Ok(out) = out else { return false };
    let command = String::from_utf8_lossy(&out.stdout);
    command.contains(&format!("--user-data-dir={}", profile.display()))
}

#[cfg(target_os = "linux")]
fn pipe() -> std::io::Result<(std::os::fd::OwnedFd, std::os::fd::OwnedFd)> {
    use std::os::fd::FromRawFd;
    let mut fds = [0i32; 2];
    // SAFETY: pipe2 writes two descriptors into the array.
    if unsafe { libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    // SAFETY: both descriptors are new and owned here.
    Ok(unsafe {
        (
            std::os::fd::OwnedFd::from_raw_fd(fds[0]),
            std::os::fd::OwnedFd::from_raw_fd(fds[1]),
        )
    })
}

/// macOS has no pipe2: a plain pipe, then close-on-exec on both ends. A child forked in between could inherit them;
/// the runner forks only through std/tokio, which mark their own descriptors, and these two are moved above 10 and
/// closed before Chrome starts.
#[cfg(not(target_os = "linux"))]
fn pipe() -> std::io::Result<(std::os::fd::OwnedFd, std::os::fd::OwnedFd)> {
    use std::os::fd::FromRawFd;
    let mut fds = [0i32; 2];
    // SAFETY: pipe writes two descriptors into the array.
    if unsafe { libc::pipe(fds.as_mut_ptr()) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    // SAFETY: both descriptors are new and owned here.
    let ends = unsafe {
        (
            std::os::fd::OwnedFd::from_raw_fd(fds[0]),
            std::os::fd::OwnedFd::from_raw_fd(fds[1]),
        )
    };
    for fd in fds {
        // SAFETY: fcntl on a descriptor owned here.
        if unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) } != 0 {
            return Err(std::io::Error::last_os_error());
        }
    }
    Ok(ends)
}

/// A copy of `fd` at 10 or above (close-on-exec), so placing the child's ends at 3 and 4 never overwrites either.
fn high(fd: &std::os::fd::OwnedFd) -> std::io::Result<std::os::fd::OwnedFd> {
    use std::os::fd::{AsRawFd, FromRawFd};
    // SAFETY: F_DUPFD_CLOEXEC returns a new descriptor or -1.
    let n = unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_DUPFD_CLOEXEC, 10) };
    if n < 0 {
        return Err(std::io::Error::last_os_error());
    }
    // SAFETY: n is a new descriptor owned here.
    Ok(unsafe { std::os::fd::OwnedFd::from_raw_fd(n) })
}

/// Make `dir` (and its parents) owner-only.
pub fn private_dir(dir: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
    std::fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(dir)?;
    std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))
}

/// What Chrome needs from the runner's environment: the user's home (the Keychain on a Mac), the display, the
/// locale and zone as they are. Nothing else crosses, so no runner token or turn variable reaches Chrome.
const KEPT_ENV: &[&str] = &[
    "HOME",
    "USER",
    "LOGNAME",
    "PATH",
    "TMPDIR",
    "SHELL",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "TZ",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
];

pub struct Launched {
    pub child: tokio::process::Child,
    pub browser: Arc<cdp::Browser>,
    pub version: String,
    pub stderr: Arc<Mutex<std::collections::VecDeque<String>>>,
}

impl Launched {
    pub fn tail(&self) -> String {
        tail(&self.stderr)
    }
}

fn tail(lines: &Mutex<std::collections::VecDeque<String>>) -> String {
    lines
        .lock()
        .unwrap()
        .iter()
        .rev()
        .take(3)
        .rev()
        .cloned()
        .collect::<Vec<_>>()
        .join(" | ")
}

/// What Chromium prints, and then exits, when it finds no sandbox it can use.
const NO_SANDBOX_SAID: &str = "No usable sandbox";

/// The executables that refused to start sandboxed on this computer (see `launch`).
fn unsandboxed() -> &'static Mutex<std::collections::HashSet<PathBuf>> {
    static SEEN: OnceLock<Mutex<std::collections::HashSet<PathBuf>>> = OnceLock::new();
    SEEN.get_or_init(Default::default)
}

/// Start Chrome on `profile` and wait until it answers over the pipe.
///
/// Chrome's sandbox stays on wherever it can start. On Linux it needs a user namespace (or Google Chrome's setuid
/// helper), and the runner container has neither: Docker's default seccomp profile refuses `unshare`/`clone` of a
/// user namespace to a process without CAP_SYS_ADMIN, and `no-new-privileges` (docker/runner.compose.yaml) makes
/// a setuid helper useless. Chromium then prints "No usable sandbox!" and exits, so only after it said so, the same
/// executable starts with `--no-sandbox` from then on. Granting the container CAP_SYS_ADMIN or a custom seccomp
/// profile would loosen it for bot code too. What still holds there: Chrome runs as the runner's user with no
/// capabilities (the ambient ones the supervisor holds are cleared before exec) and `no_new_privs`; bot code runs
/// as another user, who cannot enter the profiles (`browsers/` in the runner's state directory and each profile in
/// it are 0700, `private_dir`); and a bot opens only the website tool's own sites, and a login value is typed only
/// there.
pub async fn launch(exe: &Path, profile: &Path, proxy: Option<&str>) -> Result<Launched, String> {
    private_dir(profile).map_err(|e| format!("cannot make the profile directory ({e})"))?;
    // It may wait seconds for an orphan to end: off the async threads.
    let held = profile.to_path_buf();
    let _ = tokio::task::spawn_blocking(move || clear_orphan(&held)).await;
    fix_prefs(profile).map_err(|e| format!("cannot prepare the profile ({e})"))?;
    let known = unsandboxed().lock().unwrap().contains(exe);
    match start(exe, profile, proxy, known).await {
        Err(Refused::NoSandbox(_)) if !known => {
            crate::util::log(&format!(
                "browser: {} has no usable sandbox here; starting it with --no-sandbox",
                exe.display()
            ));
            unsandboxed().lock().unwrap().insert(exe.to_path_buf());
            start(exe, profile, proxy, true)
                .await
                .map_err(Refused::text)
        }
        r => r.map_err(Refused::text),
    }
}

enum Refused {
    NoSandbox(String),
    Other(String),
}

impl Refused {
    fn text(self) -> String {
        match self {
            Refused::NoSandbox(t) | Refused::Other(t) => t,
        }
    }
}

async fn start(
    exe: &Path,
    profile: &Path,
    proxy: Option<&str>,
    no_sandbox: bool,
) -> Result<Launched, Refused> {
    use std::os::fd::AsRawFd;
    let other = |e: std::io::Error| Refused::Other(e.to_string());
    let (chrome_reads, we_write) = pipe().map_err(other)?;
    let (we_read, chrome_writes) = pipe().map_err(other)?;
    let (child_in, child_out) = (
        high(&chrome_reads).map_err(other)?,
        high(&chrome_writes).map_err(other)?,
    );
    drop((chrome_reads, chrome_writes));
    let (fd_in, fd_out) = (child_in.as_raw_fd(), child_out.as_raw_fd());

    let mut cmd = tokio::process::Command::new(exe);
    // No page named: Chrome reopens the last one (the session the profile kept), or a new tab.
    cmd.args(flags(exe, profile, proxy));
    if no_sandbox {
        cmd.arg("--no-sandbox");
    }
    cmd.env_clear();
    for k in KEPT_ENV {
        if let Ok(v) = std::env::var(k) {
            cmd.env(k, v);
        }
    }
    if std::env::consts::OS == "linux" {
        if let Some(d) = display() {
            cmd.env("DISPLAY", d);
        }
    }
    cmd.stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true);
    // SAFETY: only async-signal-safe calls (umask, dup2, prctl) between fork and exec.
    unsafe {
        cmd.pre_exec(move || {
            libc::umask(0o077);
            if libc::dup2(fd_in, 3) < 0 || libc::dup2(fd_out, 4) < 0 {
                return Err(std::io::Error::last_os_error());
            }
            #[cfg(target_os = "linux")]
            {
                // A runner that dies takes its browsers with it; on a Mac the next start ends an orphan instead
                // (clear_orphan).
                libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
                // The container's supervisor holds ambient capabilities (DAC_OVERRIDE, SETUID, ...) to drop bot
                // code to another user; Chrome gets none of them. A kernel without ambient capabilities has none
                // to clear.
                libc::prctl(
                    libc::PR_CAP_AMBIENT,
                    libc::PR_CAP_AMBIENT_CLEAR_ALL as libc::c_ulong,
                    0,
                    0,
                    0,
                );
            }
            Ok(())
        });
    }
    let mut child = cmd
        .spawn()
        .map_err(|e| Refused::Other(format!("cannot start Chrome ({e})")))?;
    drop((child_in, child_out));
    let stderr = Arc::new(Mutex::new(std::collections::VecDeque::new()));
    let sandbox_said = Arc::new(AtomicBool::new(false));
    let mut reader = None;
    if let Some(err) = child.stderr.take() {
        let (keep, said) = (stderr.clone(), sandbox_said.clone());
        reader = Some(tokio::spawn(async move {
            use tokio::io::AsyncBufReadExt;
            let mut lines = tokio::io::BufReader::new(err).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if line.contains(NO_SANDBOX_SAID) {
                    said.store(true, Ordering::SeqCst);
                }
                let mut k = keep.lock().unwrap();
                if k.len() == 20 {
                    k.pop_front();
                }
                k.push_back(line.chars().take(300).collect::<String>());
            }
        }));
    }
    let to_chrome = tokio::net::unix::pipe::Sender::from_owned_fd(we_write).map_err(other)?;
    let from_chrome = tokio::net::unix::pipe::Receiver::from_owned_fd(we_read).map_err(other)?;
    let browser = cdp::Browser::start(to_chrome, from_chrome);
    let version = match browser
        .call_within(
            "Browser.getVersion",
            json!({}),
            None,
            Duration::from_secs(30),
        )
        .await
    {
        Ok(v) => text_of(&v["product"])
            .trim_start_matches("HeadlessChrome/")
            .trim_start_matches("Chrome/")
            .to_string(),
        Err(e) => {
            let _ = child.start_kill();
            let _ = child.wait().await;
            // The whole of stderr, so a message that came just before the exit is seen.
            if let Some(r) = reader {
                let _ = tokio::time::timeout(Duration::from_secs(2), r).await;
            }
            let text: String = format!("Chrome did not start ({e}) {}", tail(&stderr))
                .trim()
                .chars()
                .take(280)
                .collect();
            return Err(if sandbox_said.load(Ordering::SeqCst) {
                Refused::NoSandbox(text)
            } else {
                Refused::Other(text)
            });
        }
    };
    Ok(Launched {
        child,
        browser,
        version,
        stderr,
    })
}

/// Close Chrome so it writes its profile (cookies, the open page) for the next start; killed if it will not.
pub async fn close(launched: &mut Launched) {
    let _ = launched
        .browser
        .call_within("Browser.close", json!({}), None, Duration::from_secs(5))
        .await;
    if tokio::time::timeout(Duration::from_secs(10), launched.child.wait())
        .await
        .is_err()
    {
        let _ = launched.child.start_kill();
        let _ = launched.child.wait().await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chrome_uses_a_pipe_and_linux_without_a_display_uses_headless() {
        let f = flags_for(
            Path::new("/state/browsers/c1/profile"),
            None,
            std::env::consts::OS,
            true,
            Some("156.0.8078.79"),
        );
        for wanted in [
            "--user-data-dir=/state/browsers/c1/profile",
            "--remote-debugging-pipe",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-search-engine-choice-screen",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1440,900",
        ] {
            assert!(f.iter().any(|a| a == wanted), "{wanted}");
        }
        for banned in [
            "remote-debugging-port",
            "no-sandbox",
            "enable-automation",
            "user-agent",
            "--lang",
            "proxy-server",
        ] {
            assert!(!f.iter().any(|a| a.contains(banned)), "{banned}");
        }
        let bare = flags_for(
            Path::new("/tmp/profile"),
            None,
            "linux",
            false,
            Some("156.0.8078.79"),
        );
        assert!(bare.iter().any(|s| s == "--headless=new"));
        assert!(
            bare.iter().any(|s| s == "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"),
            "headless sends a windowed Chrome's user agent"
        );
        assert!(!flags_for(
            Path::new("/tmp/profile"),
            None,
            "linux",
            false,
            Some("unknown")
        )
        .iter()
        .any(|s| s.contains("user-agent")));
        assert!(!flags_for(
            Path::new("/tmp/profile"),
            None,
            "linux",
            true,
            Some("156.0.8078.79")
        )
        .iter()
        .any(|s| s.starts_with("--headless") || s.contains("user-agent")));
        let linux = f.iter().any(|a| a == "--password-store=basic");
        assert_eq!(linux, std::env::consts::OS == "linux");
        let proxied = flags_for(
            Path::new("/p"),
            Some("http://127.0.0.1:21001"),
            "macos",
            true,
            None,
        );
        assert!(proxied
            .iter()
            .any(|a| a == "--proxy-server=http://127.0.0.1:21001"));
        assert!(
            !flags_for(Path::new("/p"), Some("http://a b"), "macos", true, None)
                .iter()
                .any(|a| a.starts_with("--proxy-server"))
        );
    }

    #[test]
    fn prefs_are_marked_clean_without_changing_session_restore() {
        let mac = "4A7F0C2E9B";
        let crashed = format!(
            r#"{{"profile":{{"exit_type":"Crashed","exited_cleanly":false,"name":"x"}},"protection":{{"macs":{{"session":{{"restore_on_startup":"{mac}"}}}}}},"session":{{"restore_on_startup":5}}}}"#
        );
        let p: Value = serde_json::from_str(&fixed_prefs(&crashed).unwrap()).unwrap();
        assert_eq!(p["profile"]["exit_type"], "Normal");
        assert_eq!(p["profile"]["exited_cleanly"], true);
        assert_eq!(p["profile"]["name"], "x");
        assert_eq!(
            p["session"]["restore_on_startup"], 5,
            "Chrome's own choice stays"
        );
        assert_eq!(
            p["protection"]["macs"]["session"]["restore_on_startup"],
            mac
        );
        assert!(fixed_prefs(r#"{"session":{}}"#).is_none());
        assert!(fixed_prefs(
            r#"{"profile":{"exit_type":"Normal","exited_cleanly":true},"session":{"restore_on_startup":1}}"#
        )
        .is_none());
        assert!(fixed_prefs("not json").is_none());

        let dir =
            std::env::temp_dir().join(format!("tico-prefs-{}", uuid::Uuid::new_v4().simple()));
        fix_prefs(&dir).unwrap();
        let written: Value = serde_json::from_str(
            &std::fs::read_to_string(dir.join("Default/Preferences")).unwrap(),
        )
        .unwrap();
        assert!(written.get("session").is_none());
        std::fs::write(dir.join("Default/Preferences"), &crashed).unwrap();
        fix_prefs(&dir).unwrap();
        let again: Value = serde_json::from_str(
            &std::fs::read_to_string(dir.join("Default/Preferences")).unwrap(),
        )
        .unwrap();
        assert_eq!(again["profile"]["exit_type"], "Normal");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_app_version_is_read_from_its_plist() {
        let plist = "<dict>\n\t<key>CFBundleName</key>\n\t<string>Chrome</string>\n\t<key>CFBundleShortVersionString</key>\n\t<string>142.0.7444.60</string>\n</dict>";
        assert_eq!(plist_version(plist).as_deref(), Some("142.0.7444.60"));
        assert_eq!(plist_version("<dict></dict>"), None);
    }

    #[tokio::test]
    async fn a_chromium_without_a_usable_sandbox_starts_again_unsandboxed_and_stays_so() {
        let dir = std::env::temp_dir().join(format!("tico-nosb-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let (exe, runs) = (dir.join("chromium"), dir.join("runs"));
        // Chromium where no sandbox can start: it says so and exits, unless told --no-sandbox (then it exits anyway:
        // this one speaks no CDP). `--version` (asked before a headless start on Linux) is not a start.
        std::fs::write(
            &exe,
            format!(
                "#!/bin/sh\ncase \"$*\" in --version) echo 'Chromium 156.0.8078.79'; exit 0 ;; \
                 *--no-sandbox*) echo unsandboxed >> {runs} ;; *) echo sandboxed >> {runs}; \
                 echo '[1:1:FATAL:zygote_host_impl_linux.cc(128)] No usable sandbox! See the docs.' >&2 ;; esac\nexit 1\n",
                runs = runs.display()
            ),
        )
        .unwrap();
        std::fs::set_permissions(&exe, std::os::unix::fs::PermissionsExt::from_mode(0o755))
            .unwrap();
        let profile = dir.join("profile");
        let first = launch(&exe, &profile, None).await.err().unwrap();
        assert!(
            !first.contains("No usable sandbox"),
            "the second start's error: {first}"
        );
        assert_eq!(
            std::fs::read_to_string(&runs).unwrap(),
            "sandboxed\nunsandboxed\n"
        );
        let _ = launch(&exe, &profile, None).await;
        assert_eq!(
            std::fs::read_to_string(&runs).unwrap(),
            "sandboxed\nunsandboxed\nunsandboxed\n",
            "the next start goes straight to --no-sandbox"
        );

        // A Chrome that fails for another reason is never started without its sandbox.
        let other = dir.join("google-chrome");
        std::fs::write(
            &other,
            format!(
                "#!/bin/sh\n[ \"$*\" = --version ] && exit 0\necho \"$*\" >> {}\nexit 1\n",
                dir.join("other").display()
            ),
        )
        .unwrap();
        std::fs::set_permissions(&other, std::os::unix::fs::PermissionsExt::from_mode(0o755))
            .unwrap();
        assert!(launch(&other, &dir.join("p2"), None).await.is_err());
        let args = std::fs::read_to_string(dir.join("other")).unwrap();
        assert_eq!(args.lines().count(), 1);
        assert!(!args.contains("--no-sandbox"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_lock_left_by_a_gone_chrome_is_cleared() {
        let dir = std::env::temp_dir().join(format!("tico-lock-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        // A pid that is not a Chrome on this profile is never signalled; only the lock goes.
        std::os::unix::fs::symlink(
            format!("host-{}", std::process::id()),
            dir.join("SingletonLock"),
        )
        .unwrap();
        clear_orphan(&dir);
        assert!(std::fs::symlink_metadata(dir.join("SingletonLock")).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
