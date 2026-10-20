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
//!
//! It also clears this computer's own model key (`clear_key`) and puts a new one in its place (`use_key`), or in place of
//! the subscription its own login runs on (`to_key`, Switch to API key). No key ever travels in the queue: a new key is
//! fetched from the app over the runner's own connection.

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
/// Told to the server in every heartbeat: this runner clears and replaces its own model key (`clear_key`, `use_key`).
pub const KEYS_CAPABILITY: &str = "model_keys_v1";
/// Switch to subscription (`to_subscription`): a switch for the computer's own login that also takes its API key off.
pub const SWITCH_TYPE_CAPABILITY: &str = "model_switch_type_v1";
/// Remove (`remove`): a named subscription's login for one tool comes off the computer, then the subscription itself.
pub const REMOVE_CAPABILITY: &str = "model_remove_v1";
/// Switch to API key (`to_key`): a use_key job for the computer's own login on a subscription, which signs the
/// subscription out once the key is in place.
pub const SWITCH_TO_KEY_CAPABILITY: &str = "model_switch_to_key_v1";
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
    /// sign_in | switch | sign_out | use_shared_key | clear_key | use_key | to_subscription | to_key (backend/model_login.py KINDS).
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
    let command = crate::harness_tools::caps(runtime, "").sign_in_status.check == "command";
    match sign_in_status(runtime, exe, env) {
        Some((true, detail)) => (true, detail),
        // A status command is the whole answer: a key the CLI turns into a login is not a sign-in until it has.
        _ if command => (false, String::new()),
        _ => (headless, String::new()),
    }
}

/// The status check's own answer: Some(signed in, detail), or None when it gave none (it could not run, timed
/// out or said something unreadable, or this runtime has no check).
fn sign_in_status(runtime: &str, exe: &str, env: &Env) -> Option<(bool, String)> {
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
        "command" => {
            let (ok, out) = run(&args, timeout)?;
            let out = out.to_lowercase();
            if ok && out.contains(&status.ready) && (status.not_ready.is_empty() || !out.contains(&status.not_ready)) {
                Some((true, if out.contains("api key") { "Signed in with an API key".into() } else { status.ready_detail }))
            } else {
                Some((false, String::new()))
            }
        }
        "json" => {
            let (_, out) = run(&args, timeout)?;
            let s = serde_json::from_str::<Value>(out.lines().next().unwrap_or("")).ok().or_else(|| serde_json::from_str(out.trim()).ok())?;
            // Only a true or false is an answer: an error report, or a field that is missing or null, says nothing.
            if !s[&status.field].as_bool()? {
                return Some((false, String::new()));
            }
            let method = text_of(&s[&status.method_field]);
            Some((true, if method.is_empty() { status.ready_detail } else { format!("Signed in with {method}") }))
        }
        // No status command: the login file in this sign-in's config folder is the sign-in, as readiness checks it.
        "file" => crate::hosts::file_signed_in(runtime, env).map(|on| (on, if on { status.ready_detail } else { String::new() })),
        _ => None,
    }
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

/// The commit point of a switch or a removal: tell the server it is `finishing` and wait for it to agree. Once it
/// has, a cancel is refused there, so the change can go ahead; a cancel (or a refusal) that came first wins, and so
/// does no answer.
fn confirm_commit(s: &Session, late: &str) -> Result<(), (String, String)> {
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
            return Err(("failed".into(), late.into()));
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

/// Switch to subscription, past its commit point: before the new login goes in, key logins are held and one already
/// running is let finish, so none writes the key's login over the subscription. The key itself stays in use, and the
/// company's copy stays on this computer, until the subscription works in place (then `mark_key_off`).
fn hold_key_logins<'a>(s: &Session, runner: &'a Runner) -> Result<crate::signin::KeyChange<'a>, (String, String)> {
    let Some(held) = runner.sign_in.begin_key_change(&s.runtime, crate::signin::KEY_LOGIN_DRAIN) else {
        return Err(("failed".into(), format!("{} key is changing on this computer; try again in a minute", s.runtime)));
    };
    if !runner.sign_in.wait_for_key_logins(&s.runtime, crate::signin::KEY_LOGIN_DRAIN) {
        return Err(("failed".into(), format!("{} is still signing in with its key; try again in a minute", s.runtime)));
    }
    Ok(held)
}

fn run_session(s: Arc<Session>, runner: Arc<Runner>, config: Config, lifetime: Duration) {
    let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
    let fail = |state: &str, msg: &str| s.finish(state, msg, &runner, &default);
    let Some(login) = crate::harness_tools::login_command(&s.runtime) else { return fail("failed", "Browser sign-in is not available") };
    let (exe, mut env, profile) = match login_env(&s.runtime, &config, &s.profile) {
        Ok(found) => found,
        Err(e) => return fail("failed", &e),
    };
    // A switch signs in on the side: the old login keeps working until the new one is in place. So does a switch to
    // a subscription, whose old login is the API key.
    let real_env = env.clone();
    let to_subscription = s.kind == "to_subscription";
    let switch = if s.kind == "switch" || to_subscription {
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
                (true, _) => match confirm_commit(&s, "Tico did not confirm the switch in time, so the old account stays")
                    .and_then(|()| if to_subscription { hold_key_logins(&s, &runner).map(Some) } else { Ok(None) })
                {
                    Err(verdict) => verdict,
                    Ok(_held) => match adopt_files(&files, home, side, &real_env) {
                        Ok(moved) => match signed_in(&s.runtime, &exe, &real_env, false) {
                            // Only now, with the subscription working in place, does the key come off: marked first,
                            // then its copies deleted (both in `mark_key_off`, which puts every file back if one fails).
                            (true, detail) => match if to_subscription { runner.sign_in.mark_key_off(&s.runtime) } else { Ok(()) } {
                                Ok(()) => ("signed_in".into(), if detail.is_empty() { "Switched account".into() } else { detail }),
                                Err(failed) => {
                                    let keys = match &failed.undo {
                                        None => "so it stays in use".to_string(),
                                        Some(e) => format!("and its key files could not all be put back ({e}); add the key again"),
                                    };
                                    match restore(&moved, &real_env) {
                                        Ok(()) => ("failed".into(), format!("Could not take the API key off on this computer ({}), {keys}", failed.error)),
                                        Err(e) => {
                                            keep_side = true;
                                            ("failed".into(), format!("Could not take the API key off ({}), {keys}, and the old login could not be put back ({e}); its files are in {}", failed.error, side.display()))
                                        }
                                    }
                                }
                            },
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

/// Run the CLI's own logout. Its exit status is not the verdict (an already signed-out CLI may say so with an error):
/// the check after it is.
fn log_out(exe: &str, logout: &[String], env: &Env) {
    let args: Vec<&str> = logout.iter().map(String::as_str).collect();
    let ran = model_command(exe, &args, env).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn();
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
    log_out(&exe, &logout, &env);
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

/// The CLI and environment the computer's own key login runs with (signin.rs key_login), for a clear with no job.
pub fn key_cli(runtime: &str) -> Option<(String, Env)> {
    let exe = crate::env::which(&crate::harness_tools::executable_for(runtime), &std::env::var("PATH").unwrap_or_default())?;
    let mut env: Env = std::env::vars().collect();
    env.extend(crate::harness_tools::env_for(runtime));
    Some((exe.display().to_string(), env))
}

/// Whether a sign-in check says the login is one an API key made, not a browser or subscription login ("Logged in using
/// an API key"; Claude Code's status names its method `api_key`).
fn by_key(detail: &str) -> bool {
    detail.to_lowercase().replace('_', " ").contains("api key")
}

/// Clear `runtime`'s key on this computer, for a clear_key job or a clear made in the app while this computer was
/// away: marked signed out (the company's copy goes), its names taken out of the computer's own key file and
/// `_shared.env`, the key logins already running let end, and a login the key became logged out with the CLI (`cli`:
/// its executable and environment). A browser or subscription login is not the key's and stays.
pub fn clear_key(sign: &crate::signin::SignIn, runtime: &str, cli: Option<(&str, &Env)>) -> Result<(), String> {
    sign.forget_keys(runtime).map_err(|e| format!("Could not clear the key on this computer ({:?})", e.kind()))?;
    if !sign.wait_for_key_logins(runtime, crate::signin::KEY_LOGIN_DRAIN) {
        return Err(format!("{runtime} is still signing in with a key on this computer; try again in a minute"));
    }
    let logout = crate::harness_tools::login_command(runtime).map(|l| l.logout).unwrap_or_default();
    if crate::harness_tools::caps(runtime, "").api_key_login.is_empty() || logout.is_empty() {
        return Ok(());
    }
    let Some((exe, env)) = cli else { return Ok(()) };
    match signed_in(runtime, exe, env, false) {
        (true, detail) if by_key(&detail) => log_out(exe, &logout, env),
        _ => return Ok(()),
    }
    if signed_in(runtime, exe, env, false).0 {
        return Err(format!("The key was removed but {runtime} is still signed in"));
    }
    Ok(())
}

/// A clear_key job: the computer's own key for `runtime` is gone, and no key on this computer signs it in again
/// until a person adds one or signs in.
fn run_clear_key(s: Arc<Session>, runner: Arc<Runner>, config: Config) {
    let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
    let cli = login_env(&s.runtime, &config, "").ok();
    match clear_key(&runner.sign_in, &s.runtime, cli.as_ref().map(|(exe, env, _)| (exe.as_str(), env))) {
        Ok(()) => {
            let account = cli.and_then(|(_, _, p)| p).map(|p| p.name).unwrap_or_default();
            crate::accounts::forget(&account, &s.runtime);
            s.finish("signed_out", "Key cleared", &runner, &default);
        }
        Err(e) => s.finish("failed", &e, &runner, &default),
    }
}

/// The manifest's `api_key_login` (Codex: `login --with-api-key`) run in `env` with `key` on stdin, never in
/// arguments or logs; whether it exited cleanly in time.
fn login_with_key(exe: &str, args: &[String], key: &str, env: &Env) -> bool {
    use std::io::Write;
    let argv: Vec<&str> = args.iter().map(String::as_str).collect();
    let Ok(mut child) = model_command(exe, &argv, env).stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::null()).spawn() else {
        return false;
    };
    if let Some(mut stdin) = child.stdin.take() {
        let _ = writeln!(stdin, "{key}");
    }
    let until = Instant::now() + Duration::from_secs(30);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) if Instant::now() < until => std::thread::sleep(Duration::from_millis(50)),
            _ => {
                kill(&mut child);
                return false;
            }
        }
    }
}

/// The manifest's `api_key_check` run with only the new key (`new`) for a sign-in, in an empty folder inside the side
/// home `side` as its home and working folder (no settings or login of the subscription's in it): whether it exited
/// cleanly in time and its JSON reply, if any, is not an error.
fn key_works(exe: &str, caps: &crate::harness_tools::Capabilities, names: &[String], new: &Env, env: &Env, side: &std::path::Path) -> bool {
    let dir = side.join(".tico-check");
    if file_command(&["mkdir", "-m", "700", &dir.display().to_string()], env).is_err() {
        return false;
    }
    let dir = dir.as_path();
    let mut env = env.clone();
    for name in names.iter().chain(&caps.profile_home_overrides) {
        env.remove(name);
    }
    env.extend(new.iter().map(|(k, v)| (k.clone(), v.clone())));
    env.insert(caps.profile_home.clone(), dir.display().to_string());
    let args: Vec<&str> = caps.api_key_check.iter().map(String::as_str).collect();
    let Ok(mut child) = model_command(exe, &args, &env).current_dir(dir).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn() else {
        return false;
    };
    let until = Instant::now() + Duration::from_secs(90);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if Instant::now() < until => std::thread::sleep(Duration::from_millis(50)),
            _ => {
                kill(&mut child);
                return false;
            }
        }
    }
    let Ok(out) = child.wait_with_output() else { return false };
    let said = String::from_utf8_lossy(&out.stdout);
    let error = said.lines().chain([said.trim()]).filter_map(|l| serde_json::from_str::<Value>(l).ok()).any(|v| v["is_error"] == true);
    out.status.success() && !error
}

