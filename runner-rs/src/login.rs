//! Model sign-in started from the browser (runner/login.py; backend/model_login.py is the server half).
//!
//! The runner starts the model's own login command, in the same home the runtime's readiness check and turns
//! use, and relays what it prints: a link and a one-time code, or a prompt for a code the owner pastes back
//! (typed into a pty). The credential stays on this disk; every line that leaves is scrubbed of anything
//! token-shaped first. Success is not the command's word: sign-in is checked again afterwards.
//!
//! The same relay signs a login out (the CLI's own logout, checked afterwards), switches it to another account
//! (signed in on the side in a home of its own, moved into place only once it has finished and the server has agreed
//! it is no longer cancelled, so a cancelled or failed switch leaves the old login working), and lets the company's shared key sign a signed-out computer login in again.
//! A computer login a person signed out stays signed out (signin.rs SIGNED_OUT_FILE).

use std::collections::HashMap;
use std::io::Read;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{json, Value};

use crate::config::Config;
use crate::env::Env;
use crate::runner::Runner;
use crate::util::{log, text_of};

pub const LIFETIME_S: u64 = 15 * 60;
/// Told to the server in every heartbeat: this runner knows the kinds beyond `sign_in`.
pub const SIGN_OUT_CAPABILITY: &str = "model_sign_out_v1";
const LOGOUT_TIMEOUT: Duration = Duration::from_secs(60);
/// How long a finished switch waits for the server to agree it goes ahead (`finishing`) before giving up.
const CONFIRM_TIMEOUT: Duration = Duration::from_secs(30);
pub const POLL_S: f64 = 2.0;
const KEEP_RAW: usize = 60_000;
const MAX_LINES: usize = 12;
const MAX_LINE: usize = 200;

struct Patterns {
    osc: Regex,
    cursor_column: Regex,
    csi: Regex,
    esc: Regex,
    control: Regex,
    url: Regex,
    user_code: Regex,
    user_code_full: Regex,
    prompt: Regex,
    link: Regex,
    alnum: Regex,
    newline: Regex,
    secrets: Vec<Regex>,
}

