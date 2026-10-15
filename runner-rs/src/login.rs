//! Model sign-in started from the browser (runner/login.py; backend/model_login.py is the server half).
//!
//! The runner starts the model's own login command, in the same home the runtime's readiness check and turns
//! use, and relays what it prints: a link and a one-time code, or a prompt for a code the owner pastes back
//! (typed into a pty). The credential stays on this disk; every line that leaves is scrubbed of anything
//! token-shaped first. Success is not the command's word: sign-in is checked again afterwards.

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
    runtime: String,
    profile: String,
    inner: Mutex<Inner>,
    cancelled: AtomicBool,
}

impl Session {
    fn new(work: &Value) -> Arc<Session> {
        Arc::new(Session {
            id: text_of(&work["id"]),
            runtime: text_of(&work["runtime"]),
            profile: text_of(&work["profile"]),
            inner: Mutex::new(Inner { state: "starting".into(), dirty: true, ..Default::default() }),
            cancelled: AtomicBool::new(false),
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
        log(&format!("Tico runner: {} sign-in {state}", self.runtime));
        if state == "signed_in" {
            let name = if self.profile.is_empty() { default_profile } else { &self.profile };
            runner.sign_in.clear_rejection(&self.runtime, name);
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

fn run_session(s: Arc<Session>, runner: Arc<Runner>, config: Config, lifetime: Duration) {
    let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
    let fail = |state: &str, msg: &str| s.finish(state, msg, &runner, &default);
    let Some(login) = crate::harness_tools::login_command(&s.runtime) else { return fail("failed", "Browser sign-in is not available") };
    let path = std::env::var("PATH").unwrap_or_default();
    let Some(exe) = crate::env::which(&crate::harness_tools::executable_for(&s.runtime), &path) else {
        return fail("failed", "The runtime is not installed on this computer");
    };
    let exe = exe.display().to_string();
    let profile = profile_for(&config, &s.profile);
    if let Some(Err(e)) = profile.as_ref().map(crate::profiles::hand_to_bot) {
        return fail("failed", &format!("The subscription profile is not ready for sign-in: {e}"));
    }
    let process: Env = std::env::vars().collect();
    let mut env = match &profile {
        Some(p) => p.environment(&s.runtime, &process),
        None => process,
    };
    if crate::harness_tools::caps(&s.runtime, "").own_config_home {
        // Bot turns read this host's login from Tico's own config folder, never the operator's.
        crate::hosts::use_own_config_home(&s.runtime, profile.as_ref(), &mut env);
    }
    // The person opens the link on their own screen, not on this machine.
    env.insert("BROWSER".into(), "true".into());
    env.insert("NO_COLOR".into(), "1".into());
    env.extend(crate::harness_tools::env_for(&s.runtime));
    let (mut child, read_fd, writer) = match spawn(&exe, &login.command, &env, login.terminal) {
        Ok(x) => x,
        Err(e) => return fail("failed", &format!("The sign-in command could not start ({:?})", e.kind())),
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
    let (state, message) = match verdict {
        Some(v) => v,
        None if s.cancelled.load(Ordering::SeqCst) => ("cancelled".into(), "Cancelled".into()),
        None if !child.wait().map(|st| st.success()).unwrap_or(false) => ("failed".into(), "The sign-in command stopped without signing in".into()),
        None => {
            let headless = profile.is_none() && runner.sign_in.headless_secret(&s.runtime).is_some();
            match signed_in(&s.runtime, &exe, &env, headless) {
                (true, detail) => ("signed_in".into(), if detail.is_empty() { "Signed in".into() } else { detail }),
                _ => ("failed".into(), format!("The command finished but {} is still not signed in", s.runtime)),
            }
        }
    };
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
        let mut config = Config::load(&runner.config.path).unwrap_or_else(|_| runner.config.clone());
        let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
        let named = (!s.profile.is_empty()).then(|| profile_for(&config, &s.profile)).flatten();
        if crate::harness_tools::login_command(&s.runtime).is_none() {
            s.finish("failed", &format!("Browser sign-in is not available for {}", s.runtime), runner, &default);
        } else if self.sessions.values().any(|o| o.live() && o.runtime == s.runtime && o.profile == s.profile) {
            s.finish("failed", &format!("Another sign-in is already running for {}", s.runtime), runner, &default);
        } else if named.as_ref().is_some_and(|p| p.share_operator) && !crate::harness_tools::caps(&s.runtime, "").own_config_home {
            // A host with its own config folder never uses the operator's login, so a shared profile still signs in to Tico's own.
            s.finish("failed", &format!("Profile {} shares the operator's own logins; sign in normally", s.profile), runner, &default);
        } else {
            if !s.profile.is_empty() && named.is_none() {
                if let Err(e) = crate::profiles::add_profile(&config, &s.profile) {
                    s.finish("failed", &format!("Could not create subscription profile: {e}"), runner, &default);
                    self.sessions.insert(s.id.clone(), s);
                    return;
                }
                config = Config::load(&runner.config.path).unwrap_or(config);
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
                if runner.client.post(&format!("runner-logins/{id}/report"), &body, None).await.is_err() {
                    s.inner.lock().unwrap().dirty = true; // said again on the next poll
                    continue;
                }
                {
                    let mut i = s.inner.lock().unwrap();
                    i.taken = i.taken && body.get("code_taken").is_none();
                }
                if body["state"] == "signed_in" {
                    runner.events.ask(&["heartbeat"]); // let readiness show it now
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