/// A use_key job: the key a person just added in the app for this computer takes the place of its own key for
/// `runtime`. Fetched for this job by its id, never sent in the queue. A CLI that turns the key into a login of its own
/// signs in with it on the side, as a switch does; until it has and Tico agrees it goes in (`finishing`), nothing on
/// this computer changes, so a key that does not work, a cancel or a check that runs out leaves the old key and login
/// as they were. The regular key fetch and key login wait meanwhile. Once in, the old key from the terminal goes.
fn run_use_key(s: Arc<Session>, runner: Arc<Runner>, config: Config, handle: tokio::runtime::Handle) {
    let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
    let sign = &runner.sign_in;
    let finish = |state: &str, message: &str| s.finish(state, message, &runner, &default);
    let to_key = s.kind == "to_key";
    let left = || if to_key { "the subscription is still in use" } else if sign.headless_secret(&s.runtime).is_some() { "the old key is still in use" } else { "still no key" };
    if to_key && crate::harness_tools::login_command(&s.runtime).is_some_and(|l| l.keychain.iter().any(|p| p == crate::runner::platform())) {
        // The subscription's login is in the system keychain here, not in files: a logout that half worked could not
        // be put back.
        let msg = format!("Switching to an API key isn't available for {} on this computer: sign out, then add a key", s.runtime);
        return finish("failed", &msg);
    }
    let Some(change) = sign.begin_key_change(&s.runtime, crate::signin::KEY_LOGIN_DRAIN) else {
        return finish("failed", &format!("{} key is already changing on this computer; try again in a minute", s.runtime));
    };
    if !sign.wait_for_key_logins(&s.runtime, crate::signin::KEY_LOGIN_DRAIN) {
        return finish("failed", &format!("{} is still signing in with a key on this computer; try again in a minute", s.runtime));
    }
    let names = crate::signin::headless_names(&s.runtime);
    let query = [("runtime", s.runtime.clone()), ("login", s.id.clone())];
    let reply = handle.block_on(runner.client.get_query("runner-model-credentials", &query)).unwrap_or_default();
    let mut new = Env::new();
    for row in reply["credentials"].as_array().cloned().unwrap_or_default() {
        let (k, v) = (text_of(&row["env"]), text_of(&row["value"]).trim().to_string());
        if row["source"] == "computer" && names.contains(&k) && !v.is_empty() && !v.contains(['\r', '\n', '\0']) {
            new.insert(k, v);
        }
    }
    let Some(key) = new.values().next().cloned() else {
        return finish("failed", "The new key was not found; add it again");
    };
    let caps = crate::harness_tools::caps(&s.runtime, "");
    let files = crate::harness_tools::login_command(&s.runtime).map(|l| l.files).unwrap_or_default();
    // (executable, the login's environment, its home, the side home it signed in on with the new key)
    let mut side = None;
    if !caps.api_key_login.is_empty() {
        let (exe, env, _) = match login_env(&s.runtime, &config, "") {
            Ok(found) => found,
            Err(e) => return finish("failed", &format!("{e}; {}", left())),
        };
        let (home, dir) = match begin_switch(&s.runtime, &s.id, &env) {
            Ok(found) => found,
            Err(e) => return finish("failed", &format!("{e}; {}", left())),
        };
        let mut on_side = env.clone();
        on_side.insert(caps.profile_home.clone(), dir.display().to_string());
        let works = login_with_key(&exe, &caps.api_key_login, &key, &on_side) && matches!(signed_in(&s.runtime, &exe, &on_side, false), (true, d) if by_key(&d));
        side = Some((exe, env, home, dir));
        if !works {
            discard_side(&s.runtime, side.as_ref());
            return finish("failed", &format!("The new key did not sign {} in; {}", s.runtime, left()));
        }
    } else if to_key {
        // A CLI that reads its key from the environment: the provider is asked about the key in a side home with no
        // login, so the subscription cannot answer for it. The side home then keeps the subscription's login files
        // while it is signed out (to_key_in_place).
        if caps.api_key_check.is_empty() {
            return finish("failed", &format!("{} cannot check a key from here; {}", s.runtime, left()));
        }
        let (exe, env, _) = match login_env(&s.runtime, &config, "") {
            Ok(found) => found,
            Err(e) => return finish("failed", &format!("{e}; {}", left())),
        };
        let (home, dir) = match begin_switch(&s.runtime, &s.id, &env) {
            Ok(found) => found,
            Err(e) => return finish("failed", &format!("{e}; {}", left())),
        };
        let works = key_works(&exe, &caps, &names, &new, &env, &dir);
        side = Some((exe, env, home, dir));
        if !works {
            discard_side(&s.runtime, side.as_ref());
            return finish("failed", &format!("The new key did not work for {}; {}", s.runtime, left()));
        }
    }
    if let Err((state, message)) = confirm_commit(&s, "Tico did not confirm the new key in time") {
        discard_side(&s.runtime, side.as_ref());
        return finish(&state, &if state == "cancelled" { message } else { format!("{message}; {}", left()) });
    }
    if to_key {
        let (state, message) = to_key_in_place(&s, sign, &config, &new, side.as_ref(), &files);
        drop(change);
        return finish(&state, &message);
    }
    // Past the commit point: Tico holds the new key as this computer's.
    let mut moved_in = true;
    if let Some((exe, env, home, dir)) = &side {
        moved_in = match adopt_files(&files, home, dir, env) {
            Ok(_) if signed_in(&s.runtime, exe, env, false).0 => true,
            Ok(moved) => {
                if let Err(e) = restore(&moved, env) {
                    log(&format!("Tico runner: {} old login could not be put back: {e}", s.runtime));
                }
                false
            }
            Err(e) => {
                log(&format!("Tico runner: {} new key login could not be put in place: {e}", s.runtime));
                false
            }
        };
    }
    discard_side(&s.runtime, side.as_ref());
    let saved = sign.set_computer_keys(&s.runtime, &new).and_then(|_| sign.unmark_signed_out(&s.runtime)).and_then(|_| sign.unmark_key_off(&s.runtime));
    if let Err(e) = sign.forget_hand_keys(&s.runtime) {
        log(&format!("Tico runner: {} old key could not be deleted from _shared.env ({:?})", s.runtime, e.kind()));
    }
    drop(change);
    if let Err(e) = saved {
        sign.refresh_now(&s.runtime);
        return finish("failed", &format!("The new key could not be saved on this computer ({:?}); it is fetched again at its next check", e.kind()));
    }
    if let (false, Some((exe, ..))) = (moved_in, &side) {
        // The login could not be moved into place: made from the saved key the way the regular key login makes one.
        sign.retry_key_login(&s.runtime);
        if !handle.block_on(sign.key_login(&s.runtime, std::path::Path::new(exe))) {
            return finish("failed", &format!("The new key is saved but did not sign {} in here", s.runtime));
        }
    }
    finish("done", "The new key is in use");
}