fn patterns() -> &'static Patterns {
    static P: OnceLock<Patterns> = OnceLock::new();
    P.get_or_init(|| {
        let r = |p: &str| Regex::new(p).unwrap();
        Patterns {
            osc: r(r"(?s)\x1b\](.*?)(?:\x07|\x1b\\)"),
            cursor_column: r(r"\x1b\[\d*[GC]"),
            csi: r(r"\x1b\[[0-?]*[ -/]*[@-~]"),
            esc: r(r"\x1b[()#][0-9A-Za-z]|\x1b[@-Z\\-_=>78]"),
            control: r(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"),
            // login.py's `(?=\s)` lookahead: the link ends right before whitespace.
            url: r(r#"https://[^\s\x1b"'<>]+\s"#),
            user_code: r(r"[A-Z0-9]{3,8}-[A-Z0-9]{3,8}"),
            user_code_full: r(r"^[A-Z0-9]{3,8}-[A-Z0-9]{3,8}$"),
            prompt: r(r"(?i)paste (?:the )?code|enter (?:the )?code|authorization code"),
            link: r(r"https?://\S*"),
            alnum: r(r"[A-Za-z0-9]"),
            newline: r("\r\n|\n|\r"),
            secrets: vec![
                r(r"\beyJ[\w-]{6,}\.[\w-]{6,}(?:\.[\w-]*)?"),
                r(r"\b(?:sk|pk|rk|ghp|gho|ghs|github_pat|xox[abprs]|AIza)[-_][A-Za-z0-9_\-]{8,}"),
                r(r"(?i)\b(?:access|refresh|id)?[_-]?token\b\s*[:=]\s*\S+"),
                r(r"(?i)\b(?:secret|password|api[_-]?key)\b\s*[:=]\s*\S+"),
                r(r"[A-Za-z0-9+/_\-=]{24,}"),
            ],
        }
    })
}

pub fn redact(text: &str) -> String {
    let mut t = text.to_string();
    for p in &patterns().secrets {
        t = p.replace_all(&t, "[redacted]").into_owned();
    }
    t
}

fn squash(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// What the login command has shown so far: {url, code, lines, prompt} (login.py `parse`). `hide` is a pasted
/// code a terminal may echo.
pub fn parse(raw: &str, hide: &str) -> (String, String, Vec<String>, bool) {
    let p = patterns();
    let mut links = vec![];
    for c in p.osc.captures_iter(raw) {
        let fields: Vec<&str> = c[1].splitn(3, ';').collect();
        if fields[0] == "8" && fields.len() == 3 && fields[2].starts_with("https://") {
            links.push(fields[2].to_string());
        }
    }
    let text = p.osc.replace_all(raw, "");
    let text = p.cursor_column.replace_all(&text, " ");
    let text = p.csi.replace_all(&text, "");
    let text = p.esc.replace_all(&text, "");
    let text = p.control.replace_all(&text, "");
    let pieces: Vec<&str> = p.newline.split(&text).collect();
    let complete = &pieces[..pieces.len() - 1]; // the last piece may still be arriving
    let lines: Vec<String> = pieces.iter().map(|s| squash(s)).collect();
    let mut url = links.first().cloned().unwrap_or_default();
    if url.is_empty() {
        let joined = complete.join("\n") + "\n";
        url = p.url.find(&joined).map(|m| m.as_str().trim_end().trim_end_matches(['.', ',', ')', ';']).to_string()).unwrap_or_default();
    }
    let mut code = String::new();
    for line in complete.iter().map(|s| squash(s)) {
        if p.user_code_full.is_match(&line) || (line.to_lowercase().contains("code") && p.user_code.is_match(&line)) {
            code = p.user_code.find(&line).unwrap().as_str().to_string();
            break;
        }
    }
    let hide: String = hide.chars().take(12).collect();
    let mut shown: Vec<String> = vec![];
    for line in &lines {
        if !hide.is_empty() && line.contains(&hide) {
            continue;
        }
        let line = p.link.replace_all(line, "[link]").into_owned();
        if p.alnum.find_iter(&line).count() < 3 || (!line.contains(' ') && line.chars().count() > 40) {
            continue; // spinners, and pieces of a wrapped link
        }
        let line: String = redact(&line).chars().take(MAX_LINE).collect();
        if shown.last() != Some(&line) {
            shown.push(line);
        }
    }
    let prompt = lines.iter().any(|l| p.prompt.is_match(l));
    let start = shown.len().saturating_sub(MAX_LINES);
    (url, code, shown[start..].to_vec(), prompt)
}

#[derive(Default)]
struct Inner {
    state: String,
    message: String,
    url: String,
    code: String,
    lines: Vec<String>,
    raw: String,
    pending: Option<String>,
    typed: Option<String>,
    taken: bool,
    dirty: bool,
    finished: bool,
}

/// One login command, from start to a verdict.
pub struct Session {
    id: String,
    /// sign_in | switch | sign_out | use_shared_key (backend/model_login.py KINDS).
    kind: String,
    runtime: String,
    profile: String,
    inner: Mutex<Inner>,
    cancelled: AtomicBool,
    /// The server took this switch's `finishing` report: it can no longer be cancelled, so the new login goes in.
    confirmed: AtomicBool,
}

impl Session {
    fn new(work: &Value) -> Arc<Session> {
        Arc::new(Session {
            id: text_of(&work["id"]),
            kind: Some(text_of(&work["kind"])).filter(|k| !k.is_empty()).unwrap_or_else(|| "sign_in".into()),
            runtime: text_of(&work["runtime"]),
            profile: text_of(&work["profile"]),
            inner: Mutex::new(Inner { state: "starting".into(), dirty: true, ..Default::default() }),
            cancelled: AtomicBool::new(false),
            confirmed: AtomicBool::new(false),
        })
    }

    fn live(&self) -> bool {
        !self.inner.lock().unwrap().finished
    }

    fn submit(&self, code: &str) {
        let mut i = self.inner.lock().unwrap();
        if i.pending.is_none() && i.typed.is_none() {
            i.pending = Some(code.into());
        }
    }

    fn report(&self) -> Option<Value> {
        let mut i = self.inner.lock().unwrap();
        if !i.dirty {
            return None;
        }
        i.dirty = false;
        let mut body = json!({"state": i.state, "message": i.message, "url": i.url, "code": i.code, "lines": i.lines});
        if i.taken {
            body["code_taken"] = json!(true);
        }
        Some(body)
    }

    fn finish(&self, state: &str, message: &str, runner: &Runner, default_profile: &str) {
        {
            let mut i = self.inner.lock().unwrap();
            i.state = state.into();
            i.message = message.chars().take(300).collect();
            i.finished = true;
            i.dirty = true;
            i.url.clear();
            i.code.clear();
        }
        log(&format!("Tico runner: {} {} {state}", self.runtime, self.kind.replace('_', " ")));
        // Whatever the verdict, the next heartbeat asks the profile's CLI again instead of a cached answer.
        crate::profiles::forget_report();
        if state == "signed_in" {
            let name = if self.profile.is_empty() { default_profile } else { &self.profile };
            runner.sign_in.clear_rejection(&self.runtime, name);
            // A person signed the computer's own login in: it is no longer signed out on purpose.
            if self.profile.is_empty() {
                if let Err(e) = runner.sign_in.unmark_signed_out(&self.runtime) {
                    log(&format!("Tico runner: {} is signed in but still marked signed out: {e}", self.runtime));
                }
            }
        }
    }
}

fn profile_for(config: &Config, name: &str) -> Option<crate::profiles::Profile> {
    if name.is_empty() {
        crate::profiles::select(&config.data, None, None)
    } else {
        crate::profiles::select(&config.data, None, Some(name))
    }
}

// Login and its verdict must use the same user as runtime readiness and bot turns, as well as the same home.
fn model_command(exe: &str, args: &[&str], env: &Env) -> Command {
    let mut command = crate::isolation::std_command(exe);
    command.args(args).env_clear().envs(env);
    command
}

/// Whether `runtime` is signed in with `env` (the profile's homes), as readiness checks it.
fn signed_in(runtime: &str, exe: &str, env: &Env, headless: bool) -> (bool, String) {
    let run = |args: &[&str], secs: u64| -> Option<(bool, String)> {
        let mut c = model_command(exe, args, env);
        c.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = c.spawn().ok()?;
        let deadline = Instant::now() + Duration::from_secs(secs);
        loop {
            if let Ok(Some(_)) = child.try_wait() {
                let out = child.wait_with_output().ok()?;
                return Some((out.status.success(), format!("{}\n{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr))));
            }
            if Instant::now() > deadline {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    };
    // The same status check readiness runs (the manifest's `sign_in_status`), in this sign-in's own homes.
    let status = crate::harness_tools::caps(runtime, "").sign_in_status;
    let args: Vec<&str> = status.args.iter().map(String::as_str).collect();
    let timeout = if status.timeout_s > 0 { status.timeout_s } else { 15 };
    match status.check.as_str() {
        // A status command is the whole answer: a key the CLI turns into a login is not a sign-in until it has.
        "command" => {
            return match run(&args, timeout).map(|(ok, out)| (ok, out.to_lowercase())) {
                Some((true, out)) if out.contains(&status.ready) && (status.not_ready.is_empty() || !out.contains(&status.not_ready)) => {
                    (true, if out.contains("api key") { "Signed in with an API key".into() } else { status.ready_detail })
                }
                _ => (false, String::new()),
            };
        }
        "json" => {
            let parsed = run(&args, timeout).and_then(|(_, out)| serde_json::from_str::<Value>(out.lines().next().unwrap_or("")).ok().or_else(|| serde_json::from_str(out.trim()).ok()));
            if let Some(s) = parsed.filter(|s| s[&status.field] == true) {
                let method = text_of(&s[&status.method_field]);
                return (true, if method.is_empty() { status.ready_detail } else { format!("Signed in with {method}") });
            }
        }
        // No status command: the login file in this sign-in's config folder is the sign-in, as readiness checks it.
        "file" => {
            if crate::hosts::file_signed_in(runtime, env) == Some(true) {
                return (true, status.ready_detail);
            }
        }
        _ => {}
    }
    (headless, String::new())
}

fn spawn(exe: &str, argv: &[String], env: &Env, terminal: bool) -> std::io::Result<(Child, OwnedFd, Option<OwnedFd>)> {
    let argv: Vec<&str> = argv.iter().map(String::as_str).collect();
    let mut cmd = model_command(exe, &argv, env);
    unsafe {
        cmd.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    if !terminal {
        let mut fds = [0; 2];
        if unsafe { libc::pipe(fds.as_mut_ptr()) } != 0 {
            return Err(std::io::Error::last_os_error());
        }
        let (read, write) = unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) };
        cmd.stdin(Stdio::null()).stdout(Stdio::from(write.try_clone()?)).stderr(Stdio::from(write));
        let child = cmd.spawn()?;
        drop(cmd); // closes our copies of the write end, so the reader sees end of file
        return Ok((child, read, None));
    }
    let (mut master, mut slave) = (0, 0);
    let size = libc::winsize { ws_row: 50, ws_col: 500, ws_xpixel: 0, ws_ypixel: 0 };
    if unsafe { libc::openpty(&mut master, &mut slave, std::ptr::null_mut(), std::ptr::null_mut(), &size as *const _ as *mut _) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    let (master, slave) = unsafe { (OwnedFd::from_raw_fd(master), OwnedFd::from_raw_fd(slave)) };
    unsafe {
        // A pasted code is not echoed back into the output.
        let mut t: libc::termios = std::mem::zeroed();
        libc::tcgetattr(slave.as_raw_fd(), &mut t);
        t.c_lflag &= !libc::ECHO;
        libc::tcsetattr(slave.as_raw_fd(), libc::TCSANOW, &t);
        libc::fcntl(master.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC);
    }
    cmd.stdin(Stdio::from(slave.try_clone()?)).stdout(Stdio::from(slave.try_clone()?)).stderr(Stdio::from(slave));
    let child = cmd.spawn()?;
    drop(cmd);
    let writer = master.try_clone()?;
    Ok((child, master, Some(writer)))
}

fn kill(child: &mut Child) {
    if let Ok(Some(_)) = child.try_wait() {
        return;
    }
    for sig in [libc::SIGTERM, libc::SIGKILL] {
        if unsafe { libc::killpg(child.id() as i32, sig) } != 0 {
            return;
        }
        let until = Instant::now() + Duration::from_secs(2);
        while Instant::now() < until {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    }
}

/// The CLI and the environment a login of `runtime` on `profile` runs with: the same user and homes as its readiness
/// check and turns.
fn login_env(runtime: &str, config: &Config, profile_name: &str) -> Result<(String, Env, Option<crate::profiles::Profile>), String> {
    let path = std::env::var("PATH").unwrap_or_default();
    let Some(exe) = crate::env::which(&crate::harness_tools::executable_for(runtime), &path) else {
        return Err("The runtime is not installed on this computer".into());
    };
    let profile = profile_for(config, profile_name);
    if let Some(Err(e)) = profile.as_ref().map(crate::profiles::hand_to_bot) {
        return Err(format!("The subscription profile is not ready for sign-in: {e}"));
    }
    let process: Env = std::env::vars().collect();
    let mut env = match &profile {
        Some(p) => p.environment(runtime, &process),
        None => process,
    };
    if crate::harness_tools::caps(runtime, "").own_config_home {
        // Bot turns read this host's login from Tico's own config folder, never the operator's.
        crate::hosts::use_own_config_home(runtime, profile.as_ref(), &mut env);
    }
    // The person opens a sign-in link on their own screen, not on this machine.
    env.insert("BROWSER".into(), "true".into());
    env.insert("NO_COLOR".into(), "1".into());
    env.extend(crate::harness_tools::env_for(runtime));
    Ok((exe.display().to_string(), env, profile))
}

/// Run a small file command (mkdir, cp, mv, rm) as the user the CLI runs as, so the login's files stay that user's
/// and nothing is done to them with the supervisor's rights.
fn file_command(args: &[&str], env: &Env) -> Result<(), String> {
    let out = model_command(args[0], &args[1..], env).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::piped()).output()
        .map_err(|e| format!("{} could not run ({:?})", args[0], e.kind()))?;
    if out.status.success() {
        Ok(())
    } else {
        Err(format!("{} failed: {}", args[0], redact(String::from_utf8_lossy(&out.stderr).trim()).chars().take(200).collect::<String>()))
    }
}

/// The folder `runtime`'s login lives in with `env`: its home variable (CODEX_HOME, HOME), or Codex's own default.
fn login_home(runtime: &str, env: &Env) -> Option<std::path::PathBuf> {
    let var = crate::harness_tools::caps(runtime, "").profile_home;
    if var.is_empty() {
        return None;
    }
    match env.get(&var).filter(|v| !v.is_empty()) {
        Some(v) => Some(v.into()),
        None if var == "CODEX_HOME" => env.get("HOME").filter(|h| !h.is_empty()).map(|h| std::path::Path::new(h).join(".codex")),
        None => None,
    }
}

/// A Switch account's own home: a new folder inside the login's home, made as the CLI's user, with the manifest's
/// `seed` files copied in. The login command signs in there; the old login is untouched until `adopt_files`.
fn begin_switch(runtime: &str, id: &str, env: &Env) -> Result<(std::path::PathBuf, std::path::PathBuf), String> {
    let login = crate::harness_tools::login_command(runtime).ok_or("Switching accounts is not available")?;
    let home = login_home(runtime, env).ok_or("Switching accounts is not available for this runtime")?;
    let side = home.join(format!(".tico-switch-{}", id.chars().filter(|c| c.is_ascii_alphanumeric()).take(32).collect::<String>()));
    let (home_s, side_s) = (home.display().to_string(), side.display().to_string());
    file_command(&["mkdir", "-p", "-m", "700", &home_s], env)?;
    file_command(&["mkdir", "-m", "700", &side_s], env)?;
    let seed = || -> Result<(), String> {
        for rel in &login.seed {
            let from = home.join(rel);
            if std::fs::symlink_metadata(&from).is_ok_and(|m| m.is_file()) {
                let to = side.join(rel);
                if let Some(parent) = to.parent() {
                    file_command(&["mkdir", "-p", "-m", "700", &parent.display().to_string()], env)?;
                }
                file_command(&["cp", "-p", &from.display().to_string(), &to.display().to_string()], env)?;
            }
        }
        Ok(())
    };
    // The caller only cleans up a side home it was given, so a setup that fails takes its own away.
    if let Err(e) = seed() {
        let _ = file_command(&["rm", "-rf", &side_s], env);
        return Err(e);
    }
    Ok((home, side))
}

/// One login file a switch put in place, and where the old one was kept until the switch is over (None: none).
struct Moved {
    to: std::path::PathBuf,
    kept: Option<std::path::PathBuf>,
}

/// Put a finished switch's login in place of the old one: each of `files` the new sign-in wrote is moved over the old
/// (a rename, so the CLI never reads half a file), the old one kept in the side home first. A step that fails puts
/// back what was already moved, so the old login is whole again; the error says whether that worked.
fn adopt_files(files: &[String], home: &std::path::Path, side: &std::path::Path, env: &Env) -> Result<Vec<Moved>, String> {
    let old = side.join(".tico-old");
    let mut moved: Vec<Moved> = vec![];
    let mkdir_for = |path: &std::path::Path| match path.parent() {
        Some(parent) => file_command(&["mkdir", "-p", "-m", "700", &parent.display().to_string()], env),
        None => Ok(()),
    };
    for rel in files {
        let from = side.join(rel);
        if !std::fs::symlink_metadata(&from).is_ok_and(|m| m.is_file()) {
            continue;
        }
        let to = home.join(rel);
        let step = (|| -> Result<(), String> {
            if std::fs::symlink_metadata(&to).is_ok() {
                let kept = old.join(rel);
                mkdir_for(&kept)?;
                file_command(&["mv", "-f", &to.display().to_string(), &kept.display().to_string()], env)?;
                moved.push(Moved { to: to.clone(), kept: Some(kept) });
            } else {
                moved.push(Moved { to: to.clone(), kept: None });
            }
            mkdir_for(&to)?;
            file_command(&["mv", "-f", &from.display().to_string(), &to.display().to_string()], env)
        })();
        if let Err(e) = step {
            return Err(match restore(&moved, env) {
                Ok(()) => format!("The new account could not be put in place, so the old one stays: {e}"),
                Err(r) => format!("The new account could not be put in place ({e}), and the old one could not be put back: {r}"),
            });
        }
    }
    Ok(moved)
}

/// Undo `adopt_files`: the old files back where they were, and the new ones that had no old one removed.
fn restore(moved: &[Moved], env: &Env) -> Result<(), String> {
    let mut failed = vec![];
    for m in moved.iter().rev() {
        let to = m.to.display().to_string();
        let done = match &m.kept {
            Some(kept) => file_command(&["mv", "-f", &kept.display().to_string(), &to], env),
            None => file_command(&["rm", "-f", &to], env),
        };
        if let Err(e) = done {
            failed.push(e);
        }
    }
    if failed.is_empty() { Ok(()) } else { Err(failed.join("; ")) }
}

/// The commit point of a switch: tell the server it is `finishing` and wait for it to agree. Once it has, a cancel is
/// refused there, so the new login can go in; a cancel that came first wins, and so does no answer.
fn confirm_switch(s: &Session) -> Result<(), (String, String)> {
    let cancelled = || ("cancelled".to_string(), "Cancelled".to_string());
    {
        let mut i = s.inner.lock().unwrap();
        i.state = "finishing".into();
        i.url.clear();
        i.code.clear();
        i.dirty = true;
    }
    let until = Instant::now() + CONFIRM_TIMEOUT;
    loop {
        if s.confirmed.load(Ordering::SeqCst) {
            return Ok(());
        }
        if s.cancelled.load(Ordering::SeqCst) {
            return Err(cancelled());
        }
        if Instant::now() > until {
            return Err(("failed".into(), "Tico did not confirm the switch in time, so the old account stays".into()));
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn run_session(s: Arc<Session>, runner: Arc<Runner>, config: Config, lifetime: Duration) {
    let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
    let fail = |state: &str, msg: &str| s.finish(state, msg, &runner, &default);
    let Some(login) = crate::harness_tools::login_command(&s.runtime) else { return fail("failed", "Browser sign-in is not available") };
    let (exe, mut env, profile) = match login_env(&s.runtime, &config, &s.profile) {
        Ok(found) => found,
        Err(e) => return fail("failed", &e),
    };
    // A switch signs in on the side: the old login keeps working until the new one is in place.
    let real_env = env.clone();
    let switch = if s.kind == "switch" {
        match begin_switch(&s.runtime, &s.id, &env) {
            Ok((home, side)) => {
                let var = crate::harness_tools::caps(&s.runtime, "").profile_home;
                env.insert(var, side.display().to_string());
                Some((home, side))
            }
            Err(e) => return fail("failed", &e),
        }
    } else {
        None
    };
    let discard = |side: &std::path::Path| {
        if let Err(e) = file_command(&["rm", "-rf", &side.display().to_string()], &real_env) {
            log(&format!("Tico runner: {} switch left {}: {e}", s.runtime, side.display()));
        }
    };
    let (mut child, read_fd, writer) = match spawn(&exe, &login.command, &env, login.terminal) {
        Ok(x) => x,
        Err(e) => {
            if let Some((_, side)) = &switch {
                discard(side);
            }
            return fail("failed", &format!("The sign-in command could not start ({:?})", e.kind()));
        }
    };
    let (done_tx, done_rx) = std::sync::mpsc::channel::<()>();
    let reader = {
        let s = s.clone();
        std::thread::spawn(move || {
            let mut file = std::fs::File::from(read_fd);
            let mut buf = [0u8; 4096];
            while let Ok(n) = file.read(&mut buf) {
                if n == 0 {
                    break;
                }
                let (raw, hide) = {
                    let mut i = s.inner.lock().unwrap();
                    i.raw.push_str(&String::from_utf8_lossy(&buf[..n]));
                    if i.raw.len() > KEEP_RAW {
                        let mut cut = i.raw.len() - KEEP_RAW;
                        while !i.raw.is_char_boundary(cut) {
                            cut += 1;
                        }
                        i.raw.drain(..cut);
                    }
                    (i.raw.clone(), i.typed.clone().unwrap_or_default())
                };
                let (url, code, lines, _) = parse(&raw, &hide);
                let mut i = s.inner.lock().unwrap();
                if (i.url.as_str(), i.code.as_str(), &i.lines) != (url.as_str(), code.as_str(), &lines) {
                    i.url = url;
                    i.code = code;
                    i.lines = lines;
                    i.dirty = true;
                }
                if i.state == "starting" && (!i.url.is_empty() || !i.code.is_empty()) {
                    i.state = "waiting".into();
                    i.dirty = true;
                }
            }
            let _ = done_tx.send(());
        })
    };
    let deadline = Instant::now() + lifetime;
    let verdict: Option<(String, String)> = loop {
        match child.try_wait() {
            Ok(Some(_)) | Err(_) => break None,
            Ok(None) => {}
        }
        if s.cancelled.load(Ordering::SeqCst) {
            kill(&mut child);
            break Some(("cancelled".into(), "Cancelled".into()));
        }
        if Instant::now() > deadline {
            kill(&mut child);
            break Some(("failed".into(), "The sign-in was not finished in time".into()));
        }
        if let Some(w) = &writer {
            let code = s.inner.lock().unwrap().pending.take();
            if let Some(code) = code {
                let line = format!("{code}\r");
                let wrote = unsafe { libc::write(w.as_raw_fd(), line.as_ptr() as *const _, line.len()) };
                if wrote >= 0 {
                    let mut i = s.inner.lock().unwrap();
                    i.typed = Some(code);
                    i.taken = true;
                    i.dirty = true;
                }
            }
        }
        std::thread::sleep(Duration::from_millis(100));
    };
    drop(writer); // the master closes, so the reader ends on a pty too
    let _ = done_rx.recv_timeout(Duration::from_secs(3));
    // Kept only when the old login's files are still in it, so a failed restore never deletes them.
    let mut keep_side = false;
    let (state, message) = match verdict {
        Some(v) => v,
        None if s.cancelled.load(Ordering::SeqCst) => ("cancelled".into(), "Cancelled".into()),
        None if !child.wait().map(|st| st.success()).unwrap_or(false) => ("failed".into(), "The sign-in command stopped without signing in".into()),
        None if switch.is_some() => {
            // Signed in on the side? Then, once the server agrees, that login takes the old one's place and is checked
            // where it now is; if it does not work there, the old one goes back.
            let (home, side) = switch.as_ref().unwrap();
            let files = login.files.clone();
            match signed_in(&s.runtime, &exe, &env, false) {
                (true, _) => match confirm_switch(&s) {
                    Err(verdict) => verdict,
                    Ok(()) => match adopt_files(&files, home, side, &real_env) {
                        Ok(moved) => match signed_in(&s.runtime, &exe, &real_env, false) {
                            (true, detail) => ("signed_in".into(), if detail.is_empty() { "Switched account".into() } else { detail }),
                            _ => match restore(&moved, &real_env) {
                                Ok(()) => ("failed".into(), format!("The new account did not work in place of the old one, so the old one is back on {}", s.runtime)),
                                Err(e) => {
                                    keep_side = true;
                                    ("failed".into(), format!("The new account did not work, and the old one could not be put back ({e}); its files are in {}", side.display()))
                                }
                            },
                        },
                        Err(e) => {
                            keep_side = e.contains("could not be put back");
                            ("failed".into(), e)
                        }
                    },
                },
                _ => ("failed".into(), format!("The command finished but {} is not signed in to the new account; the old one stays", s.runtime)),
            }
        }
        None => {
            let headless = profile.is_none() && runner.sign_in.headless_secret(&s.runtime).is_some();
            match signed_in(&s.runtime, &exe, &env, headless) {
                (true, detail) => ("signed_in".into(), if detail.is_empty() { "Signed in".into() } else { detail }),
                _ => ("failed".into(), format!("The command finished but {} is still not signed in", s.runtime)),
            }
        }
    };
    if let Some((_, side)) = &switch {
        if !keep_side {
            discard(side);
        }
        if state == "signed_in" {
            // The new account's email and plan are read afresh, not the old one's.
            crate::accounts::forget(&profile.as_ref().map(|p| p.name.clone()).unwrap_or_default(), &s.runtime);
        }
    }
    s.finish(&state, &message, &runner, &default);
    {
        let mut i = s.inner.lock().unwrap();
        i.typed = None;
        i.pending = None;
        i.raw.clear();
    }
    if reader.is_finished() {
        let _ = reader.join();
    }
}

/// Sign `runtime` out: the computer's own login is marked signed out first (so no key signs it straight back in),
/// then the CLI's own logout runs and the sign-in is checked again. A logout that did not take leaves it as it was.
fn run_sign_out(s: Arc<Session>, runner: Arc<Runner>, config: Config) {
    let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
    let fail = |msg: &str| s.finish("failed", msg, &runner, &default);
    let logout = crate::harness_tools::login_command(&s.runtime).map(|l| l.logout).unwrap_or_default();
    if logout.is_empty() {
        return fail(&format!("Signing out from the app is not available for {}", s.runtime));
    }
    let (exe, env, profile) = match login_env(&s.runtime, &config, &s.profile) {
        Ok(found) => found,
        Err(e) => return fail(&e),
    };
    let own = s.profile.is_empty();
    if own {
        if let Err(e) = runner.sign_in.mark_signed_out(&s.runtime) {
            return fail(&format!("Could not record the sign-out on this computer ({:?})", e.kind()));
        }
        // A key login that started before the mark could still be writing a login: let it end before logging out,
        // or it would write the login back after the check below said signed out.
        if !runner.sign_in.wait_for_key_logins(&s.runtime, crate::signin::KEY_LOGIN_DRAIN) {
            let _ = runner.sign_in.unmark_signed_out(&s.runtime);
            return fail(&format!("{} is still signing in with a key on this computer; try again in a minute", s.runtime));
        }
    }
    // Its exit status is not the verdict (an already signed-out CLI may say so with an error): the check after it is.
    let args: Vec<&str> = logout.iter().map(String::as_str).collect();
    let ran = model_command(&exe, &args, &env).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn();
    if let Ok(mut child) = ran {
        let until = Instant::now() + LOGOUT_TIMEOUT;
        while !matches!(child.try_wait(), Ok(Some(_)) | Err(_)) {
            if Instant::now() > until {
                kill(&mut child);
                break;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    }
    if signed_in(&s.runtime, &exe, &env, false).0 {
        if own {
            let _ = runner.sign_in.unmark_signed_out(&s.runtime);
        }
        return fail(&format!("The logout finished but {} is still signed in", s.runtime));
    }
    let account = profile.as_ref().map(|p| p.name.clone()).unwrap_or_default();
    crate::accounts::forget(&account, &s.runtime);
    crate::profiles::forget_windows(&account, &s.runtime);
    s.finish("signed_out", "Signed out", &runner, &default);
}

/// The browser sign-ins this runner is running, kept in step with the server's list.
pub struct Logins {
    sessions: HashMap<String, Arc<Session>>,
    polled: Option<Instant>,
    pub lifetime: Duration,
}

impl Default for Logins {
    fn default() -> Self {
        Logins { sessions: HashMap::new(), polled: None, lifetime: Duration::from_secs(LIFETIME_S) }
    }
}

impl Logins {
    pub fn busy(&self) -> bool {
        !self.sessions.is_empty()
    }

    fn begin(&mut self, runner: &Arc<Runner>, work: &Value) {
        let s = Session::new(work);
        // Read again: a profile `profile add` (or a sign-in) made since start is in the file, not in memory.
        let mut config = runner.config_now();
        let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
        let named = (!s.profile.is_empty()).then(|| profile_for(&config, &s.profile)).flatten();
        if crate::harness_tools::login_command(&s.runtime).is_none() {
            s.finish("failed", &format!("Browser sign-in is not available for {}", s.runtime), runner, &default);
        } else if !["sign_in", "switch", "sign_out", "use_shared_key"].contains(&s.kind.as_str()) {
            // Never a sign-in in place of something this runner does not know.
            s.finish("failed", &format!("Update this computer's runner to {}", s.kind.replace('_', " ")), runner, &default);
        } else if self.sessions.values().any(|o| o.live() && o.runtime == s.runtime && o.profile == s.profile) {
            s.finish("failed", &format!("Another sign-in is already running for {}", s.runtime), runner, &default);
        } else if named.as_ref().is_some_and(|p| p.share_operator) && !crate::harness_tools::caps(&s.runtime, "").own_config_home {
            // A host with its own config folder never uses the operator's login, so a shared profile still signs in to Tico's own.
            s.finish("failed", &format!("Profile {} shares the operator's own logins; sign in normally", s.profile), runner, &default);
        } else if s.kind == "switch"
            && crate::harness_tools::login_command(&s.runtime).is_some_and(|l| l.keychain.iter().any(|p| p == crate::runner::platform()))
        {
            // The login is in the system keychain here, not in files: there is no side to sign in on.
            let msg = format!("Switching accounts isn't available for {} on this computer: sign out, then sign in", s.runtime);
            s.finish("failed", &msg, runner, &default);
        } else if s.kind == "use_shared_key" {
            let state = match runner.sign_in.unmark_signed_out(&s.runtime) {
                Ok(()) => ("done", "The shared key signs it in again".to_string()),
                Err(e) => ("failed", format!("Could not change this computer's sign-out ({:?})", e.kind())),
            };
            s.finish(state.0, &state.1, runner, &default);
        } else if s.kind == "sign_out" && !s.profile.is_empty() && named.is_none() {
            s.finish("failed", &format!("Subscription {} is not on this computer", s.profile), runner, &default);
        } else if s.kind == "sign_out" {
            let (s2, r2) = (s.clone(), runner.clone());
            std::thread::spawn(move || run_sign_out(s2, r2, config));
        } else {
            if !s.profile.is_empty() && named.is_none() {
                if let Err(e) = crate::profiles::add_profile(&config, &s.profile) {
                    s.finish("failed", &format!("Could not create subscription profile: {e}"), runner, &default);
                    self.sessions.insert(s.id.clone(), s);
                    return;
                }
                config = runner.config_now();
                // Listed at the next heartbeat as not signed in; a failed or abandoned sign-in leaves it so, never hidden.
                runner.events.ask(&["heartbeat"]);
            }
            let (s2, r2, lifetime) = (s.clone(), runner.clone(), self.lifetime);
            std::thread::spawn(move || run_session(s2, r2, config, lifetime));
        }
        self.sessions.insert(s.id.clone(), s);
    }

    /// Ask the server what to run, then say what happened.
    pub async fn poll(&mut self, runner: &Arc<Runner>, force: bool) -> Result<(), crate::client::ApiError> {
        if !force && self.polled.is_some_and(|t| t.elapsed().as_secs_f64() < POLL_S) {
            return Ok(());
        }
        self.polled = Some(Instant::now());
        let reply = runner.client.get("runner-logins").await?;
        let mut wanted: HashMap<String, Value> = HashMap::new();
        for row in reply["logins"].as_array().cloned().unwrap_or_default() {
            let id = text_of(&row["id"]);
            if row.is_object() && !id.is_empty() {
                wanted.insert(id, row);
            }
        }
        for (id, s) in &self.sessions {
            if s.live() && !wanted.contains_key(id) {
                s.cancelled.store(true, Ordering::SeqCst); // cancelled or expired on the server
            }
        }
        let mut ids: Vec<&String> = wanted.keys().collect();
        ids.sort();
        for id in ids {
            let row = &wanted[id];
            match self.sessions.get(id) {
                None => self.begin(runner, row),
                Some(s) if !text_of(&row["code"]).is_empty() => s.submit(&text_of(&row["code"])),
                _ => {}
            }
        }
        self.flush(runner).await;
        Ok(())
    }

    async fn flush(&mut self, runner: &Arc<Runner>) {
        let ids: Vec<String> = self.sessions.keys().cloned().collect();
        for id in ids {
            let s = self.sessions[&id].clone();
            let body = s.report().filter(|b| b["state"] != "cancelled"); // the server ended it: nothing to tell it
            if let Some(body) = body {
                let reply = match runner.client.post(&format!("runner-logins/{id}/report"), &body, None).await {
                    Ok(reply) => reply,
                    Err(_) => {
                        s.inner.lock().unwrap().dirty = true; // said again on the next poll
                        continue;
                    }
                };
                // A switch goes ahead only once the server holds it as finishing; if it had already ended there
                // (cancelled, expired), the runner stops too.
                match text_of(&reply["state"]).as_str() {
                    "finishing" if body["state"] == "finishing" => s.confirmed.store(true, Ordering::SeqCst),
                    "cancelled" | "expired" if s.live() => s.cancelled.store(true, Ordering::SeqCst),
                    _ => {}
                }
                {
                    let mut i = s.inner.lock().unwrap();
                    i.taken = i.taken && body.get("code_taken").is_none();
                }
                if s.inner.lock().unwrap().finished {
                    runner.events.ask(&["heartbeat"]); // let readiness and the profiles show the verdict now
                }
            }
            let i = s.inner.lock().unwrap();
            if i.finished && !i.dirty {
                drop(i);
                self.sessions.remove(&id);
            }
        }
    }

    pub fn stop(&self) {
        for s in self.sessions.values() {
            s.cancelled.store(true, Ordering::SeqCst);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_switch_whose_setup_fails_leaves_no_side_folder() {
        use std::os::unix::fs::PermissionsExt;
        if unsafe { libc::geteuid() } == 0 {
            return; // root reads an unreadable file, so the copy below would not fail
        }
        let home = std::env::temp_dir().join(format!("tico-switch-setup-{}", std::process::id()));
        std::fs::create_dir_all(&home).unwrap();
        // Claude Code seeds the side home with .claude.json; one the copy cannot read fails the setup.
        let seed = home.join(".claude.json");
        std::fs::write(&seed, "{}").unwrap();
        std::fs::set_permissions(&seed, std::fs::Permissions::from_mode(0o000)).unwrap();
        let env: Env = [("HOME".to_string(), home.display().to_string()), ("PATH".to_string(), std::env::var("PATH").unwrap())].into();
        let err = begin_switch("claude", "setup1", &env).unwrap_err();
        assert!(err.starts_with("cp failed"), "{err}");
        assert!(!home.join(".tico-switch-setup1").exists(), "the half-made side home is gone");
        std::fs::set_permissions(&seed, std::fs::Permissions::from_mode(0o600)).unwrap();
        std::fs::remove_dir_all(&home).unwrap();
    }

    #[test]
    fn login_and_verdict_commands_use_the_bot_identity_and_profile_home() {
        const CHILD: &str = "TICO_TEST_LOGIN_IDENTITY";
        if std::env::var_os(CHILD).is_none() {
            // A separate process keeps the configured isolation identity out of other parallel tests.
            let uid = if unsafe { libc::geteuid() } == 10003 { 10004 } else { 10003 };
            let out = Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "login::tests::login_and_verdict_commands_use_the_bot_identity_and_profile_home", "--nocapture"])
                .env(CHILD, "1").env(crate::isolation::UID_ENV, uid.to_string()).env(crate::isolation::GID_ENV, "10002")
                .output().unwrap();
            assert!(out.status.success(), "{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
            return;
        }
        let uid = std::env::var(crate::isolation::UID_ENV).unwrap();
        let env: Env = [("HOME".into(), "/subscriptions/acme/claude".into())].into_iter().collect();
        for args in [&["auth", "login", "--claudeai"][..], &["auth", "status", "--json"][..]] {
            let command = model_command("claude", args, &env);
            assert_eq!(command.get_program(), crate::isolation::SETPRIV);
            let mut expected = vec![format!("--reuid={uid}"), "--regid=10002".into(), "--clear-groups".into(),
                "--inh-caps=-all".into(), "--ambient-caps=-all".into(), "claude".into()];
            expected.extend(args.iter().map(|s| s.to_string()));
            assert_eq!(command.get_args().map(|a| a.to_string_lossy().into_owned()).collect::<Vec<_>>(), expected);
            assert!(command.get_envs().any(|(key, value)| key == "HOME" && value == Some(std::ffi::OsStr::new("/subscriptions/acme/claude"))));
        }
    }

    /// A stand-in `codex`, each run recorded in $HOME/runs: `login status` reads $CODEX_HOME/auth.json (on a switch's
    /// side home it first waits for $HOME/release while $HOME/hold-probe is there; in the real home it says signed
    /// out while $HOME/refuse is there), `logout` removes it (unless $HOME/stuck), and `login --device-auth` shows a
    /// code and writes a new login (or hangs while $HOME/slow is there).
    const FAKE_CODEX: &str = r#"#!/bin/sh
home="${CODEX_HOME:-$HOME/.codex}"
echo "$@" >> "$HOME/runs"
case "$1 $2" in
  "login status")
    case "$home" in
      *tico-switch-*) if [ -f "$HOME/hold-probe" ]; then : > "$HOME/probing"; n=0; while [ ! -f "$HOME/release" ] && [ $n -lt 200 ]; do sleep 0.05; n=$((n+1)); done; fi ;;
      *) if [ -f "$HOME/refuse" ]; then echo "Not signed in"; exit 1; fi ;;
    esac
    if [ -f "$home/auth.json" ]; then echo "Logged in using ChatGPT"; exit 0; fi; echo "Not signed in"; exit 1 ;;
  "logout ") [ -f "$HOME/stuck" ] || rm -f "$home/auth.json"; exit 0 ;;
  "login --device-auth")
    echo "Open https://auth.openai.com/codex/device"; echo "Enter this one-time code"; echo "ABCD-1234"
    if [ -f "$HOME/slow" ]; then sleep 30; exit 1; fi
    mkdir -p "$home"; echo new > "$home/auth.json"; exit 0 ;;
esac
exit 2
"#;

    fn session(kind: &str) -> Arc<Session> {
        Session::new(&json!({"id": format!("l{}", uuid::Uuid::new_v4().simple()), "runtime": "codex", "kind": kind}))
    }

    fn state(s: &Session) -> (String, String) {
        let i = s.inner.lock().unwrap();
        (i.state.clone(), i.message.clone())
    }

    fn wait_for(what: impl Fn() -> bool) {
        let until = Instant::now() + Duration::from_secs(10);
        while !what() && Instant::now() < until {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(what(), "timed out waiting");
    }

    /// Run a switch on its own thread, the way the relay does, with `server` standing in for Tico's answer once the
    /// runner says it is finishing (true: confirmed; false: it had been cancelled).
    fn switch_with(runner: &Arc<Runner>, config: &Config, server: Option<bool>) -> (Arc<Session>, std::thread::JoinHandle<()>) {
        let s = session("switch");
        let (s2, r2, c2) = (s.clone(), runner.clone(), config.clone());
        let running = std::thread::spawn(move || run_session(s2, r2, c2, Duration::from_secs(20)));
        if let Some(confirm) = server {
            let s3 = s.clone();
            std::thread::spawn(move || {
                let until = Instant::now() + Duration::from_secs(15);
                while state(&s3).0 != "finishing" && s3.live() && Instant::now() < until {
                    std::thread::sleep(Duration::from_millis(20));
                }
                if state(&s3).0 == "finishing" {
                    if confirm { s3.confirmed.store(true, Ordering::SeqCst) } else { s3.cancelled.store(true, Ordering::SeqCst) }
                }
            });
        }
        (s, running)
    }

    #[test]
    fn sign_out_and_switch_account_on_the_computers_own_codex_login() {
        const CHILD: &str = "TICO_TEST_SIGN_OUT";
        let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
            // A process of its own: PATH, HOME and CODEX_HOME point at a stand-in CLI and a scratch home.
            let root = std::env::temp_dir().join(format!("tico-sign-out-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir_all(root.join("bin")).unwrap();
            std::fs::write(root.join("bin/codex"), FAKE_CODEX).unwrap();
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(root.join("bin/codex"), std::fs::Permissions::from_mode(0o755)).unwrap();
            let path = format!("{}:/usr/bin:/bin", root.join("bin").display());
            let out = Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "login::tests::sign_out_and_switch_account_on_the_computers_own_codex_login", "--nocapture"])
                .env(CHILD, &root).env("PATH", path).env("HOME", &root).env("CODEX_HOME", root.join("codex"))
                .env_remove(crate::isolation::UID_ENV).env_remove(crate::isolation::GID_ENV)
                .env_remove("OPENAI_API_KEY").output().unwrap();
            std::fs::remove_dir_all(&root).ok();
            let said = String::from_utf8_lossy(&out.stdout);
            assert!(out.status.success() && said.contains("1 passed"), "{said}{}", String::from_utf8_lossy(&out.stderr));
            return;
        };
        std::fs::create_dir_all(root.join("projects/secrets")).unwrap();
        std::fs::create_dir_all(root.join("codex")).unwrap();
        let path = root.join("runner.json");
        std::fs::write(&path, json!({"url": "http://127.0.0.1:9", "token": "t", "runner_id": "r1", "projects_dir": root.join("projects")}).to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        let client = crate::client::Client::new(&config.url(), &config.token(), 1, 0).unwrap();
        let runner = Arc::new(Runner::new(config.clone(), client, crate::state::State::open(&root.join("state")).unwrap()));
        let auth = root.join("codex/auth.json");
        let team = root.join("projects/secrets").join(crate::signin::TEAM_KEYS_FILE);
        let sides = || std::fs::read_dir(root.join("codex")).unwrap().flatten().filter(|e| e.file_name().to_string_lossy().starts_with(".tico-switch-")).count();

        // Sign out: the login and the company's key this computer held are gone, and it is marked signed out.
        std::fs::write(&auth, "old\n").unwrap();
        std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        let out = session("sign_out");
        run_sign_out(out.clone(), runner.clone(), config.clone());
        assert_eq!(state(&out).0, "signed_out", "{:?}", state(&out));
        assert!(!auth.exists() && runner.sign_in.signed_out("codex") && runner.sign_in.headless_secret("codex").is_none());
        assert!(!std::fs::read_to_string(&team).unwrap().contains("OPENAI_API_KEY"));

        // A browser sign-in is a person signing in again: the mark goes.
        let back = session("sign_in");
        run_session(back.clone(), runner.clone(), config.clone(), Duration::from_secs(20));
        assert_eq!(state(&back).0, "signed_in", "{:?}", state(&back));
        assert!(!runner.sign_in.signed_out("codex"));

        // A logout that did not take: failed, and not left marked signed out.
        std::fs::write(root.join("stuck"), "").unwrap();
        let stuck = session("sign_out");
        run_sign_out(stuck.clone(), runner.clone(), config.clone());
        assert_eq!(state(&stuck), ("failed".into(), "The logout finished but codex is still signed in".into()));
        assert!(auth.exists() && !runner.sign_in.signed_out("codex"));
        std::fs::remove_file(root.join("stuck")).unwrap();

        // Switch account: signed in on the side, then, once Tico confirms, put in place; the side home is gone.
        std::fs::write(&auth, "old\n").unwrap();
        let (switch, running) = switch_with(&runner, &config, Some(true));
        running.join().unwrap();
        assert_eq!(state(&switch).0, "signed_in", "{:?}", state(&switch));
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "new\n");
        assert_eq!(sides(), 0);

        // Nothing moves before Tico confirms: a switch Tico says was cancelled meanwhile keeps the old login.
        std::fs::write(&auth, "old\n").unwrap();
        let (refused, running) = switch_with(&runner, &config, Some(false));
        running.join().unwrap();
        assert_eq!(state(&refused).0, "cancelled", "{:?}", state(&refused));
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");
        assert_eq!(sides(), 0);

        // A cancel that arrives while the new login is being checked on the side wins too.
        std::fs::write(root.join("hold-probe"), "").unwrap();
        let (late, running) = switch_with(&runner, &config, Some(true));
        wait_for(|| root.join("probing").exists());
        late.cancelled.store(true, Ordering::SeqCst);
        std::fs::write(root.join("release"), "").unwrap();
        running.join().unwrap();
        assert_eq!(state(&late).0, "cancelled", "{:?}", state(&late));
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");
        assert_eq!(sides(), 0);
        for f in ["hold-probe", "probing", "release"] {
            std::fs::remove_file(root.join(f)).unwrap();
        }

        // The new login, once in place, does not work: the old one goes back.
        std::fs::write(root.join("refuse"), "").unwrap();
        let (broken, running) = switch_with(&runner, &config, Some(true));
        running.join().unwrap();
        assert_eq!(state(&broken).0, "failed");
        assert!(state(&broken).1.contains("the old one is back"), "{:?}", state(&broken));
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");
        assert_eq!(sides(), 0);
        std::fs::remove_file(root.join("refuse")).unwrap();

        // Two login files, and the second cannot be moved in: the first is put back, so the old login is whole.
        let (home, side) = (root.join("multi/home"), root.join("multi/side"));
        std::fs::create_dir_all(&home).unwrap();
        std::fs::create_dir_all(side.join("sub")).unwrap();
        std::fs::write(home.join("a.json"), "old-a").unwrap();
        std::fs::write(home.join("sub"), "a file where a folder should be").unwrap();
        std::fs::write(side.join("a.json"), "new-a").unwrap();
        std::fs::write(side.join("sub/b.json"), "new-b").unwrap();
        let env: Env = std::env::vars().collect();
        let files = vec!["a.json".to_string(), "sub/b.json".to_string()];
        let err = adopt_files(&files, &home, &side, &env).err().expect("the second move fails");
        assert!(err.contains("so the old one stays"), "{err}");
        assert_eq!(std::fs::read_to_string(home.join("a.json")).unwrap(), "old-a");
        // And a whole move undone: the old file back, the new one that had no old one gone.
        std::fs::remove_file(home.join("sub")).unwrap();
        let moved = adopt_files(&files, &home, &side, &env).unwrap();
        assert_eq!(std::fs::read_to_string(home.join("sub/b.json")).unwrap(), "new-b");
        restore(&moved, &env).unwrap();
        assert_eq!(std::fs::read_to_string(home.join("a.json")).unwrap(), "old-a");
        assert!(!home.join("sub/b.json").exists());

        // A kind this runner does not know is refused, and nothing runs or changes.
        let runs = std::fs::read_to_string(root.join("runs")).unwrap_or_default();
        let mut logins = Logins::default();
        logins.begin(&runner, &json!({"id": "lnew", "runtime": "codex", "kind": "rename_account"}));
        let unknown = logins.sessions["lnew"].clone();
        assert_eq!(state(&unknown), ("failed".into(), "Update this computer's runner to rename account".into()));
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(std::fs::read_to_string(root.join("runs")).unwrap_or_default(), runs, "a CLI ran for an unknown kind");
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");

        // A switch cancelled partway leaves the old login exactly as it was.
        std::fs::write(&auth, "old\n").unwrap();
        std::fs::write(root.join("slow"), "").unwrap();
        let cancelled = session("switch");
        let (s2, r2, c2) = (cancelled.clone(), runner.clone(), config.clone());
        let running = std::thread::spawn(move || run_session(s2, r2, c2, Duration::from_secs(20)));
        let until = Instant::now() + Duration::from_secs(10);
        while state(&cancelled).0 != "waiting" && Instant::now() < until {
            std::thread::sleep(Duration::from_millis(50));
        }
        assert_eq!(sides(), 1, "the switch signs in on the side");
        cancelled.cancelled.store(true, Ordering::SeqCst);
        running.join().unwrap();
        assert_eq!(state(&cancelled).0, "cancelled");
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");
        assert_eq!(sides(), 0);
    }

    #[test]
    fn a_muse_sign_in_is_its_login_file_not_a_status_command() {
        let dir = std::env::temp_dir().join(format!("muse-signin-{}", uuid::Uuid::new_v4().simple()));
        let env: Env = [("XDG_CONFIG_HOME".to_string(), dir.display().to_string())].into_iter().collect();
        // `muse` has no `auth status`; the executable is never run for it.
        assert!(!signed_in("musecode", "/nonexistent/muse", &env, false).0);
        std::fs::create_dir_all(dir.join("muse")).unwrap();
        std::fs::write(dir.join("muse").join("auth.json"), "{}").unwrap();
        let signed = signed_in("musecode", "/nonexistent/muse", &env, false);
        std::fs::remove_dir_all(&dir).unwrap();
        assert_eq!(signed, (true, "Signed in with a Meta account".into()));
    }

    #[test]
    fn device_code_and_link_are_read_and_secrets_scrubbed() {
        let raw = "\x1b[1mWelcome\x1b[0m\r\nOpen this link\r\n  https://auth.openai.com/codex/device \r\nEnter this one-time code\r\n   ABCD-1234\r\n\
                   access_token: abcdefghijklmnop\r\nsk-proj-aaaaaaaaaaaaaaaa here\r\nspinner\r\n";
        let (url, code, lines, prompt) = parse(raw, "");
        assert_eq!(url, "https://auth.openai.com/codex/device");
        assert_eq!(code, "ABCD-1234");
        assert!(!prompt);
        assert!(lines.contains(&"[link]".to_string()));
        assert!(lines.iter().any(|l| l == "[redacted]"), "{lines:?}");
        assert!(lines.iter().any(|l| l == "[redacted] here"), "{lines:?}");
        assert!(!lines.iter().any(|l| l.contains("abcdefghijklmnop")));
    }

    #[test]
    fn osc8_link_wins_and_typed_code_is_hidden() {
        let raw = "\x1b]8;;https://claude.ai/oauth/authorize?code=true\x07Sign in\x1b]8;;\x07\r\nPaste code here if prompted >\r\nsecretcode12345 typed\r\n";
        let (url, _, lines, prompt) = parse(raw, "secretcode12345");
        assert_eq!(url, "https://claude.ai/oauth/authorize?code=true");
        assert!(prompt);
        assert!(!lines.iter().any(|l| l.contains("secretcode")));
    }
}