/// Switch to API key, past its commit point (Tico can no longer cancel it, and does not hold the key as this computer's
/// until it hears `done`). The key's login checked on the side goes in, the key is saved as this computer's with the
/// marks that kept keys off lifted, and then the subscription is signed out, unless the login now reads as the API key:
/// a CLI that keeps both in one login file (Codex's auth.json) has had the subscription replaced by the key's login
/// already, and its logout would delete the new key's login. A CLI that keeps them apart (Claude Code: its login files
/// or the keychain, the key in this computer's key file) logs the subscription out, and the key stays. A step that fails
/// puts back everything changed before it, the login files and this runtime's keys and marks, so the subscription stays
/// in use. Runs under the caller's key change, so the regular fetch and key login keep out meanwhile.
fn to_key_in_place(s: &Session, sign: &crate::signin::SignIn, config: &Config, new: &Env, side: Option<&(String, Env, std::path::PathBuf, std::path::PathBuf)>, files: &[String]) -> (String, String) {
    let failed = |why: String| ("failed".to_string(), why);
    let before = match sign.keys_before(&s.runtime) {
        Ok(found) => found,
        Err(e) => {
            discard_side(&s.runtime, side);
            return failed(format!("Could not read this computer's keys ({:?}); the subscription is still in use", e.kind()));
        }
    };
    let Some((exe, env, home, dir)) = side else {
        return failed("The new key was not checked; the subscription is still in use".into());
    };
    let (exe, env) = (exe.clone(), env.clone());
    let profile = login_env(&s.runtime, config, "").ok().and_then(|(_, _, p)| p);
    // A CLI that turns the key into a login of its own (Codex) puts it in place of the subscription's; one that reads
    // the key from the environment (Claude Code) keeps its subscription's login files aside until it is signed out.
    let adopt = !crate::harness_tools::caps(&s.runtime, "").api_key_login.is_empty();
    // What `undo` puts back: the files the key's login replaced, or the subscription's files kept aside.
    let moved = std::cell::RefCell::new(vec![]);
    if adopt {
        match adopt_files(files, home, dir, &env) {
            Ok(m) => *moved.borrow_mut() = m,
            Err(e) => {
                if !e.contains("could not be put back") {
                    discard_side(&s.runtime, side);
                }
                return failed(e.replace("The new account", "The new key's login").replace("the old one", "the subscription"));
            }
        }
    }
    // Every step after the files moved is undone the same way: login files first, then keys and marks.
    let undo = |why: String| -> (String, String) {
        let files_back = restore(&moved.borrow(), &env);
        let keys_back = sign.put_keys_back(&before);
        if files_back.is_ok() {
            discard_side(&s.runtime, side);
        }
        match (files_back, keys_back) {
            (Ok(()), Ok(())) => failed(format!("{why}, so the subscription is still in use")),
            (Err(e), _) => failed(format!("{why}, and the subscription's login could not be put back ({e}); its files are in {}",
                dir.display())),
            (Ok(()), Err(e)) => failed(format!("{why}; the subscription is back, but this computer's keys could not all be put back ({:?})", e.kind())),
        }
    };
    let saved = sign.set_computer_keys(&s.runtime, new)
        .and_then(|_| sign.unmark_signed_out(&s.runtime))
        .and_then(|_| sign.unmark_key_off(&s.runtime))
        .and_then(|_| sign.forget_hand_keys(&s.runtime));
    if let Err(e) = saved {
        return undo(format!("The new key could not be saved on this computer ({:?})", e.kind()));
    }
    let logout = crate::harness_tools::login_command(&s.runtime).map(|l| l.logout).unwrap_or_default();
    // A status check that gives no answer (it could not run, timed out or said something unreadable) is not "signed
    // out": the switch is undone rather than taken on trust.
    let status = || sign_in_status(&s.runtime, &exe, &env);
    match status() {
        // One login file: the key's login is the login now, and the subscription's went with the old file.
        Some((true, detail)) if by_key(&detail) => {}
        _ if adopt => return undo(format!("The new key's login did not work in place on {}", s.runtime)),
        None => return undo(format!("Could not check whether {} is signed in to its subscription", s.runtime)),
        Some((true, _)) if logout.is_empty() => return undo(format!("{} cannot sign its subscription out from here", s.runtime)),
        Some((true, _)) => {
            // Everything the logout may change is kept aside first, so a logout that half worked is put back whole.
            match keep_aside(files, home, dir, &env) {
                Ok(kept) => *moved.borrow_mut() = kept,
                Err(e) => return undo(format!("The subscription's login could not be kept aside ({e})")),
            }
            log_out(&exe, &logout, &env);
            match status() {
                Some((true, d)) if !by_key(&d) => return undo(format!("The subscription could not be signed out on {}", s.runtime)),
                None => return undo(format!("Could not check that {} signed its subscription out", s.runtime)),
                _ => {}
            }
        }
        // Nothing signed in apart from the key: nothing to sign out.
        Some((false, _)) => {}
    }
    discard_side(&s.runtime, side);
    crate::accounts::forget(&profile.map(|p| p.name).unwrap_or_default(), &s.runtime);
    ("done".into(), "Switched to the API key; the subscription is signed out".into())
}

/// Copy each of a login's `files` under `home` into the side home `dir` (`.tico-old`, as `adopt_files` keeps them), so
/// `restore` puts them back as they were; one that was not there is removed again.
fn keep_aside(files: &[String], home: &std::path::Path, dir: &std::path::Path, env: &Env) -> Result<Vec<Moved>, String> {
    let mut kept = vec![];
    for rel in files {
        let to = home.join(rel);
        // Only a file that is not there may be left out; a link, a folder or one that cannot be looked at stops
        // the switch before anything is signed out.
        match std::fs::symlink_metadata(&to) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                kept.push(Moved { to, kept: None });
                continue;
            }
            Ok(m) if m.is_file() => {}
            Ok(_) => return Err(format!("{rel} is not a plain file")),
            Err(e) => return Err(format!("{rel} could not be read ({})", e.kind())),
        }
        let copy = dir.join(".tico-old").join(rel);
        if let Some(parent) = copy.parent() {
            file_command(&["mkdir", "-p", "-m", "700", &parent.display().to_string()], env)?;
        }
        file_command(&["cp", "-p", &to.display().to_string(), &copy.display().to_string()], env)?;
        kept.push(Moved { to, kept: Some(copy) });
    }
    Ok(kept)
}

/// Delete the side home a use_key job signed in on, if it made one.
fn discard_side(runtime: &str, side: Option<&(String, Env, std::path::PathBuf, std::path::PathBuf)>) {
    if let Some((_, env, _, dir)) = side {
        if let Err(e) = file_command(&["rm", "-rf", &dir.display().to_string()], env) {
            log(&format!("Tico runner: {runtime} new key left {}: {e}", dir.display()));
        }
    }
}

/// Whether `runtime` is surely not signed in on `profile`: its home holds no file but what Tico put there when it
/// made the folder, or its status check says signed out. A check that could not run (the CLI is gone, it timed
/// out) is no answer, so its login is taken to be there.
fn surely_signed_out(runtime: &str, config: &Config, profile: &crate::profiles::Profile, env: &Env) -> bool {
    if profile.home(runtime).is_some_and(|home| login_files(&home, env) == Some(false)) {
        return true;
    }
    login_env(runtime, config, &profile.name).ok()
        .and_then(|(exe, env, _)| sign_in_status(runtime, &exe, &env))
        .is_some_and(|(on, _)| !on)
}

/// Whether `home` holds any file other than the .gitconfig Tico copies in, looked at as the CLI's user (under
/// isolation the home is that user's). None when it could not be looked at.
fn login_files(home: &std::path::Path, env: &Env) -> Option<bool> {
    if !home.exists() {
        return Some(false);
    }
    let (dir, gitconfig) = (home.display().to_string(), home.join(".gitconfig").display().to_string());
    let out = model_command("find", &[&dir, "!", "-type", "d", "!", "-path", &gitconfig, "-print", "-quit"], env)
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).output().ok()?;
    out.status.success().then(|| !out.stdout.is_empty())
}

/// Remove a named subscription's login for `runtime`. Tico confirms first (no bot has come onto it since it was
/// asked for); then its CLI's own logout runs in the subscription's home and is checked; then, once every other
/// tool is surely signed out of that subscription, the subscription leaves runner.json and its folder is deleted.
/// A logout that did not take leaves everything as it was.
fn run_remove(s: Arc<Session>, runner: Arc<Runner>, config: Config) {
    let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
    let fail = |msg: &str| s.finish("failed", msg, &runner, &default);
    if s.profile == default {
        return fail(&format!("Subscription {} is this computer's own login; sign it out instead", s.profile));
    }
    let (exe, env, profile) = match login_env(&s.runtime, &config, &s.profile) {
        Ok((exe, env, Some(profile))) => (exe, env, profile),
        Ok(_) => return fail(&format!("Subscription {} is not on this computer", s.profile)),
        Err(e) => return fail(&e),
    };
    if let Err((state, msg)) = confirm_commit(&s, "Tico did not confirm the removal in time, so nothing was removed") {
        return s.finish(&state, &msg, &runner, &default);
    }
    if sign_in_status(&s.runtime, &exe, &env).is_none_or(|(on, _)| on) {
        let logout = crate::harness_tools::login_command(&s.runtime).map(|l| l.logout).unwrap_or_default();
        if logout.is_empty() {
            return fail(&format!("Removing a subscription from the app is not available for {}", s.runtime));
        }
        let args: Vec<&str> = logout.iter().map(String::as_str).collect();
        if let Ok(mut child) = model_command(&exe, &args, &env).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn() {
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
            return fail(&format!("The logout finished but {} is still signed in on {}", s.runtime, s.profile));
        }
    }
    crate::accounts::forget(&profile.name, &s.runtime);
    crate::profiles::forget_windows(&profile.name, &s.runtime);
    // Another tool that may still be signed in on it keeps the subscription; only this tool's login went.
    let stays: Vec<String> = crate::harness_tools::profile_runtimes().into_iter()
        .filter(|r| *r != s.runtime && !surely_signed_out(r, &config, &profile, &env))
        .map(|r| crate::harness_tools::name_for(&r)).collect();
    if !stays.is_empty() {
        let msg = format!("{} signed out of {}; it stays on this computer for {}", s.runtime, s.profile, stays.join(" and "));
        return s.finish("done", &msg, &runner, &default);
    }
    // Under runner isolation each tool's home in the folder is the bot user's: empty them as that user first, so the
    // supervisor can then delete the folder. Only a folder Tico made, never one a person pointed it at.
    if crate::profiles::tico_made(&config, &profile) {
        for runtime in crate::harness_tools::profile_runtimes() {
            if let Some(home) = profile.home(&runtime).filter(|h| h.is_dir()) {
                let home = home.display().to_string();
                let _ = file_command(&["find", &home, "-mindepth", "1", "-delete"], &env);
            }
        }
    }
    match crate::profiles::remove_profile(&config, &s.profile) {
        Ok(deleted) => {
            if !deleted {
                log(&format!("Tico runner: subscription {} unlisted; its folder is not Tico's to delete", s.profile));
            }
            runner.events.ask(&["heartbeat"]);
            s.finish("done", &format!("Removed {}", s.profile), &runner, &default)
        }
        Err(e) => fail(&e),
    }
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
        // A key job runs for a runtime with no browser sign-in too, and only on the computer's own login.
        let key_job = ["clear_key", "use_key", "to_key"].contains(&s.kind.as_str());
        if key_job && !s.profile.is_empty() {
            s.finish("failed", &format!("Subscription {} holds no key of this computer's; only its own login has one", s.profile), runner, &default);
        } else if key_job && crate::signin::headless_names(&s.runtime).is_empty() {
            s.finish("failed", &format!("This computer does not hold a key for {}", s.runtime), runner, &default);
        } else if key_job && self.sessions.values().any(|o| o.live() && o.runtime == s.runtime && o.profile == s.profile) {
            s.finish("failed", &format!("Another sign-in is already running for {}", s.runtime), runner, &default);
        } else if s.kind == "clear_key" {
            let (s2, r2) = (s.clone(), runner.clone());
            std::thread::spawn(move || run_clear_key(s2, r2, config));
        } else if s.kind == "use_key" || s.kind == "to_key" {
            match tokio::runtime::Handle::try_current() {
                Ok(handle) => {
                    let (s2, r2) = (s.clone(), runner.clone());
                    std::thread::spawn(move || run_use_key(s2, r2, config, handle));
                }
                Err(_) => s.finish("failed", "The runner could not fetch the new key", runner, &default),
            }
        } else if crate::harness_tools::login_command(&s.runtime).is_none() {
            s.finish("failed", &format!("Browser sign-in is not available for {}", s.runtime), runner, &default);
        } else if !["sign_in", "switch", "sign_out", "use_shared_key", "to_subscription", "remove"].contains(&s.kind.as_str()) {
            // Never a sign-in in place of something this runner does not know.
            s.finish("failed", &format!("Update this computer's runner to {}", s.kind.replace('_', " ")), runner, &default);
        } else if self.sessions.values().any(|o| o.live() && o.runtime == s.runtime && o.profile == s.profile) {
            s.finish("failed", &format!("Another sign-in is already running for {}", s.runtime), runner, &default);
        } else if !s.profile.is_empty() && self.sessions.values().any(|o| o.live() && o.profile == s.profile && (o.kind == "remove" || s.kind == "remove")) {
            // A removal may delete the subscription's whole folder: nothing else runs on that subscription meanwhile.
            s.finish("failed", &format!("Something else is running on {}; try again when it ends", s.profile), runner, &default);
        } else if named.as_ref().is_some_and(|p| p.share_operator) && !crate::harness_tools::caps(&s.runtime, "").own_config_home {
            // A host with its own config folder never uses the operator's login, so a shared profile still signs in to Tico's own.
            s.finish("failed", &format!("Profile {} shares the operator's own logins; sign in normally", s.profile), runner, &default);
        } else if s.kind == "to_subscription" && !s.profile.is_empty() {
            s.finish("failed", "Switch to subscription is for the computer's own login", runner, &default);
        } else if (s.kind == "switch" || s.kind == "to_subscription")
            && crate::harness_tools::login_command(&s.runtime).is_some_and(|l| l.keychain.iter().any(|p| p == crate::runner::platform()))
        {
            // The login is in the system keychain here, not in files: there is no side to sign in on.
            let msg = format!("Switching accounts isn't available for {} on this computer: sign out, then sign in", s.runtime);
            s.finish("failed", &msg, runner, &default);
        } else if s.kind == "use_shared_key" {
            let state = match runner.sign_in.unmark_signed_out(&s.runtime).and_then(|()| runner.sign_in.unmark_key_off(&s.runtime)) {
                Ok(()) => ("done", "The shared key signs it in again".to_string()),
                Err(e) => ("failed", format!("Could not change this computer's sign-out ({:?})", e.kind())),
            };
            s.finish(state.0, &state.1, runner, &default);
        } else if s.kind == "remove" && named.is_none() {
            s.finish("failed", &format!("Subscription {} is not on this computer", s.profile), runner, &default);
        } else if s.kind == "remove" {
            let (s2, r2) = (s.clone(), runner.clone());
            std::thread::spawn(move || run_remove(s2, r2, config));
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
                    // A removal Tico refused at its commit point (a bot runs on it now) stops before anything is removed.
                    "cancelled" | "expired" | "failed" if s.live() => s.cancelled.store(true, Ordering::SeqCst),
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

    #[test]
    fn a_profiles_claude_sign_in_drops_an_inherited_claude_config_dir() {
        const CHILD: &str = "TICO_TEST_LOGIN_CONFIG_DIR";
        let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
            // A process of its own: the runner's environment points Claude at a folder outside every profile.
            let root = std::env::temp_dir().join(format!("tico-login-config-dir-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir_all(root.join("bin")).unwrap();
            crate::util::write_script(&root.join("bin/claude"), crate::profiles::tests::FAKE_CLAUDE_LOGIN);
            let out = Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "login::tests::a_profiles_claude_sign_in_drops_an_inherited_claude_config_dir", "--nocapture"])
                .env(CHILD, &root).env("PATH", format!("{}:/usr/bin:/bin", root.join("bin").display())).env("HOME", &root)
                .env("CLAUDE_CONFIG_DIR", root.join("outside"))
                .env_remove(crate::isolation::UID_ENV).env_remove(crate::isolation::GID_ENV).output().unwrap();
            std::fs::remove_dir_all(&root).ok();
            let said = String::from_utf8_lossy(&out.stdout);
            assert!(out.status.success() && said.contains("1 passed"), "{said}{}", String::from_utf8_lossy(&out.stderr));
            return;
        };
        let outside = root.join("outside");
        std::fs::create_dir_all(&outside).unwrap();
        std::fs::create_dir_all(root.join("projects")).unwrap();
        std::fs::write(outside.join("sentinel"), "x").unwrap();
        let path = root.join("runner.json");
        std::fs::write(&path, json!({"url": "http://127.0.0.1:9", "token": "t", "runner_id": "r1", "projects_dir": root.join("projects"),
                                      "profiles": {"acme": {"dir": root.join("acme")}}, "default_profile": "acme"}).to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        // Named (Settings > Computers on a profile) and the default profile alike.
        for name in ["acme", ""] {
            let (exe, env, profile) = login_env("claude", &config, name).unwrap();
            assert_eq!(profile.unwrap().name, "acme");
            assert_eq!(env["HOME"], root.join("acme/claude").display().to_string());
            assert!(!env.contains_key("CLAUDE_CONFIG_DIR"), "{env:?}");
            // The sign-in runs with exactly this environment (spawn: model_command), so it writes in the profile only.
            assert!(model_command(&exe, &["auth", "login", "--claudeai"], &env).status().unwrap().success());
            assert!(root.join("acme/claude/.claude/.credentials.json").is_file());
            std::fs::remove_dir_all(root.join("acme")).unwrap();
        }
        let left: Vec<_> = std::fs::read_dir(&outside).unwrap().flatten().map(|e| e.file_name()).collect();
        assert_eq!(left, vec![std::ffi::OsString::from("sentinel")]);
    }

    /// A stand-in `codex`, each run recorded in $HOME/runs: `login status` reads $CODEX_HOME/auth.json (on a switch's
    /// side home it first waits for $HOME/release while $HOME/hold-probe is there; in the real home it says signed
    /// out while $HOME/refuse is there), `logout` removes it (unless $HOME/stuck), `login --device-auth` shows a
    /// code and writes a new login (or hangs while $HOME/slow is there), and `login --with-api-key` writes a key login
    /// (a key with "bad" in it is refused, and the old login is gone; while $HOME/hold-key is there it first marks
    /// $HOME/key-started and waits for $HOME/key-release).
    const FAKE_CODEX: &str = r#"#!/bin/sh
home="${CODEX_HOME:-$HOME/.codex}"
echo "$@" >> "$HOME/runs"
case "$1 $2" in
  "login status")
    case "$home" in
      *tico-switch-*) if [ -f "$HOME/hold-probe" ]; then : > "$HOME/probing"; n=0; while [ ! -f "$HOME/release" ] && [ $n -lt 200 ]; do sleep 0.05; n=$((n+1)); done; fi ;;
      *) if [ -f "$HOME/refuse" ]; then echo "Not signed in"; exit 1; fi ;;
    esac
    if grep -q '^apikey' "$home/auth.json" 2>/dev/null; then echo "Logged in using an API key - sk-***"; exit 0; fi
    if [ -f "$home/auth.json" ]; then echo "Logged in using ChatGPT"; exit 0; fi; echo "Not signed in"; exit 1 ;;
  "logout ") [ -f "$HOME/stuck" ] || rm -f "$home/auth.json"; exit 0 ;;
  "login --with-api-key")
    read key
    if [ -f "$HOME/hold-key" ]; then : > "$HOME/key-started"; n=0; while [ ! -f "$HOME/key-release" ] && [ $n -lt 200 ]; do sleep 0.05; n=$((n+1)); done; fi
    case "$key" in *bad*) rm -f "$home/auth.json"; exit 1 ;; esac
    mkdir -p "$home"; echo "apikey $key" > "$home/auth.json"; exit 0 ;;
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
        switch_kind(runner, config, server, "switch")
    }

    fn switch_kind(runner: &Arc<Runner>, config: &Config, server: Option<bool>, kind: &str) -> (Arc<Session>, std::thread::JoinHandle<()>) {
        let s = session(kind);
        let (s2, r2, c2) = (s.clone(), runner.clone(), config.clone());
        let running = std::thread::spawn(move || run_session(s2, r2, c2, Duration::from_secs(20)));
        if let Some(confirm) = server {
            answer_finishing(&s, confirm);
        }
        (s, running)
    }

    /// Stand in for Tico's answer once the runner says `s` is finishing (true: confirmed; false: it had been cancelled).
    fn answer_finishing(s: &Arc<Session>, confirm: bool) {
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

    #[test]
    fn a_switch_to_a_subscription_waits_for_a_key_login_already_running_and_a_fetch_already_out() {
        const CHILD: &str = "TICO_TEST_SWITCH_DRAIN";
        let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
            // A process of its own: PATH, HOME and CODEX_HOME point at a stand-in CLI and a scratch home.
            let root = std::env::temp_dir().join(format!("tico-switch-drain-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir_all(root.join("bin")).unwrap();
            std::fs::write(root.join("bin/codex"), FAKE_CODEX).unwrap();
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(root.join("bin/codex"), std::fs::Permissions::from_mode(0o755)).unwrap();
            let path = format!("{}:/usr/bin:/bin", root.join("bin").display());
            let out = Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "login::tests::a_switch_to_a_subscription_waits_for_a_key_login_already_running_and_a_fetch_already_out", "--nocapture"])
                .env(CHILD, &root).env("PATH", path).env("HOME", &root).env("CODEX_HOME", root.join("codex"))
                .env_remove(crate::isolation::UID_ENV).env_remove(crate::isolation::GID_ENV)
                .env_remove("OPENAI_API_KEY").output().unwrap();
            std::fs::remove_dir_all(&root).ok();
            let said = String::from_utf8_lossy(&out.stdout);
            assert!(out.status.success() && said.contains("1 passed"), "{said}{}", String::from_utf8_lossy(&out.stderr));
            return;
        };
        // Tico answers the regular key fetch only once let go, so it is still out when the switch begins.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let (asked_tx, asked_rx) = std::sync::mpsc::channel::<()>();
        let (go_tx, go_rx) = std::sync::mpsc::channel::<()>();
        std::thread::spawn(move || {
            use std::io::{Read, Write};
            let (mut sock, _) = listener.accept().unwrap();
            let _ = sock.read(&mut [0u8; 8192]);
            let _ = asked_tx.send(());
            let _ = go_rx.recv();
            let body = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-stale-0123456789","source":"shared"}]}"#;
            let reply = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
            let _ = sock.write_all(reply.as_bytes());
        });
        std::fs::create_dir_all(root.join("projects/secrets")).unwrap();
        std::fs::create_dir_all(root.join("codex")).unwrap();
        let path = root.join("runner.json");
        std::fs::write(&path, json!({"url": format!("http://127.0.0.1:{port}"), "token": "t", "runner_id": "r1", "projects_dir": root.join("projects")}).to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        let client = crate::client::Client::new(&config.url(), &config.token(), 30, 0).unwrap();
        let runner = Arc::new(Runner::new(config.clone(), client, crate::state::State::open(&root.join("state")).unwrap()));
        let auth = root.join("codex/auth.json");
        let team = root.join("projects/secrets").join(crate::signin::TEAM_KEYS_FILE);
        std::fs::write(&auth, "apikey sk-team-0123456789\n").unwrap();
        std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        let rt = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().unwrap();
        let r = runner.clone();
        let fetch = rt.spawn(async move { r.sign_in.team_model_keys(&r.client, "codex").await });
        asked_rx.recv_timeout(Duration::from_secs(10)).unwrap();
        // A key login already running, which writes the key's login only once let go.
        std::fs::write(root.join("hold-key"), "").unwrap();
        let (r, exe) = (runner.clone(), root.join("bin/codex"));
        let login = rt.spawn(async move { r.sign_in.key_login("codex", &exe).await });
        wait_for(|| root.join("key-started").exists());

        // The switch, signed in on the side and confirmed, holds key changes and waits for that login to end: the
        // subscription is not in place yet, and the fetch that comes back meanwhile saves nothing.
        let (switch, running) = switch_kind(&runner, &config, Some(true), "to_subscription");
        wait_for(|| switch.confirmed.load(Ordering::SeqCst));
        std::thread::sleep(Duration::from_millis(500));
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "apikey sk-team-0123456789\n", "the subscription went in while a key login was running");
        go_tx.send(()).unwrap();
        assert!(!rt.block_on(fetch).unwrap(), "a fetch older than the switch changed a key");
        assert_eq!(crate::signin::read_env(&team)["OPENAI_API_KEY"], "sk-team-0123456789");

        // The key login ends, writing the key's login; only then does the subscription go in, and it stays.
        std::fs::write(root.join("key-release"), "").unwrap();
        assert!(rt.block_on(login).unwrap(), "the key login finished");
        running.join().unwrap();
        assert_eq!(state(&switch).0, "signed_in", "{:?}", state(&switch));
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "new\n", "the key login wrote over the subscription");
        assert!(runner.sign_in.key_off("codex") && runner.sign_in.headless_secret("codex").is_none());
        assert!(!crate::signin::read_env(&team).contains_key("OPENAI_API_KEY"));
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

        // Switch to subscription (Computers part 2): the login runs on the company's key until the subscription is in
        // place. Cancelled before the commit point, or not working once in place, the key stays in use.
        let key_in_use = || runner.sign_in.headless_secret("codex").map(|(_, v)| v) == Some("sk-team-0123456789".into());
        std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        std::fs::write(&auth, "old\n").unwrap();
        let (kept, running) = switch_kind(&runner, &config, Some(false), "to_subscription");
        running.join().unwrap();
        assert_eq!(state(&kept).0, "cancelled", "{:?}", state(&kept));
        assert!(key_in_use() && !runner.sign_in.key_off("codex"));
        std::fs::write(root.join("refuse"), "").unwrap();
        let (broken, running) = switch_kind(&runner, &config, Some(true), "to_subscription");
        running.join().unwrap();
        assert_eq!(state(&broken).0, "failed", "{:?}", state(&broken));
        assert!(!runner.sign_in.key_off("codex"), "the key is the login again");
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");
        // Nothing was taken off before the subscription worked: the company's copy is still here, so the next turn
        // has the key at once, with no fetch in between.
        assert!(std::fs::read_to_string(&team).unwrap().contains("OPENAI_API_KEY=sk-team-0123456789"), "the company key was deleted by a switch that failed");
        assert!(key_in_use(), "the next turn would start with no key");
        std::fs::remove_file(root.join("refuse")).unwrap();
        // In place: the subscription is the login, the company's copy of the key is deleted and no key reaches it.
        std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        let (moved, running) = switch_kind(&runner, &config, Some(true), "to_subscription");
        running.join().unwrap();
        assert_eq!(state(&moved).0, "signed_in", "{:?}", state(&moved));
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "new\n");
        assert!(runner.sign_in.key_off("codex") && runner.sign_in.headless_secret("codex").is_none());
        assert!(!std::fs::read_to_string(&team).unwrap().contains("OPENAI_API_KEY"));
        assert_eq!(sides(), 0);
        // Use the shared key lifts it.
        let shared = session("use_shared_key");
        Logins::default().begin(&runner, &json!({"id": shared.id, "runtime": "codex", "kind": "use_shared_key"}));
        wait_for(|| !runner.sign_in.key_off("codex"));
        // In place and working, but a key file cannot be changed (the terminal's, after the company's and the
        // computer's own were rewritten): every key file goes back as it was, and the old login with it, so the next
        // turn has the computer's own key at once, with nothing to fetch.
        let secrets = root.join("projects/secrets");
        let (computer, hand) = (secrets.join(crate::signin::COMPUTER_KEYS_FILE), secrets.join(crate::signin::HAND_KEYS_FILE));
        let unwritable = secrets.join(format!("{}.tmp", crate::signin::HAND_KEYS_FILE));
        std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        std::fs::write(&computer, "OPENAI_API_KEY=sk-mine-0123456789\nOTHER=kept\n").unwrap();
        std::fs::write(&hand, "OPENAI_API_KEY=sk-hand-0123456789\n").unwrap();
        std::fs::create_dir(&unwritable).unwrap();
        let key_files = || [&team, &computer, &hand].map(|f| std::fs::read_to_string(f).unwrap());
        let before = key_files();
        std::fs::write(&auth, "old\n").unwrap();
        let (late, running) = switch_kind(&runner, &config, Some(true), "to_subscription");
        running.join().unwrap();
        assert_eq!(state(&late).0, "failed", "{:?}", state(&late));
        assert!(state(&late).1.ends_with("so it stays in use"), "{:?}", state(&late));
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");
        assert_eq!(key_files(), before, "a key file was left changed by a switch that failed");
        assert!(!runner.sign_in.key_off("codex"));
        assert_eq!(runner.sign_in.headless_secret("codex").map(|(_, v)| v), Some("sk-mine-0123456789".into()), "the next turn's key");
        assert_eq!(sides(), 0);
        std::fs::remove_dir(&unwritable).unwrap();
        for f in [&computer, &hand] {
            std::fs::remove_file(f).unwrap();
        }
        std::fs::write(&auth, "old\n").unwrap();

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

    /// A Tico that answers the model key fetch: `replies.0` to readiness's fetch, `replies.1` to a use_key job's (the
    /// request names the job). Each request line is kept.
    fn key_server(rt: &tokio::runtime::Runtime) -> (String, Arc<Mutex<(String, String)>>, Arc<Mutex<Vec<String>>>) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = rt.block_on(tokio::net::TcpListener::bind("127.0.0.1:0")).unwrap();
        let url = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
        let (replies, seen) = (Arc::new(Mutex::new((String::new(), String::new()))), Arc::new(Mutex::new(vec![])));
        let (answer, heard) = (replies.clone(), seen.clone());
        rt.spawn(async move {
            while let Ok((mut sock, _)) = listener.accept().await {
                let mut buf = vec![0u8; 8192];
                let n = sock.read(&mut buf).await.unwrap_or(0);
                let line = String::from_utf8_lossy(&buf[..n]).lines().next().unwrap_or_default().to_string();
                let body = { let r = answer.lock().unwrap(); if line.contains("login=") { r.1.clone() } else { r.0.clone() } };
                heard.lock().unwrap().push(line);
                let reply = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
                let _ = sock.write_all(reply.as_bytes()).await;
                let _ = sock.shutdown().await;
            }
        });
        (url, replies, seen)
    }

    #[test]
    fn clear_and_replace_the_computers_own_codex_key() {
        const CHILD: &str = "TICO_TEST_MODEL_KEYS";
        let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
            // A process of its own: PATH, HOME and CODEX_HOME point at a stand-in CLI and a scratch home.
            let root = std::env::temp_dir().join(format!("tico-model-keys-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir_all(root.join("bin")).unwrap();
            std::fs::write(root.join("bin/codex"), FAKE_CODEX).unwrap();
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(root.join("bin/codex"), std::fs::Permissions::from_mode(0o755)).unwrap();
            let path = format!("{}:/usr/bin:/bin", root.join("bin").display());
            let out = Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "login::tests::clear_and_replace_the_computers_own_codex_key", "--nocapture"])
                .env(CHILD, &root).env("PATH", path).env("HOME", &root).env("CODEX_HOME", root.join("codex"))
                .env_remove(crate::isolation::UID_ENV).env_remove(crate::isolation::GID_ENV)
                .env_remove("OPENAI_API_KEY").output().unwrap();
            std::fs::remove_dir_all(&root).ok();
            let said = String::from_utf8_lossy(&out.stdout);
            assert!(out.status.success() && said.contains("1 passed"), "{said}{}", String::from_utf8_lossy(&out.stderr));
            return;
        };
        use crate::signin::{read_env, COMPUTER_KEYS_FILE, HAND_KEYS_FILE, TEAM_KEYS_FILE};
        let rt = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().unwrap();
        let (url, replies, seen) = key_server(&rt);
        let fetches = || seen.lock().unwrap().iter().filter(|l| !l.contains("login=")).count();
        let secrets = root.join("projects/secrets");
        std::fs::create_dir_all(&secrets).unwrap();
        std::fs::create_dir_all(root.join("codex")).unwrap();
        let path = root.join("runner.json");
        std::fs::write(&path, json!({"url": url, "token": "t", "runner_id": "r1", "projects_dir": root.join("projects")}).to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        let client = crate::client::Client::new(&config.url(), &config.token(), 5, 0).unwrap();
        let runner = Arc::new(Runner::new(config.clone(), client, crate::state::State::open(&root.join("state")).unwrap()));
        let sign = &runner.sign_in;
        let (auth, runs) = (root.join("codex/auth.json"), root.join("runs"));
        let (computer, hand, team) = (secrets.join(COMPUTER_KEYS_FILE), secrets.join(HAND_KEYS_FILE), secrets.join(TEAM_KEYS_FILE));
        let key_logins = || std::fs::read_to_string(&runs).unwrap_or_default().lines().filter(|l| *l == "login --with-api-key").count();
        // A use_key job on its own thread, with Tico's answer once it is finishing (None: no answer).
        let start_use_key = |reply: &str, server: Option<bool>| {
            replies.lock().unwrap().1 = reply.into();
            let s = session("use_key");
            let (s2, r2, c2, h) = (s.clone(), runner.clone(), config.clone(), rt.handle().clone());
            let running = std::thread::spawn(move || run_use_key(s2, r2, c2, h));
            if let Some(confirm) = server {
                answer_finishing(&s, confirm);
            }
            (s, running)
        };
        let use_key = |reply: &str| {
            let (s, running) = start_use_key(reply, Some(true));
            running.join().unwrap();
            state(&s)
        };
        let next = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-next-0123456789","source":"computer"}]}"#;
        let (hold, probing, release) = (root.join("hold-probe"), root.join("probing"), root.join("release"));
        let no_side = || std::fs::read_dir(root.join("codex")).unwrap().flatten().all(|e| !e.file_name().to_string_lossy().starts_with(".tico-switch-"));

        // Clear: every copy of the key on this computer goes, and the login it made; the terminal's other lines stay.
        std::fs::write(&hand, "OPENAI_API_KEY=sk-hand-0123456789\nKEEP=me\n# a note\n").unwrap();
        std::fs::write(&computer, "OPENAI_API_KEY=sk-mine-0123456789\n").unwrap();
        std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        std::fs::write(&auth, "apikey sk-mine-0123456789\n").unwrap();
        let clear = session("clear_key");
        run_clear_key(clear.clone(), runner.clone(), config.clone());
        assert_eq!(state(&clear), ("signed_out".into(), "Key cleared".into()));
        assert!(!auth.exists(), "the login the key made is logged out");
        assert_eq!(std::fs::read_to_string(&hand).unwrap(), "KEEP=me\n# a note\n");
        assert!(!read_env(&computer).contains_key("OPENAI_API_KEY") && !read_env(&team).contains_key("OPENAI_API_KEY"));
        // Nothing signs it back in: not the next fetch, not a company key that reappears, not the key login, not a restart.
        replies.lock().unwrap().0 = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-team-0123456789","source":"shared"}]}"#.into();
        sign.refresh_now("codex");
        assert!(!rt.block_on(sign.team_model_keys(&runner.client, "codex")));
        assert_eq!(fetches(), 0, "the shared key was fetched after the key was cleared");
        std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        assert!(sign.headless_secret("codex").is_none());
        let before = key_logins();
        assert!(!rt.block_on(sign.key_login("codex", &root.join("bin/codex"))));
        assert_eq!(key_logins(), before, "a key login ran after the key was cleared");
        let restarted = crate::signin::SignIn::new(&root.join("projects"));
        assert!(restarted.signed_out("codex") && restarted.headless_secret("codex").is_none());
        std::fs::remove_file(&team).unwrap();

        // A browser login is not the key's: clearing the key leaves it signed in.
        sign.unmark_signed_out("codex").unwrap();
        std::fs::write(&computer, "OPENAI_API_KEY=sk-mine-0123456789\n").unwrap();
        std::fs::write(&auth, "old\n").unwrap();
        let clear = session("clear_key");
        run_clear_key(clear.clone(), runner.clone(), config.clone());
        assert_eq!(state(&clear).0, "signed_out");
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");
        assert!(sign.signed_out("codex") && !read_env(&computer).contains_key("OPENAI_API_KEY"));

        // Replace: the new key is fetched for this job, logged in with and checked; the old terminal key is deleted.
        // The login was switched to a subscription before: a key added now is the person choosing a key again, so the
        // next turn has it.
        sign.mark_key_off("codex").unwrap();
        std::fs::write(&hand, "OPENAI_API_KEY=sk-hand-0123456789\nKEEP=me\n").unwrap();
        std::fs::remove_file(&auth).unwrap();
        let done = use_key(r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-new-0123456789","source":"computer"}]}"#);
        assert_eq!(done, ("done".into(), "The new key is in use".into()));
        assert!(seen.lock().unwrap().iter().any(|l| l.contains("runtime=codex") && l.contains("login=l")), "{:?}", seen.lock().unwrap());
        assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-new-0123456789");
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "apikey sk-new-0123456789\n");
        assert_eq!(std::fs::read_to_string(&hand).unwrap(), "KEEP=me\n");
        assert!(!sign.signed_out("codex") && !sign.key_off("codex"));
        assert_eq!(sign.key_source("codex"), Some(("OPENAI_API_KEY".into(), "computer")));
        assert_eq!(sign.headless_secret("codex").map(|(_, v)| v), Some("sk-new-0123456789".into()), "the next turn's key");

        // While the next key is checked nothing here changes: the regular fetch and key login wait, runs and the CLI
        // keep the old key. A cancel that reaches Tico first leaves all of it, the terminal's key too.
        std::fs::write(&hand, "OPENAI_API_KEY=sk-hand-0123456789\n").unwrap();
        replies.lock().unwrap().0 = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-other-0123456789","source":"computer"}]}"#.into();
        std::fs::write(&hold, "").unwrap();
        let (cancelled, running) = start_use_key(next, Some(false));
        wait_for(|| probing.exists());
        let (fetched, logged) = (fetches(), key_logins());
        sign.refresh_now("codex");
        assert!(!rt.block_on(sign.team_model_keys(&runner.client, "codex")));
        assert!(!rt.block_on(sign.key_login("codex", &root.join("bin/codex"))));
        assert_eq!((fetches(), key_logins()), (fetched, logged), "the regular fetch or key login ran during a key change");
        assert_eq!(sign.headless_secret("codex").unwrap().1, "sk-new-0123456789");
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "apikey sk-new-0123456789\n");
        std::fs::write(&release, "").unwrap();
        running.join().unwrap();
        assert_eq!(state(&cancelled), ("cancelled".into(), "Cancelled".into()));
        assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-new-0123456789");
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "apikey sk-new-0123456789\n");
        assert_eq!(read_env(&hand)["OPENAI_API_KEY"], "sk-hand-0123456789");
        assert!(no_side());
        for f in [&probing, &release] {
            std::fs::remove_file(f).unwrap();
        }

        // Run out on Tico while it is checked (the next poll no longer lists it): the same.
        let (expired, running) = start_use_key(next, None);
        wait_for(|| probing.exists());
        expired.cancelled.store(true, Ordering::SeqCst);
        std::fs::write(&release, "").unwrap();
        running.join().unwrap();
        assert_eq!(state(&expired).0, "cancelled");
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "apikey sk-new-0123456789\n");
        assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-new-0123456789");
        assert!(no_side());
        for f in [&hold, &probing, &release] {
            std::fs::remove_file(f).unwrap();
        }

        // Tico agrees: the CLI login, the key file and what Tico now hands out are the same key, and the fetch after
        // it changes nothing.
        assert_eq!(use_key(next), ("done".into(), "The new key is in use".into()));
        assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-next-0123456789");
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "apikey sk-next-0123456789\n");
        assert!(!read_env(&hand).contains_key("OPENAI_API_KEY") && no_side());
        replies.lock().unwrap().0 = next.into();
        sign.refresh_now("codex");
        assert!(!rt.block_on(sign.team_model_keys(&runner.client, "codex")));
        assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-next-0123456789");

        // A new key the provider refuses: the old key and its login are back.
        let refused = use_key(r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-bad-0123456789","source":"computer"}]}"#);
        assert_eq!(refused, ("failed".into(), "The new key did not sign codex in; the old key is still in use".into()));
        assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-next-0123456789");
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "apikey sk-next-0123456789\n");
        assert!(!sign.signed_out("codex"));

        // From a cleared key, a refused one leaves it cleared and signed out.
        let clear = session("clear_key");
        run_clear_key(clear.clone(), runner.clone(), config.clone());
        assert_eq!(state(&clear).0, "signed_out");
        let refused = use_key(r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-bad-0123456789","source":"computer"}]}"#);
        assert_eq!(refused, ("failed".into(), "The new key did not sign codex in; still no key".into()));
        assert!(sign.signed_out("codex") && !read_env(&computer).contains_key("OPENAI_API_KEY"));
        assert!(sign.headless_secret("codex").is_none());
        // From a cleared key, a cancel leaves it cleared, signed out and with no login.
        let (cancelled, running) = start_use_key(next, Some(false));
        running.join().unwrap();
        assert_eq!(state(&cancelled).0, "cancelled");
        assert!(sign.signed_out("codex") && !read_env(&computer).contains_key("OPENAI_API_KEY") && !auth.exists() && no_side());

        // No key staged for the job, or a shared one: nothing changes.
        let missing = use_key(r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-team-0123456789","source":"shared"}]}"#);
        assert_eq!(missing, ("failed".into(), "The new key was not found; add it again".into()));
        assert!(sign.signed_out("codex"));

        // Only the computer's own login holds a key, and only for a runtime that takes one; nothing runs.
        let mut logins = Logins::default();
        logins.begin(&runner, &json!({"id": "lprofile", "runtime": "codex", "kind": "clear_key", "profile": "lab"}));
        logins.begin(&runner, &json!({"id": "lgemini", "runtime": "gemini", "kind": "use_key"}));
        assert!(crate::signin::headless_names("gemini").is_empty());
        assert_eq!(state(&logins.sessions["lprofile"]).0, "failed");
        assert_eq!(state(&logins.sessions["lgemini"]), ("failed".into(), "This computer does not hold a key for gemini".into()));

        // Its own key deleted in Credentials with no shared key behind it: Tico answers the next fetch as for Clear key,
        // and the login that key made stops working here.
        sign.unmark_signed_out("codex").unwrap();
        std::fs::write(&computer, "OPENAI_API_KEY=sk-mine-0123456789\n").unwrap();
        std::fs::write(&auth, "apikey sk-mine-0123456789\n").unwrap();
        assert_eq!(sign.key_source("codex"), Some(("OPENAI_API_KEY".into(), "computer")));
        replies.lock().unwrap().0 = r#"{"credentials":[],"signed_out":true,"clear_key":true}"#.into();
        sign.refresh_now("codex");
        assert!(rt.block_on(sign.team_model_keys(&runner.client, "codex")));
        assert!(!auth.exists() && sign.headless_secret("codex").is_none() && !read_env(&computer).contains_key("OPENAI_API_KEY"));
        let codex = root.join("bin/codex").display().to_string();
        assert!(!signed_in("codex", &codex, &key_cli("codex").unwrap().1, false).0, "the deleted key's login still works");

        // Cleared in the app while this computer was away: the next fetch clears it here, and none follows.
        sign.unmark_signed_out("codex").unwrap();
        std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        std::fs::write(&auth, "apikey sk-team-0123456789\n").unwrap();
        replies.lock().unwrap().0 = r#"{"credentials":[],"signed_out":true,"clear_key":true}"#.into();
        let fetched = fetches();
        assert!(rt.block_on(sign.team_model_keys(&runner.client, "codex")));
        assert_eq!(fetches(), fetched + 1);
        assert!(sign.signed_out("codex") && sign.headless_secret("codex").is_none());
        assert!(!read_env(&team).contains_key("OPENAI_API_KEY") && !auth.exists());
        sign.refresh_now("codex");
        assert!(!rt.block_on(sign.team_model_keys(&runner.client, "codex")));
        let before = key_logins();
        assert!(!rt.block_on(sign.key_login("codex", &root.join("bin/codex"))));
        assert_eq!((fetches(), key_logins()), (fetched + 1, before));
    }

    /// A stand-in `claude` (Claude Code), each run recorded in the test's own folder ($TICO_TEST_TO_KEY/runs). `-p`, the
    /// key check, answers only for the right key and only in a home with no subscription login. `auth status --json`
    /// reads the subscription's login file (and says nothing readable while `broken` is there); `auth logout` removes
    /// it, unless `stuck`, or only changes `.claude.json` (`partial`), or breaks the status check after (`break-after`).
    const FAKE_CLAUDE: &str = r#"#!/bin/sh
root="$TICO_TEST_TO_KEY"
echo "$@" >> "$root/runs"
creds="$HOME/.claude/.credentials.json"
if [ "$1" = "-p" ]; then
  if [ -f "$creds" ]; then echo '{"type":"result","is_error":true,"result":"the subscription answered"}'
  elif [ "$ANTHROPIC_API_KEY" = "sk-ant-key-0123456789" ]; then echo '{"type":"result","is_error":false,"result":"OK"}'
  else echo '{"type":"result","is_error":true,"result":"Invalid API key"}'; fi
  exit 0
fi
case "$1 $2" in
  "auth status") if [ -f "$root/broken" ]; then echo 'Something went wrong'; exit 1; fi
    if [ -f "$root/status" ]; then cat "$root/status"; exit 0; fi
    if [ -f "$creds" ]; then echo '{"loggedIn": true, "authMethod": "claude.ai"}'; else echo '{"loggedIn": false}'; fi; exit 0 ;;
  "auth logout") [ -f "$root/break-after" ] && touch "$root/broken"
    [ -f "$root/status-after" ] && cp "$root/status-after" "$root/status"
    if [ -f "$root/partial" ]; then echo changed > "$HOME/.claude.json"; exit 0; fi
    [ -f "$root/stuck" ] || rm -f "$creds"; exit 0 ;;
esac
exit 2
"#;

    #[test]
    fn switch_to_an_api_key_on_the_computers_own_codex_and_claude_logins() {
        const CHILD: &str = "TICO_TEST_TO_KEY";
        let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
            // A process of its own: PATH, HOME and CODEX_HOME point at stand-in CLIs and a scratch home.
            let root = std::env::temp_dir().join(format!("tico-to-key-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir_all(root.join("bin")).unwrap();
            crate::util::write_script(&root.join("bin/codex"), FAKE_CODEX);
            crate::util::write_script(&root.join("bin/claude"), FAKE_CLAUDE);
            let path = format!("{}:/usr/bin:/bin", root.join("bin").display());
            let out = Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "login::tests::switch_to_an_api_key_on_the_computers_own_codex_and_claude_logins", "--nocapture"])
                .env(CHILD, &root).env("PATH", path).env("HOME", &root).env("CODEX_HOME", root.join("codex"))
                .env_remove(crate::isolation::UID_ENV).env_remove(crate::isolation::GID_ENV).env_remove("CLAUDE_CONFIG_DIR")
                .env_remove("OPENAI_API_KEY").env_remove("ANTHROPIC_API_KEY").env_remove("CLAUDE_CODE_OAUTH_TOKEN").output().unwrap();
            std::fs::remove_dir_all(&root).ok();
            let said = String::from_utf8_lossy(&out.stdout);
            assert!(out.status.success() && said.contains("1 passed"), "{said}{}", String::from_utf8_lossy(&out.stderr));
            return;
        };
        use crate::signin::{read_env, COMPUTER_KEYS_FILE, HAND_KEYS_FILE};
        let rt = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().unwrap();
        let (url, replies, _) = key_server(&rt);
        let secrets = root.join("projects/secrets");
        std::fs::create_dir_all(&secrets).unwrap();
        std::fs::create_dir_all(root.join("codex")).unwrap();
        std::fs::create_dir_all(root.join(".claude")).unwrap();
        let path = root.join("runner.json");
        std::fs::write(&path, json!({"url": url, "token": "t", "runner_id": "r1", "projects_dir": root.join("projects")}).to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        let client = crate::client::Client::new(&config.url(), &config.token(), 5, 0).unwrap();
        let runner = Arc::new(Runner::new(config.clone(), client, crate::state::State::open(&root.join("state")).unwrap()));
        let sign = &runner.sign_in;
        let (auth, creds, runs) = (root.join("codex/auth.json"), root.join(".claude/.credentials.json"), root.join("runs"));
        let (computer, hand) = (secrets.join(COMPUTER_KEYS_FILE), secrets.join(HAND_KEYS_FILE));
        let logouts = || std::fs::read_to_string(&runs).unwrap_or_default().lines().filter(|l| *l == "logout" || *l == "auth logout").count();
        let no_side = || std::fs::read_dir(root.join("codex")).unwrap().flatten().all(|e| !e.file_name().to_string_lossy().starts_with(".tico-switch-"));
        // A to_key job on its own thread, the key Tico staged for it in `reply`, with Tico's answer once it is finishing.
        let to_key = |runtime: &str, reply: &str, server: Option<bool>| {
            replies.lock().unwrap().1 = reply.into();
            let s = Session::new(&json!({"id": format!("l{}", uuid::Uuid::new_v4().simple()), "runtime": runtime, "kind": "to_key"}));
            let (s2, r2, c2, h) = (s.clone(), runner.clone(), config.clone(), rt.handle().clone());
            let running = std::thread::spawn(move || run_use_key(s2, r2, c2, h));
            if let Some(confirm) = server {
                answer_finishing(&s, confirm);
            }
            running.join().unwrap();
            state(&s)
        };
        let codex_key = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-key-0123456789","source":"computer"}]}"#;

        // Codex on a ChatGPT subscription it was switched to from its key before (Computers part 2: marked key-off).
        std::fs::write(&auth, "chatgpt\n").unwrap();
        sign.mark_key_off("codex").unwrap();
        let subscription_in_use = || std::fs::read_to_string(&auth).unwrap() == "chatgpt\n" && sign.key_off("codex")
            && sign.headless_secret("codex").is_none() && !read_env(&computer).contains_key("OPENAI_API_KEY");
        // Cancelled before its commit point, or a key the provider refuses: the subscription stays signed in and in use.
        assert_eq!(to_key("codex", codex_key, Some(false)), ("cancelled".into(), "Cancelled".into()));
        assert!(subscription_in_use() && no_side());
        let bad = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-bad-0123456789","source":"computer"}]}"#;
        assert_eq!(to_key("codex", bad, Some(true)), ("failed".into(), "The new key did not sign codex in; the subscription is still in use".into()));
        assert!(subscription_in_use() && no_side());
        // Past the commit point a step fails (the terminal's key file cannot be rewritten, after this computer's key was
        // saved and the key-off mark lifted): everything it touched goes back, the subscription's login with it.
        std::fs::write(&hand, "OPENAI_API_KEY=sk-hand-0123456789\nKEEP=me\n").unwrap();
        let unwritable = secrets.join(format!("{HAND_KEYS_FILE}.tmp"));
        std::fs::create_dir(&unwritable).unwrap();
        let late = to_key("codex", codex_key, Some(true));
        assert_eq!(late.0, "failed", "{late:?}");
        assert!(late.1.ends_with("so the subscription is still in use"), "{late:?}");
        assert!(subscription_in_use(), "a failed switch left something changed");
        assert_eq!(std::fs::read_to_string(&hand).unwrap(), "OPENAI_API_KEY=sk-hand-0123456789\nKEEP=me\n");
        assert!(no_side());
        std::fs::remove_dir(&unwritable).unwrap();
        std::fs::remove_file(&hand).unwrap();
        // Works: Codex keeps both logins in one auth.json, so the key's login has replaced the subscription's and is not
        // logged out (that would delete the new key's login). The key-off mark is lifted: the next turn has the key.
        let before = logouts();
        assert_eq!(to_key("codex", codex_key, Some(true)), ("done".into(), "Switched to the API key; the subscription is signed out".into()));
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), "apikey sk-key-0123456789\n");
        assert_eq!(logouts(), before, "the key's login was logged out");
        assert!(!sign.key_off("codex") && !sign.signed_out("codex") && no_side());
        assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-key-0123456789");
        assert_eq!(sign.headless_secret("codex"), Some(("OPENAI_API_KEY".into(), "sk-key-0123456789".into())), "the next turn's key");

        // Claude Code on a subscription: its login and the key are kept apart, so the subscription is logged out and the
        // key stays in this computer's key file. Before anything changes the key is checked with Anthropic, in a home
        // with no login (the stand-in refuses to answer for the subscription).
        let (subscription, settings) = (r#"{"sub":1}"#, r#"{"settings":1}"#);
        std::fs::write(&creds, subscription).unwrap();
        std::fs::write(root.join(".claude.json"), settings).unwrap();
        sign.mark_key_off("claude").unwrap();
        let claude_key = r#"{"credentials":[{"env":"ANTHROPIC_API_KEY","value":"sk-ant-key-0123456789","source":"computer"}]}"#;
        let flag = |name: &str, on: bool| if on { std::fs::write(root.join(name), "").unwrap() } else { std::fs::remove_file(root.join(name)).unwrap() };
        let claude_side = || std::fs::read_dir(&root).unwrap().flatten().any(|e| e.file_name().to_string_lossy().starts_with(".tico-switch-"));
        let still_subscription = |why: &str| {
            assert_eq!(std::fs::read_to_string(&creds).ok().as_deref(), Some(subscription), "{why}: the subscription's login");
            assert_eq!(std::fs::read_to_string(root.join(".claude.json")).unwrap(), settings, "{why}: its settings file");
            assert!(sign.key_off("claude") && sign.headless_secret("claude").is_none(), "{why}: the key-off mark");
            assert!(!read_env(&computer).contains_key("ANTHROPIC_API_KEY"), "{why}: no key saved");
            assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-key-0123456789", "{why}: another runtime's key was touched");
            assert!(!claude_side(), "{why}: the side home was left");
        };
        assert_eq!(to_key("claude", claude_key, Some(false)).0, "cancelled");
        still_subscription("cancelled");
        // A key Anthropic refuses: failed before the commit point, nothing logged out.
        let before = logouts();
        let refused = r#"{"credentials":[{"env":"ANTHROPIC_API_KEY","value":"sk-ant-bad-0123456789","source":"computer"}]}"#;
        assert_eq!(to_key("claude", refused, None), ("failed".into(), "The new key did not work for claude; the subscription is still in use".into()));
        still_subscription("refused key");
        // A status check that says nothing readable is not "signed out": before the logout nothing is logged out ...
        flag("broken", true);
        let unknown = to_key("claude", claude_key, Some(true));
        assert_eq!(unknown, ("failed".into(), "Could not check whether claude is signed in to its subscription, so the subscription is still in use".into()));
        assert_eq!(logouts(), before, "logged out on a status check that said nothing");
        flag("broken", false);
        still_subscription("status check failed before the logout");
        // ... and after it the login the logout removed is put back.
        flag("break-after", true);
        let unchecked = to_key("claude", claude_key, Some(true));
        assert_eq!(unchecked, ("failed".into(), "Could not check that claude signed its subscription out, so the subscription is still in use".into()));
        flag("break-after", false);
        flag("broken", false);
        still_subscription("status check failed after the logout");
        // JSON that does not say true or false (an error report, a field missing, null or of another type) is no answer
        // either, before the logout ...
        let before = logouts();
        for said in [r#"{"type":"error","error":"Something went wrong"}"#, r#"{"loggedIn": null}"#, r#"{"loggedIn": "false"}"#] {
            std::fs::write(root.join("status"), said).unwrap();
            let unknown = to_key("claude", claude_key, Some(true));
            assert_eq!(unknown, ("failed".into(), "Could not check whether claude is signed in to its subscription, so the subscription is still in use".into()), "{said}");
            assert_eq!(logouts(), before, "logged out when the status check said {said}");
            flag("status", false);
            still_subscription(said);
        }
        // ... and after it.
        std::fs::write(root.join("status-after"), r#"{"loggedIn": null, "authMethod": "claude.ai"}"#).unwrap();
        let unchecked = to_key("claude", claude_key, Some(true));
        assert_eq!(unchecked, ("failed".into(), "Could not check that claude signed its subscription out, so the subscription is still in use".into()));
        flag("status-after", false);
        flag("status", false);
        still_subscription("status check said null after the logout");
        // A login file that is a link (or anything but a plain file) cannot be kept aside as it is: the switch stops
        // before the logout, and the link and the file it points at are left as they were.
        let (link, target) = (root.join(".claude.json"), root.join("settings-target.json"));
        std::fs::write(&target, settings).unwrap();
        std::fs::remove_file(&link).unwrap();
        std::os::unix::fs::symlink(&target, &link).unwrap();
        let before = logouts();
        let linked = to_key("claude", claude_key, Some(true));
        assert_eq!(linked, ("failed".into(), "The subscription's login could not be kept aside (.claude.json is not a plain file), so the subscription is still in use".into()));
        assert_eq!(logouts(), before, "logged out with a login file that could not be kept aside");
        assert_eq!(std::fs::read_link(&link).unwrap(), target, "the link was changed");
        assert_eq!(std::fs::read_to_string(&target).unwrap(), settings, "the file the link points at was changed");
        still_subscription("linked settings file");
        std::fs::remove_file(&link).unwrap();
        std::fs::rename(&target, &link).unwrap();
        // A logout that does not take, or takes half (changes the settings file, leaves the login): all put back.
        flag("stuck", true);
        let stuck = to_key("claude", claude_key, Some(true));
        assert_eq!(stuck, ("failed".into(), "The subscription could not be signed out on claude, so the subscription is still in use".into()));
        flag("stuck", false);
        still_subscription("stuck logout");
        flag("partial", true);
        assert_eq!(to_key("claude", claude_key, Some(true)).0, "failed");
        flag("partial", false);
        still_subscription("partial logout");
        let before = logouts();
        assert_eq!(to_key("claude", claude_key, Some(true)).0, "done");
        assert_eq!(logouts(), before + 1, "the subscription was not logged out");
        assert!(!creds.exists() && !sign.key_off("claude") && !claude_side());
        assert_eq!(sign.headless_secret("claude"), Some(("ANTHROPIC_API_KEY".into(), "sk-ant-key-0123456789".into())), "the next turn's key");
        assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-key-0123456789");
    }

    #[test]
    fn remove_waits_for_tico_runs_alone_on_the_subscription_and_keeps_a_login_it_cannot_check() {
        const CHILD: &str = "TICO_TEST_REMOVE";
        let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
            // A process of its own: the stand-in codex is the only CLI on PATH, so Claude Code cannot be checked.
            let root = std::env::temp_dir().join(format!("tico-remove-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir_all(root.join("bin")).unwrap();
            std::fs::write(root.join("bin/codex"), FAKE_CODEX).unwrap();
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(root.join("bin/codex"), std::fs::Permissions::from_mode(0o755)).unwrap();
            let path = format!("{}:/usr/bin:/bin", root.join("bin").display());
            let out = Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "login::tests::remove_waits_for_tico_runs_alone_on_the_subscription_and_keeps_a_login_it_cannot_check", "--nocapture"])
                .env(CHILD, &root).env("PATH", path).env("HOME", &root).env_remove("CODEX_HOME")
                .env_remove(crate::isolation::UID_ENV).env_remove(crate::isolation::GID_ENV)
                .env_remove("OPENAI_API_KEY").output().unwrap();
            std::fs::remove_dir_all(&root).ok();
            let said = String::from_utf8_lossy(&out.stdout);
            assert!(out.status.success() && said.contains("1 passed"), "{said}{}", String::from_utf8_lossy(&out.stderr));
            return;
        };
        let path = root.join("runner.json");
        std::fs::write(&path, json!({"url": "http://127.0.0.1:9", "token": "t", "runner_id": "r1", "projects_dir": root.join("projects")}).to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        let client = crate::client::Client::new(&config.url(), &config.token(), 1, 0).unwrap();
        let runner = Arc::new(Runner::new(config.clone(), client, crate::state::State::open(&root.join("state")).unwrap()));
        crate::profiles::add_profile(&config, "work").unwrap();
        let folder = root.join("profiles/work");
        let (codex, claude) = (folder.join("codex/auth.json"), folder.join("claude/.claude.json"));
        std::fs::write(&codex, "work\n").unwrap();
        std::fs::write(&claude, "{}").unwrap();
        let listed = || Config::load(&path).unwrap().data["profiles"].get("work").is_some();
        let logouts = || std::fs::read_to_string(root.join("runs")).unwrap_or_default().matches("logout").count();
        let remove = |server: bool| {
            let s = Session::new(&json!({"id": format!("l{}", uuid::Uuid::new_v4().simple()), "runtime": "codex", "kind": "remove", "profile": "work"}));
            // As the relay hands it over: runner.json read again, with the profile `add_profile` wrote.
            let (s2, r2, c2) = (s.clone(), runner.clone(), Config::load(&path).unwrap());
            let running = std::thread::spawn(move || run_remove(s2, r2, c2));
            wait_for(|| state(&s).0 == "finishing" || !s.live());
            if server { s.confirmed.store(true, Ordering::SeqCst) } else { s.cancelled.store(true, Ordering::SeqCst) }
            running.join().unwrap();
            state(&s)
        };

        // Tico refuses at the commit point (a bot came onto it): nothing is signed out or deleted.
        let refused = remove(false);
        assert_eq!(refused.0, "cancelled", "{refused:?}");
        assert!(codex.exists() && claude.exists() && listed() && logouts() == 0);

        // Claude Code's login files are there but it cannot be checked (not installed here): Codex's login goes,
        // the subscription and Claude Code's login stay.
        let kept = remove(true);
        assert_eq!(kept, ("done".into(), "codex signed out of work; it stays on this computer for Claude Code".into()));
        assert!(!codex.exists() && claude.exists() && listed());

        // While a removal runs nothing else starts on that subscription, and it does not start while something does.
        let mut logins = Logins::default();
        let held = Session::new(&json!({"id": "lheld", "runtime": "claude", "kind": "sign_in", "profile": "work"}));
        logins.sessions.insert(held.id.clone(), held);
        logins.begin(&runner, &json!({"id": "lrm", "runtime": "codex", "kind": "remove", "profile": "work"}));
        assert_eq!(state(&logins.sessions["lrm"]), ("failed".into(), "Something else is running on work; try again when it ends".into()));
        let mut logins = Logins::default();
        let held = Session::new(&json!({"id": "lheld", "runtime": "codex", "kind": "remove", "profile": "work"}));
        logins.sessions.insert(held.id.clone(), held);
        logins.begin(&runner, &json!({"id": "lin", "runtime": "claude", "kind": "sign_in", "profile": "work"}));
        assert_eq!(state(&logins.sessions["lin"]), ("failed".into(), "Something else is running on work; try again when it ends".into()));
        assert!(claude.exists() && listed());

        // Every other tool surely signed out: the subscription leaves runner.json and its folder goes.
        std::fs::remove_file(&claude).unwrap();
        std::fs::write(&codex, "work\n").unwrap();
        assert_eq!(remove(true), ("done".into(), "Removed work".into()));
        assert!(!folder.exists() && !listed());
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
