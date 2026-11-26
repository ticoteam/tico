//! Model sign-in started from the browser (runner/login.py; backend/model_login.py is the server half).
//!
//! The runner starts the model's own login command, in the same home the runtime's readiness check and turns
//! use, and relays what it prints: a link and a one-time code, or a prompt for a code the owner pastes back
//! (typed into a pty). The credential stays on this disk; every line that leaves is scrubbed of anything
//! token-shaped first. Success is not the command's word: sign-in is checked again afterwards.
//!
//! The same relay signs a login out (the CLI's own logout, checked afterwards), switches it to another account
//! (signed in on the side in a home of its own, moved into place only once it has finished and the server has agreed
//! it is no longer canceled, so a canceled or failed switch leaves the old login working), and lets the company's shared key sign a signed-out computer login in again.
//! A computer login a person signed out stays signed out (signin.rs SIGNED_OUT_FILE).
//!
//! It also clears this computer's own model key (`clear_key`) and puts a new one in its place (`use_key`), or in place of
//! the subscription its own login runs on (`to_key`, Switch to API key). No key ever travels in the queue: a new key is
//! fetched from the app over the runner's own connection.
//!
//! Connect (`connect`, Settings > Models) signs a plan in for a company subscription and leaves this computer's own login
//! as it was: the CLI prints a token, or writes its login in a home of its own. That one result goes to the server in its
//! own request (`runner-logins/<id>/subscription`, backend/model_subscriptions.py) and is kept nowhere here. No line of a
//! Connect's output is relayed, and its link and code are read only from what came before the token.

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
/// Connect (`connect`): this runner signs a plan in for a company subscription.
pub const CONNECT_CAPABILITY: &str = "model_connect_v1";
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
    masked: Regex,
    token_run: Regex,
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
            masked: r(r"\*{8,}\S*"),
            token_run: r(r"[A-Za-z0-9_\-]{20,}"),
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

/// A terminal's output as the text it shows.
fn plain(raw: &str) -> String {
    let p = patterns();
    let text = p.osc.replace_all(raw, "");
    let text = p.cursor_column.replace_all(&text, " ");
    let text = p.csi.replace_all(&text, "");
    let text = p.esc.replace_all(&text, "");
    p.control.replace_all(&text, "").into_owned()
}

/// The shortest thing Connect takes for a token: anything less is the CLI naming the format, or a piece of a token.
const MIN_TOKEN: usize = 90;
/// How long a Connect's command has to end by itself once it has printed its token.
#[cfg(not(test))]
const TOKEN_GRACE: Duration = Duration::from_secs(10);
#[cfg(test)]
const TOKEN_GRACE: Duration = Duration::from_millis(500);
/// How long after the code Enter is typed. `claude setup-token` takes a code and Enter written together as a paste and
/// waits for an Enter that never comes ("Code sent. Waiting for the computer to finish…", Nov 3): typed alone a moment
/// later, it signs in at once.
const ENTER_AFTER_CODE: Duration = Duration::from_millis(1200);

/// The token `prefix` starts, from what a Connect's command has printed so far. Until the command has `ended` a token
/// counts only once something follows it: the rest of it may still be arriving.
///
/// A renderer that repaints only the cells that changed steps over the others with cursor moves, so a token painted on
/// a row that already had text can arrive with pieces missing from the stream. Once the command has ended, the rows as
/// the terminal shows them (`screen`) are read too.
fn printed_token(raw: &str, prefix: &str, ended: bool) -> Option<String> {
    let direct = raw
        .contains(prefix)
        .then(|| token_in(&plain(raw), prefix, ended))
        .flatten();
    if !ended {
        return direct;
    }
    // A move after the token's 90th character leaves `direct` a token cut short: the longer reading is the token.
    let shown = screen(raw)
        .iter()
        .find_map(|row| token_in(row, prefix, true));
    match (direct, shown) {
        (Some(a), Some(b)) => Some(if b.len() > a.len() { b } else { a }),
        (a, b) => a.or(b),
    }
}

fn token_in(text: &str, prefix: &str, ended: bool) -> Option<String> {
    let part = |c: char| c.is_ascii_alphanumeric() || c == '-' || c == '_';
    text.match_indices(prefix).find_map(|(at, _)| {
        let tail = &text[at..];
        let end = tail.find(|c| !part(c));
        let token = &tail[..end.unwrap_or(tail.len())];
        (token.len() >= MIN_TOKEN && (end.is_some() || ended)).then(|| token.to_string())
    })
}

/// The pty's size (`spawn`).
const SCREEN_ROWS: usize = 50;
const SCREEN_COLS: usize = 500;

/// A terminal's output as the rows it ends up showing: characters land where the cursor is, and cursor moves and
/// erases are followed instead of dropped. Enough of a terminal for a CLI's repaints; colours and modes are ignored.
fn screen(raw: &str) -> Vec<String> {
    let raw = patterns().osc.replace_all(raw, "");
    let mut rows: Vec<Vec<char>> = vec![vec![]];
    let (mut row, mut col) = (0usize, 0usize);
    let mut chars = raw.chars();
    while let Some(c) = chars.next() {
        match c {
            '\x1b' => match chars.next() {
                Some('[') => {
                    let mut params = String::new();
                    let mut last = None;
                    for c in chars.by_ref() {
                        if ('@'..='~').contains(&c) {
                            last = Some(c);
                            break;
                        }
                        params.push(c);
                    }
                    let mut nums = params.split(';').map(|n| n.parse::<usize>().ok());
                    let first = nums.next().flatten();
                    let n = first.unwrap_or(1).clamp(1, SCREEN_COLS);
                    let top = rows.len().saturating_sub(SCREEN_ROWS);
                    match last {
                        Some('A') => row = row.saturating_sub(n),
                        Some('B') => row += n.min(SCREEN_ROWS),
                        Some('C') => col = (col + n).min(SCREEN_COLS - 1),
                        Some('D') => col = col.saturating_sub(n),
                        Some('E') => (row, col) = (row + n.min(SCREEN_ROWS), 0),
                        Some('F') => (row, col) = (row.saturating_sub(n), 0),
                        Some('G') => col = (n - 1).min(SCREEN_COLS - 1),
                        Some('H') | Some('f') => {
                            row = top + (n - 1).min(SCREEN_ROWS - 1);
                            col = nums.next().flatten().unwrap_or(1).clamp(1, SCREEN_COLS) - 1;
                        }
                        Some('K') => {
                            if let Some(line) = rows.get_mut(row) {
                                match first.unwrap_or(0) {
                                    0 => line.truncate(col),
                                    1 => line.iter_mut().take(col + 1).for_each(|c| *c = ' '),
                                    _ => line.clear(),
                                }
                            }
                        }
                        Some('J') => match first.unwrap_or(0) {
                            0 => {
                                rows.truncate(row + 1);
                                if let Some(line) = rows.get_mut(row) {
                                    line.truncate(col);
                                }
                            }
                            2 | 3 => rows.iter_mut().skip(top).for_each(Vec::clear),
                            _ => {}
                        },
                        _ => {}
                    }
                }
                Some('(' | ')' | '#') => {
                    chars.next();
                }
                _ => {}
            },
            '\r' => col = 0,
            '\n' => (row, col) = (row + 1, 0),
            '\x08' => col = col.saturating_sub(1),
            c if c.is_control() => {}
            c => {
                if col >= SCREEN_COLS {
                    (row, col) = (row + 1, 0);
                }
                if rows.len() <= row {
                    rows.resize(row + 1, vec![]);
                }
                let line = &mut rows[row];
                if line.len() <= col {
                    line.resize(col + 1, ' ');
                }
                line[col] = c;
                col += 1;
            }
        }
    }
    rows.iter().map(|r| r.iter().collect()).collect()
}

/// A CLI waiting for Enter after an OAuth error cannot finish without another interactive attempt. Never relay
/// its text: a Connect's output can contain codes and credentials. Cursor movements and wrapping split words.
fn oauth_retry(raw: &str) -> bool {
    let text: String = plain(raw)
        .chars()
        .filter(|c| !c.is_whitespace())
        .flat_map(char::to_lowercase)
        .collect();
    text.contains("oautherror:") && text.contains("pressentertoretry")
}

/// Only an allowlisted category leaves the terminal. Provider errors can echo the code, URL or credential, and
/// a rejected code's generic message does not distinguish expired, reused or incomplete codes.
///
/// A command that ends by itself after taking the code was refused by the provider: the token was made and then held
/// back by the account's organization (`provider_refused`), or the account is on hold (`account_on_hold`).
fn oauth_failure(raw: &str) -> &'static str {
    let squeezed = |text: String| -> String {
        text.chars()
            .filter(|c| !c.is_whitespace())
            .flat_map(char::to_lowercase)
            .collect()
    };
    let text = squeezed(plain(raw));
    // A repaint can step over cells, so the rows as shown are read as well.
    let shown = squeezed(screen(raw).join(" ")).replace('\u{2019}', "'");
    let text = text.replace('\u{2019}', "'");
    let says = |words: &[&str]| words.iter().any(|w| text.contains(w) || shown.contains(w));
    if says(&[
        "doesnotpermit",
        "verifyyourorganization'ssettings",
        "wasnotshown",
        "reportedthetokenrevoked",
    ]) {
        return "provider_refused";
    }
    if says(&[
        "account_on_hold",
        "accountisonhold",
        "accountonhold",
        "accounthasbeenplacedonhold",
    ]) {
        return "account_on_hold";
    }
    let Some((_, error)) = text.split_once("oautherror:") else {
        return "cli_stopped";
    };
    if error.contains("thatcodewasn'taccepted") || error.contains("thatcodewasnotaccepted") {
        "oauth_code_rejected"
    } else if [
        "fetchfailed",
        "econnreset",
        "etimedout",
        "enotfound",
        "unabletoconnect",
        "requesttimedout",
    ]
    .iter()
    .any(|s| error.contains(s))
    {
        "oauth_network_error"
    } else {
        "oauth_error"
    }
}

/// What a person reads for a Connect that ended in `category` (`oauth_failure`). Never the CLI's own text.
fn failure_message(category: &str, waiting: bool) -> &'static str {
    match category {
        "provider_refused" => "This account was not given a token. Its organization's settings may not allow it.",
        "account_on_hold" => "This account was not given a token. The account is on hold.",
        _ if waiting => "The sign-in could not finish. Choose Try again and use the code from the new sign-in page.",
        _ => "The sign-in command stopped without signing in",
    }
}

/// An Antigravity Connect's failures in a person's words (T3 Code's AntigravityAuth.ts and `safeAuthFailure`).
const AGY_NOT_STARTED: &str =
    "Antigravity has not started after 90 seconds. Its runtime may be stuck starting on this machine.";
const AGY_NO_LINK: &str = "Antigravity has not reached Google after 30 seconds. Check this machine's network connection. Broken IPv6 is a common cause.";
const AGY_EXPIRED: &str = "Google sign-in expired. Start sign-in again.";
const AGY_NOT_APPROVED: &str = "Google sign-in was not approved. Start sign-in again.";
const AGY_SUBSCRIPTION: &str =
    "Google requires an eligible Antigravity subscription for this account.";
const AGY_NO_SESSION: &str = "Signed in, but Antigravity could not start a session or load models.";
/// How long `agy acp --login` has to print anything, and then Google's link.
const AGY_START: Duration = Duration::from_secs(90);
const AGY_LINK: Duration = Duration::from_secs(30);
/// The most an Antigravity sign-in's session check takes.
const AGY_CHECK: Duration = Duration::from_secs(90);

/// What a person reads for an Antigravity Connect that got no sign-in: what its session check found (`checked`), else
/// what `agy acp --login` said, else how it exited. The CLI's own text never leaves this computer.
fn agy_failure(raw: &str, checked: Option<&str>, code: Option<i32>) -> String {
    static DENIED: OnceLock<Regex> = OnceLock::new();
    let text = format!("{} {}", checked.unwrap_or_default(), plain(raw)).to_lowercase();
    if text.contains("subscription_required") {
        return AGY_SUBSCRIPTION.into();
    }
    if checked.is_some() {
        return AGY_NO_SESSION.into();
    }
    if DENIED
        .get_or_init(|| Regex::new(r"access_denied|denied access|cancelled").unwrap())
        .is_match(&text)
    {
        return AGY_NOT_APPROVED.into();
    }
    if text.contains("invalid_grant") || text.contains("expired") {
        return AGY_EXPIRED.into();
    }
    match code {
        Some(0) => AGY_NO_SESSION.into(),
        Some(c) => format!("Antigravity exited with code {c}. Start sign-in again."),
        None => "Antigravity exited unexpectedly. Start sign-in again.".into(),
    }
}

/// An Antigravity sign-in in `home` counts once a session starts on it there, as T3 Code checks one: `agy acp` says
/// `initialize`, `authenticate` and `session/new`, and the session offers a model. Headless `agy models` would read
/// `$HOME/.gemini` whatever the home. Err is what agy said, for `agy_failure` only.
fn agy_session(exe: &str, home: &std::path::Path, env: &Env, most: Duration) -> Result<(), String> {
    use crate::hosts::antigravity as agy;
    use std::io::{BufRead, Write};
    let scratch =
        std::env::temp_dir().join(format!("tico-agy-check-{}", uuid::Uuid::new_v4().simple()));
    let (cwd, tmp) = (scratch.join("work"), scratch.join("tmp"));
    let checked = (|| -> Result<(), String> {
        for dir in [&cwd, &tmp] {
            crate::isolation::mkdir(dir, 0o700).map_err(|e| e.to_string())?;
        }
        let home = home.display().to_string();
        let argv = agy::acp_argv(&[], &home);
        let args: Vec<&str> = argv.iter().map(String::as_str).collect();
        let mut command = model_command(exe, &args, &agy::acp_env(env, &home, true, &tmp));
        command
            .current_dir(&cwd)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        unsafe {
            command.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
        let mut child = command.spawn().map_err(|e| e.to_string())?;
        let (Some(mut stdin), Some(stdout)) = (child.stdin.take(), child.stdout.take()) else {
            kill(&mut child);
            return Err("agy has no pipes".into());
        };
        let (tx, rx) = std::sync::mpsc::channel::<Value>();
        let reader = std::thread::spawn(move || {
            for line in std::io::BufReader::new(stdout)
                .lines()
                .map_while(Result::ok)
            {
                if let Ok(v) = serde_json::from_str::<Value>(line.trim()) {
                    if tx.send(v).is_err() {
                        break;
                    }
                }
            }
        });
        let deadline = Instant::now() + most.min(AGY_CHECK);
        let mut ask = |id: i64, method: &str, params: Value| -> Result<Value, String> {
            let line = json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params});
            writeln!(stdin, "{line}").map_err(|e| format!("{method}: {e}"))?;
            loop {
                let left = deadline.saturating_duration_since(Instant::now());
                let msg = rx
                    .recv_timeout(left)
                    .map_err(|_| format!("{method}: Antigravity did not answer"))?;
                if msg["id"] == id && msg.get("method").is_none() {
                    return match msg.get("error") {
                        Some(e) => Err(format!("{method}: {e}")),
                        None => Ok(msg["result"].clone()),
                    };
                }
            }
        };
        let mut session = ask(1, "initialize", agy::initialize_params());
        if session.is_ok() {
            session = ask(2, "authenticate", json!({"methodId": agy::AUTH_METHOD}));
        }
        if session.is_ok() {
            session = ask(3, "session/new", json!({"cwd": cwd, "mcpServers": []}));
        }
        drop(stdin);
        // agy has a moment to exit on its own, watched without reaping it: then its whole group goes while the group's
        // number is still its own (as hosts/antigravity.rs's turns), and only then is it reaped.
        let pid = child.id() as i32;
        let until = Instant::now() + Duration::from_secs(2);
        while !crate::hosts::exited_unreaped(pid) && Instant::now() < until {
            std::thread::sleep(Duration::from_millis(50));
        }
        // SAFETY: agy leads group `pid` (setsid above) and is not reaped yet.
        unsafe { libc::killpg(pid, libc::SIGKILL) };
        let _ = child.wait();
        // Nothing of the group holds agy's output now, so its reader ends (its sender with it) before the scratch it ran
        // in goes; a process that left the group could hold it open, so it has two seconds.
        let until = Instant::now() + Duration::from_secs(2);
        while let Ok(_) | Err(std::sync::mpsc::RecvTimeoutError::Timeout) =
            rx.recv_timeout(until.saturating_duration_since(Instant::now()))
        {
            if Instant::now() >= until {
                break;
            }
        }
        if reader.is_finished() {
            let _ = reader.join();
        }
        if agy::models(&session?) == 0 {
            return Err("session/new: no models".into());
        }
        Ok(())
    })();
    let _ = std::fs::remove_dir_all(&scratch);
    checked
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
    let text = plain(raw);
    let pieces: Vec<&str> = p.newline.split(&text).collect();
    let complete = &pieces[..pieces.len() - 1]; // the last piece may still be arriving
    let lines: Vec<String> = pieces.iter().map(|s| squash(s)).collect();
    let mut url = links.first().cloned().unwrap_or_default();
    if url.is_empty() {
        let joined = complete.join("\n") + "\n";
        url = p
            .url
            .find(&joined)
            .map(|m| {
                m.as_str()
                    .trim_end()
                    .trim_end_matches(['.', ',', ')', ';'])
                    .to_string()
            })
            .unwrap_or_default();
    }
    let hide: String = hide.chars().take(12).collect();
    let mut code = String::new();
    for line in complete.iter().map(|s| squash(s)) {
        if !hide.is_empty() && line.contains(&hide) {
            continue; // the code the runner typed, echoed back, is never a device code to report
        }
        if p.user_code_full.is_match(&line)
            || (line.to_lowercase().contains("code") && p.user_code.is_match(&line))
        {
            code = p.user_code.find(&line).unwrap().as_str().to_string();
            break;
        }
    }
    let mut shown: Vec<String> = vec![];
    for line in &lines {
        if !hide.is_empty() && line.contains(&hide) {
            continue;
        }
        let line = p.link.replace_all(line, "[link]").into_owned();
        if p.alnum.find_iter(&line).count() < 3
            || (!line.contains(' ') && line.chars().count() > 40)
        {
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
    /// A Connect's result, until the server has taken it (`Logins::flush`).
    captured: Option<String>,
    save_retry_logged: bool,
    /// Every byte the command has printed, so the part after a typed code can be found once `raw` is trimmed.
    seen: usize,
}

/// Why a Connect failed, for the server (`Session::report`'s `trace`): the phases in order, the allowlisted category
/// and the scrubbed last screen. A lock of its own: phases are noted while `inner` is held.
#[derive(Default)]
struct Diag {
    phases: Vec<Value>,
    category: String,
    screen: String,
    /// A server from before `trace` refuses a report that carries it: said again without.
    refused: bool,
}
const MAX_PHASES: usize = 60;

/// One login command, from start to a verdict.
pub struct Session {
    id: String,
    /// sign_in | switch | sign_out | use_shared_key | clear_key | use_key | to_subscription | to_key | remove | connect (backend/model_login.py KINDS).
    kind: String,
    runtime: String,
    profile: String,
    /// A Connect for a full Claude login (backend/claude_login.py): the CLI's own sign-in, run on the side, whose
    /// login files are the result.
    full: bool,
    inner: Mutex<Inner>,
    diag: Mutex<Diag>,
    canceled: AtomicBool,
    /// The server took this switch's `finishing` report: it can no longer be canceled, so the new login goes in.
    confirmed: AtomicBool,
    started: Instant,
}

impl Session {
    fn new(work: &Value) -> Arc<Session> {
        Arc::new(Session {
            id: text_of(&work["id"]),
            kind: Some(text_of(&work["kind"]))
                .filter(|k| !k.is_empty())
                .unwrap_or_else(|| "sign_in".into()),
            runtime: text_of(&work["runtime"]),
            profile: text_of(&work["profile"]),
            full: work["subscription_kind"] == "claude_login",
            inner: Mutex::new(Inner {
                state: "starting".into(),
                dirty: true,
                ..Default::default()
            }),
            diag: Mutex::default(),
            canceled: AtomicBool::new(false),
            confirmed: AtomicBool::new(false),
            started: Instant::now(),
        })
    }

    /// Correlate the browser's login row with local stages. Only fixed phases, a CLI exit code, and server-owned
    /// identifiers are logged; never the CLI's text, account details, URL, code or captured value.
    fn trace(&self, phase: &'static str, exit_code: Option<i32>) {
        if self.kind == "connect" {
            let elapsed_ms = self.started.elapsed().as_millis() as u64;
            log(&format!(
                "Tico runner: model sign-in {}",
                json!({"login_id": self.id, "runtime": self.runtime,
                "phase": phase, "elapsed_ms": elapsed_ms, "exit_code": exit_code})
            ));
            let mut d = self.diag.lock().unwrap();
            if d.phases.len() < MAX_PHASES {
                d.phases.push(
                    json!({"phase": phase, "elapsed_ms": elapsed_ms, "exit_code": exit_code}),
                );
            }
        }
    }

    /// A Connect that got no credential: the category of what its command last showed (`oauth_failure`) and that
    /// screen, scrubbed (`screen_digest`), kept for the failure report and written to the log as a diagnostic line, so
    /// the heartbeat's `recent_errors` has it too. `since` is how many of the command's last bytes to read.
    fn why(&self, since: Option<usize>) -> &'static str {
        let (reason, since, digest) = {
            let i = self.inner.lock().unwrap();
            let since = since.unwrap_or(i.seen);
            let mut from = i.raw.len().saturating_sub(since);
            while !i.raw.is_char_boundary(from) {
                from += 1;
            }
            (
                oauth_failure(&i.raw),
                since,
                screen_digest(&i.raw[from..], i.typed.as_deref().unwrap_or("")),
            )
        };
        crate::util::diagnostic(&format!(
            "Tico runner: model sign-in {}",
            json!({"login_id": self.id, "runtime": self.runtime, "phase": "final_screen", "reason": reason,
            "elapsed_ms": self.started.elapsed().as_millis() as u64, "bytes": since, "screen": digest})
        ));
        {
            let mut d = self.diag.lock().unwrap();
            d.category = reason.into();
            d.screen = digest;
        }
        self.trace(reason, None);
        reason
    }

    fn live(&self) -> bool {
        !self.inner.lock().unwrap().finished
    }

    fn submit(&self, code: &str) {
        let mut i = self.inner.lock().unwrap();
        if i.pending.is_none() && i.typed.is_none() {
            i.pending = Some(code.into());
            self.trace("code_received", None);
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
        // A failed Connect says why, so the reason is on the server and not only in this computer's log.
        if self.kind == "connect" && i.state == "failed" {
            let d = self.diag.lock().unwrap();
            if !d.refused {
                body["trace"] =
                    json!({"category": d.category, "phases": d.phases, "screen": d.screen});
            }
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
        log(&format!(
            "Tico runner: {} {} {state}",
            self.runtime,
            self.kind.replace('_', " ")
        ));
        self.trace(
            match state {
                "done" => "completed",
                "failed" => "failed",
                "cancelled" => "canceled",
                _ => "finished",
            },
            None,
        );
        // A failed Connect's phases in one line that joins the heartbeat's `recent_errors`: the lines above stay in this
        // computer's log. Fixed words and numbers only.
        if self.kind == "connect" && state == "failed" {
            let steps: Vec<String> = self
                .diag
                .lock()
                .unwrap()
                .phases
                .iter()
                .map(|p| {
                    let exit = p["exit_code"]
                        .as_i64()
                        .map(|c| format!("={c}"))
                        .unwrap_or_default();
                    format!("{}@{}{exit}", text_of(&p["phase"]), p["elapsed_ms"])
                })
                .collect();
            crate::util::diagnostic(&format!(
                "Tico runner: model sign-in {}",
                json!({"login_id": self.id, "runtime": self.runtime, "phase": "failed_steps", "steps": steps.join(" ")})
            ));
        }
        // Whatever the verdict, the next heartbeat asks the profile's CLI again instead of a cached answer.
        crate::profiles::forget_report();
        if state == "signed_in" {
            let name = if self.profile.is_empty() {
                default_profile
            } else {
                &self.profile
            };
            runner.sign_in.clear_rejection(&self.runtime, name);
            // A person signed the computer's own login in: it is no longer signed out on purpose.
            if self.profile.is_empty() {
                if let Err(e) = runner.sign_in.unmark_signed_out(&self.runtime) {
                    log(&format!(
                        "Tico runner: {} is signed in but still marked signed out: {e}",
                        self.runtime
                    ));
                }
            }
        }
        // Every finish is after its action (a sign-in, a sign-out's logout, a switch, a removal, or a failure that undid
        // its marks), so the probe reads the result of that action, whatever the verdict.
        crate::readiness::request_probe();
    }
}

fn profile_for(config: &Config, name: &str) -> Option<crate::profiles::Profile> {
    crate::profiles::select(&config.data, Some(name))
}

// Login and its verdict must use the same user as runtime readiness and bot turns, as well as the same home.
fn model_command(exe: &str, args: &[&str], env: &Env) -> Command {
    let mut command = crate::isolation::std_command(exe);
    command.args(args).env_clear().envs(env);
    command
}

/// Whether `runtime` is signed in with `env` (the profile's homes), as readiness checks it.
fn signed_in(runtime: &str, exe: &str, env: &Env, headless: bool) -> (bool, String) {
    let command = crate::harness_tools::caps(runtime).sign_in_status.check == "command";
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
        c.stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = c.spawn().ok()?;
        let deadline = Instant::now() + Duration::from_secs(secs);
        loop {
            if let Ok(Some(_)) = child.try_wait() {
                let out = child.wait_with_output().ok()?;
                return Some((
                    out.status.success(),
                    format!(
                        "{}\n{}",
                        String::from_utf8_lossy(&out.stdout),
                        String::from_utf8_lossy(&out.stderr)
                    ),
                ));
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
    let status = crate::harness_tools::caps(runtime).sign_in_status;
    let args: Vec<&str> = status.args.iter().map(String::as_str).collect();
    let timeout = if status.timeout_s > 0 {
        status.timeout_s
    } else {
        15
    };
    match status.check.as_str() {
        "command" => {
            let (ok, out) = run(&args, timeout)?;
            let out = out.to_lowercase();
            if !status.not_ready.is_empty() && out.contains(&status.not_ready) {
                // Some CLIs use a nonzero exit for their explicit signed-out answer.
                Some((false, String::new()))
            } else if ok && out.contains(&status.ready) {
                Some((
                    true,
                    if out.contains("api key") {
                        "Signed in with an API key".into()
                    } else {
                        status.ready_detail
                    },
                ))
            } else {
                None
            }
        }
        "json" => {
            let (_, out) = run(&args, timeout)?;
            let s = serde_json::from_str::<Value>(out.lines().next().unwrap_or(""))
                .ok()
                .or_else(|| serde_json::from_str(out.trim()).ok())?;
            // Only a true or false is an answer: an error report, or a field that is missing or null, says nothing.
            if !s[&status.field].as_bool()? {
                return Some((false, String::new()));
            }
            let method = text_of(&s[&status.method_field]);
            Some((
                true,
                if method.is_empty() {
                    status.ready_detail
                } else {
                    format!("Signed in with {method}")
                },
            ))
        }
        // No status command: the login file in this sign-in's config folder is the sign-in, as readiness checks it.
        "file" => crate::hosts::file_signed_in(runtime, env).map(|on| {
            (
                on,
                if on {
                    crate::hosts::file_sign_in_detail(runtime, env)
                        .map(String::from)
                        .unwrap_or(status.ready_detail)
                } else {
                    String::new()
                },
            )
        }),
        _ => None,
    }
}

/// A Connect's screen after the code went in, as one line for the runner's log: links, the typed code and anything
/// token-shaped taken out (`redact`), the last 240 characters. A code that the CLI accepted but then stops on (a
/// question, a choice, an error with no retry) shows here; before, the log went quiet between code_written and cancel.
pub(crate) fn screen_digest(raw: &str, typed: &str) -> String {
    let mut text = plain(raw);
    if !typed.is_empty() {
        text = text.replace(typed, "");
    }
    let p = patterns();
    let text = redact(&p.link.replace_all(&text, "[link]"));
    // A code the CLI echoes masked keeps its last characters, and a token a repaint split leaves pieces too short for
    // `redact`: neither leaves this computer.
    let text = p.masked.replace_all(&text, "********");
    let text = squash(&p.token_run.replace_all(&text, "[redacted]"));
    let skip = text.chars().count().saturating_sub(240);
    text.chars().skip(skip).collect()
}

struct AfterCode {
    seen_at: usize,
    checked: Instant,
    logged: Instant,
    last: String,
}

impl AfterCode {
    fn new(seen_at: usize) -> AfterCode {
        let now = Instant::now();
        AfterCode {
            seen_at,
            checked: now,
            logged: now,
            last: String::new(),
        }
    }

    /// Every 2 s: a changed screen is logged; an unchanged one says it is still waiting every 20 s.
    fn check(&mut self, s: &Session) {
        if self.checked.elapsed() < Duration::from_secs(2) {
            return;
        }
        self.checked = Instant::now();
        let (since, digest) = {
            let i = s.inner.lock().unwrap();
            let since = i.seen.saturating_sub(self.seen_at);
            let mut from = i.raw.len().saturating_sub(since);
            while !i.raw.is_char_boundary(from) {
                from += 1;
            }
            (
                since,
                screen_digest(&i.raw[from..], i.typed.as_deref().unwrap_or("")),
            )
        };
        if digest != self.last {
            log(&format!(
                "Tico runner: model sign-in {}",
                json!({"login_id": s.id, "runtime": s.runtime,
                "phase": "after_code_screen", "elapsed_ms": s.started.elapsed().as_millis(), "bytes": since, "screen": digest})
            ));
            self.last = digest;
            self.logged = Instant::now();
        } else if self.logged.elapsed() >= Duration::from_secs(20) {
            log(&format!(
                "Tico runner: model sign-in {}",
                json!({"login_id": s.id, "runtime": s.runtime,
                "phase": "waiting_after_code", "elapsed_ms": s.started.elapsed().as_millis(), "bytes": since})
            ));
            self.logged = Instant::now();
        }
    }
}

fn spawn(
    exe: &str,
    argv: &[String],
    env: &Env,
    terminal: bool,
) -> std::io::Result<(Child, OwnedFd, Option<OwnedFd>)> {
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
        cmd.stdin(Stdio::null())
            .stdout(Stdio::from(write.try_clone()?))
            .stderr(Stdio::from(write));
        let child = cmd.spawn()?;
        drop(cmd); // closes our copies of the write end, so the reader sees end of file
        return Ok((child, read, None));
    }
    let (mut master, mut slave) = (0, 0);
    let size = libc::winsize {
        ws_row: 50,
        ws_col: 500,
        ws_xpixel: 0,
        ws_ypixel: 0,
    };
    if unsafe {
        libc::openpty(
            &mut master,
            &mut slave,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &size as *const _ as *mut _,
        )
    } != 0
    {
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
    cmd.stdin(Stdio::from(slave.try_clone()?))
        .stdout(Stdio::from(slave.try_clone()?))
        .stderr(Stdio::from(slave));
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
fn login_env(
    runtime: &str,
    config: &Config,
    profile_name: &str,
) -> Result<(String, Env, Option<crate::profiles::Profile>), String> {
    let path = std::env::var("PATH").unwrap_or_default();
    let Some(exe) = crate::env::which(&crate::harness_tools::executable_for(runtime), &path) else {
        return Err("The runtime is not installed on this computer".into());
    };
    let profile = profile_for(config, profile_name);
    if let Some(Err(e)) = profile.as_ref().map(crate::profiles::hand_to_bot) {
        return Err(format!(
            "The subscription profile is not ready for sign-in: {e}"
        ));
    }
    let process: Env = std::env::vars().collect();
    let mut env = match &profile {
        Some(p) => p.environment(runtime, &process),
        None => process,
    };
    crate::hosts::prepare_login_home(runtime, &env);
    if profile.as_ref().is_some_and(|p| !p.share_operator) {
        for key in crate::harness_tools::api_key_env(runtime) {
            env.remove(&key);
        }
    }
    if crate::harness_tools::caps(runtime).own_config_home {
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
    let out = model_command(args[0], &args[1..], env)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .map_err(|e| format!("{} could not run ({:?})", args[0], e.kind()))?;
    if out.status.success() {
        Ok(())
    } else {
        Err(format!(
            "{} failed: {}",
            args[0],
            redact(String::from_utf8_lossy(&out.stderr).trim())
                .chars()
                .take(200)
                .collect::<String>()
        ))
    }
}

/// A file's contents, read as the user the CLI runs as; None when it cannot be read or is empty.
fn file_text(path: &std::path::Path, env: &Env) -> Option<String> {
    let path = path.display().to_string();
    let out = model_command("cat", &[&path], env)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    let text = String::from_utf8(out.stdout).ok()?;
    (out.status.success() && !text.trim().is_empty()).then_some(text)
}

/// Where Claude Code keeps its login under HOME, beside `.claude.json` (claude-code.toml `login.files`).
const FULL_FILE: &str = ".credentials.json";

/// A full Claude login signed in on the side (`side` was its HOME): its `.credentials.json` and the account
/// `.claude.json` names, as one value (backend/claude_login.py parse). None when either is missing.
fn full_login(side: &std::path::Path, env: &Env) -> Option<String> {
    let creds: Value =
        serde_json::from_str(&file_text(&side.join(".claude").join(FULL_FILE), env)?).ok()?;
    let settings: Value =
        serde_json::from_str(&file_text(&side.join(".claude.json"), env)?).ok()?;
    let oauth = creds
        .get("claudeAiOauth")
        .filter(|o| o["refreshToken"].is_string())?;
    let account = settings
        .get("oauthAccount")
        .filter(|a| a["emailAddress"].is_string())?;
    let value = serde_json::json!({"claudeAiOauth": oauth, "oauthAccount": account}).to_string();
    for secret in crate::model_subscriptions::claude_secrets(&value) {
        crate::redact::hide_everywhere(&secret, false);
    }
    Some(value)
}

/// The folder `runtime`'s login lives in with `env`: its home variable (CODEX_HOME, HOME), or Codex's own default, or
/// for a runtime whose plans sign in per turn (model_subscriptions.rs TURN_HOMES) its own (GEMINI_HOME, ~/.gemini).
fn login_home(runtime: &str, env: &Env) -> Option<std::path::PathBuf> {
    if let Some(t) = crate::model_subscriptions::turn_home(runtime) {
        return t.own(env);
    }
    let var = crate::harness_tools::caps(runtime).profile_home;
    if var.is_empty() {
        return None;
    }
    match env.get(&var).filter(|v| !v.is_empty()) {
        Some(v) => Some(v.into()),
        None if var == "CODEX_HOME" => env
            .get("HOME")
            .filter(|h| !h.is_empty())
            .map(|h| std::path::Path::new(h).join(".codex")),
        None => None,
    }
}

/// Where a Connect of a plan that signs in per turn (model_subscriptions.rs TURN_HOMES) makes its side home: in this
/// runner's own state, so another runner of the same OS user never sees it, nor sweeps it away.
fn plan_sides(state_dir: &std::path::Path) -> std::path::PathBuf {
    crate::isolation::bot_state(state_dir).join("plan-sides")
}

/// At the runner's start, under its lock and before any sign-in: the side homes its Connects of such plans left (it
/// stopped mid sign-in) go, removed as the CLI's user. They hold nothing else: the computer's own login is never in one.
pub fn sweep_plan_sides(state_dir: &std::path::Path) {
    let dir = plan_sides(state_dir).display().to_string();
    let process: Env = std::env::vars().collect();
    if let Err(e) = file_command(&["rm", "-rf", &dir], &process) {
        log(&format!("Tico runner: could not remove {dir}: {e}"));
    }
}

/// A Switch account's own home: a new folder inside the login's home (or `under`, for a plan's Connect), made as the
/// CLI's user, with the manifest's `seed` files copied in. The login command signs in there; the old login is
/// untouched until `adopt_files`.
fn begin_switch(
    runtime: &str,
    id: &str,
    env: &Env,
    under: Option<&std::path::Path>,
) -> Result<(std::path::PathBuf, std::path::PathBuf), String> {
    let login = crate::harness_tools::login_command(runtime)
        .ok_or("Switching accounts is not available")?;
    let caps = crate::harness_tools::caps(runtime);
    if caps
        .profile_home_overrides
        .iter()
        .any(|k| env.get(k).is_some_and(|v| !v.is_empty()))
    {
        return Err("Switching accounts with a custom login folder is not available; sign out and sign in instead".into());
    }
    let home =
        login_home(runtime, env).ok_or("Switching accounts is not available for this runtime")?;
    let parent = under.unwrap_or(&home);
    let side = parent.join(format!(
        ".tico-switch-{}",
        id.chars()
            .filter(|c| c.is_ascii_alphanumeric())
            .take(32)
            .collect::<String>()
    ));
    let (home_s, side_s) = (parent.display().to_string(), side.display().to_string());
    file_command(&["mkdir", "-p", "-m", "700", &home_s], env)?;
    file_command(&["mkdir", "-m", "700", &side_s], env)?;
    let seed = || -> Result<(), String> {
        for rel in &login.seed {
            let from = home.join(rel);
            if std::fs::symlink_metadata(&from).is_ok_and(|m| m.is_file()) {
                let to = side.join(rel);
                if let Some(parent) = to.parent() {
                    file_command(
                        &["mkdir", "-p", "-m", "700", &parent.display().to_string()],
                        env,
                    )?;
                }
                file_command(
                    &[
                        "cp",
                        "-p",
                        &from.display().to_string(),
                        &to.display().to_string(),
                    ],
                    env,
                )?;
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
fn adopt_files(
    files: &[String],
    home: &std::path::Path,
    side: &std::path::Path,
    env: &Env,
) -> Result<Vec<Moved>, String> {
    let old = side.join(".tico-old");
    let mut moved: Vec<Moved> = vec![];
    let mkdir_for = |path: &std::path::Path| match path.parent() {
        Some(parent) => file_command(
            &["mkdir", "-p", "-m", "700", &parent.display().to_string()],
            env,
        ),
        None => Ok(()),
    };
    for rel in files {
        let from = side.join(rel);
        if !std::fs::symlink_metadata(&from).is_ok_and(|m| m.is_file()) {
            continue;
        }
        let to = home.join(rel);
        let step = (|| -> Result<(), String> {
            let had_old = std::fs::symlink_metadata(&to).is_ok();
            if had_old {
                let kept = old.join(rel);
                mkdir_for(&kept)?;
                file_command(
                    &[
                        "mv",
                        "-f",
                        &to.display().to_string(),
                        &kept.display().to_string(),
                    ],
                    env,
                )?;
                moved.push(Moved {
                    to: to.clone(),
                    kept: Some(kept),
                });
            }
            mkdir_for(&to)?;
            // A failed mkdir has not installed anything to undo. In particular, BSD rm -f reports ENOTDIR
            // for a missing file under a plain file, so recording it early would falsely fail the rollback.
            if !had_old {
                moved.push(Moved {
                    to: to.clone(),
                    kept: None,
                });
            }
            file_command(
                &[
                    "mv",
                    "-f",
                    &from.display().to_string(),
                    &to.display().to_string(),
                ],
                env,
            )
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
    if failed.is_empty() {
        Ok(())
    } else {
        Err(failed.join("; "))
    }
}

/// The commit point of a switch or a removal: tell the server it is `finishing` and wait for it to agree. Once it
/// has, a cancel is refused there, so the change can go ahead; a cancel (or a refusal) that came first wins, and so
/// does no answer.
fn confirm_commit(s: &Session, late: &str) -> Result<(), (String, String)> {
    // "cancelled" is the wire's spelling: runners and servers of every release read it (backend/model_login.py WIRE_STATE).
    let canceled = || ("cancelled".to_string(), "Canceled".to_string());
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
        if s.canceled.load(Ordering::SeqCst) {
            return Err(canceled());
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
fn hold_key_logins<'a>(
    s: &Session,
    runner: &'a Runner,
) -> Result<crate::signin::KeyChange<'a>, (String, String)> {
    let Some(held) = runner
        .sign_in
        .begin_key_change(&s.runtime, crate::signin::KEY_LOGIN_DRAIN)
    else {
        return Err((
            "failed".into(),
            format!(
                "{} key is changing on this computer; try again in a minute",
                s.runtime
            ),
        ));
    };
    if !runner
        .sign_in
        .wait_for_key_logins(&s.runtime, crate::signin::KEY_LOGIN_DRAIN)
    {
        return Err((
            "failed".into(),
            format!(
                "{} is still signing in with its key; try again in a minute",
                s.runtime
            ),
        ));
    }
    Ok(held)
}

fn run_session(s: Arc<Session>, runner: Arc<Runner>, config: Config, lifetime: Duration) {
    let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
    let fail = |state: &str, msg: &str| s.finish(state, msg, &runner, &default);
    let Some(login) = crate::harness_tools::login_command(&s.runtime) else {
        return fail("failed", "Browser sign-in is not available");
    };
    let connect = s.kind == "connect";
    // A full login signs in with the CLI's own login command, on the side, and keeps what it wrote there.
    let full = connect && s.full;
    if full && (login.command.is_empty() || !login.files.iter().any(|f| f.ends_with(FULL_FILE))) {
        return fail("failed", "Connect is not available for this tool");
    }
    if connect && !full && login.connect.is_empty() {
        return fail("failed", "Connect is not available for this tool");
    }
    let (exe, mut env, profile) = match login_env(&s.runtime, &config, &s.profile) {
        Ok(found) => found,
        Err(e) => return fail("failed", &e),
    };
    // A switch signs in on the side: the old login keeps working until the new one is in place. So does a switch to
    // a subscription, whose old login is the API key. A Connect whose result is a file signs in there too, and leaves
    // the computer's own login alone for good.
    let real_env = env.clone();
    if connect {
        // The sign-in is for the plan the person picks, whatever key this computer's own login uses.
        for key in crate::harness_tools::api_key_env(&s.runtime)
            .into_iter()
            .chain(crate::signin::headless_names(&s.runtime))
        {
            env.remove(&key);
        }
    }
    let to_subscription = s.kind == "to_subscription";
    let switch = if s.kind == "switch"
        || to_subscription
        || full
        || (connect && !login.connect_file.is_empty())
    {
        let sides = crate::model_subscriptions::turn_home(&s.runtime)
            .filter(|_| connect)
            .map(|_| plan_sides(&config.state_dir()));
        match begin_switch(&s.runtime, &s.id, &env, sides.as_deref()) {
            Ok((home, side)) => {
                let var = match crate::model_subscriptions::turn_home(&s.runtime) {
                    Some(t) => t.var.to_string(),
                    None => crate::harness_tools::caps(&s.runtime).profile_home,
                };
                env.insert(var, side.display().to_string());
                if full {
                    // The seeded settings name the computer's own account: the plan's must come from its sign-in.
                    let seeded = side.join(".claude.json").display().to_string();
                    let _ = file_command(&["rm", "-f", &seeded], &real_env);
                }
                Some((home, side))
            }
            Err(e) => return fail("failed", &e),
        }
    } else {
        None
    };
    let discard = |side: &std::path::Path| {
        if let Err(e) = file_command(&["rm", "-rf", &side.display().to_string()], &real_env) {
            log(&format!(
                "Tico runner: {} switch left {}: {e}",
                s.runtime,
                side.display()
            ));
        }
    };
    let plan_home = crate::model_subscriptions::turn_home(&s.runtime).filter(|_| connect);
    if let Some(t) = plan_home {
        // Signed in as a turn on the plan runs (hosts/antigravity.rs): told it runs over SSH, so the sign-in is the file
        // in its side home and never the computer's keyring, and with no display, so it prints its link.
        for key in t.strip.iter().chain(&["DISPLAY", "WAYLAND_DISPLAY"]) {
            env.remove(*key);
        }
        env.extend(t.env.iter().map(|(k, v)| (k.to_string(), v.to_string())));
    }
    let side_home = switch
        .as_ref()
        .map(|(_, side)| side.display().to_string())
        .unwrap_or_default();
    let connect_argv: Vec<String> = login
        .connect
        .iter()
        .map(|a| a.replace("{home}", &side_home))
        .collect();
    let command = if connect && !full {
        &connect_argv
    } else {
        &login.command
    };
    let token_prefix = if connect && !full {
        login.connect_token.clone()
    } else {
        String::new()
    };
    let (mut child, read_fd, writer) = match spawn(&exe, command, &env, login.terminal) {
        Ok(x) => x,
        Err(e) => {
            if let Some((_, side)) = &switch {
                discard(side);
            }
            return fail(
                "failed",
                &format!("The sign-in command could not start ({:?})", e.kind()),
            );
        }
    };
    s.trace("cli_started", None);
    let (done_tx, done_rx) = std::sync::mpsc::channel::<()>();
    let reader = {
        let s = s.clone();
        let token_prefix = token_prefix.clone();
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
                    i.seen += n;
                    if i.raw.len() > KEEP_RAW {
                        let mut cut = i.raw.len() - KEEP_RAW;
                        while !i.raw.is_char_boundary(cut) {
                            cut += 1;
                        }
                        i.raw.drain(..cut);
                    }
                    (i.raw.clone(), i.typed.clone().unwrap_or_default())
                };
                // A Connect relays its link and code from before the token, and none of what it prints.
                let shown = match raw.find(&token_prefix) {
                    Some(at) if !token_prefix.is_empty() => &raw[..at],
                    _ => raw.as_str(),
                };
                let (url, code, mut lines, _) = parse(shown, &hide);
                if connect {
                    lines.clear();
                }
                let mut i = s.inner.lock().unwrap();
                if (i.url.as_str(), i.code.as_str(), &i.lines)
                    != (url.as_str(), code.as_str(), &lines)
                {
                    i.url = url;
                    i.code = code;
                    i.lines = lines;
                    i.dirty = true;
                }
                if i.state == "starting" && (!i.url.is_empty() || !i.code.is_empty()) {
                    i.state = "waiting".into();
                    i.dirty = true;
                    s.trace("instructions_ready", None);
                }
            }
            let _ = done_tx.send(());
        })
    };
    let started_at = Instant::now();
    let deadline = started_at + lifetime;
    // A Connect's command that printed its token and then would not end: its token still counts.
    let mut token_at = None;
    let mut stopped_after_token = false;
    // After a Connect's code is typed: what the command shows, as a scrubbed line, when it changes (`after_code`).
    let mut watch: Option<AfterCode> = None;
    let mut enter_at: Option<Instant> = None;
    // When an Antigravity Connect's command first printed anything.
    let mut printed_at: Option<Instant> = None;
    let verdict: Option<(String, String)> = loop {
        match child.try_wait() {
            Ok(Some(_)) | Err(_) => break None,
            Ok(None) => {}
        }
        if s.canceled.load(Ordering::SeqCst) {
            kill(&mut child);
            break Some(("cancelled".into(), "Canceled".into()));
        }
        if Instant::now() > deadline {
            s.trace("timed_out", None);
            kill(&mut child);
            if plan_home.is_some() {
                s.why(None);
                break Some(("failed".into(), AGY_EXPIRED.into()));
            }
            break Some((
                "failed".into(),
                "The sign-in was not finished in time".into(),
            ));
        }
        // Antigravity shows Google's link within seconds once it runs (T3 Code's AntigravityAuth hints).
        if plan_home.is_some() {
            let (printed, linked) = {
                let i = s.inner.lock().unwrap();
                (i.seen > 0, !i.url.is_empty())
            };
            if printed && printed_at.is_none() {
                printed_at = Some(Instant::now());
            }
            let stalled = match printed_at {
                _ if linked => None,
                None if started_at.elapsed() > AGY_START => Some(AGY_NOT_STARTED),
                Some(at) if at.elapsed() > AGY_LINK => Some(AGY_NO_LINK),
                _ => None,
            };
            if let Some(message) = stalled {
                s.trace("timed_out", None);
                kill(&mut child);
                s.why(None);
                break Some(("failed".into(), message.into()));
            }
        }
        if connect && oauth_retry(&s.inner.lock().unwrap().raw) {
            let reason = s.why(None);
            kill(&mut child);
            break Some(("failed".into(), failure_message(reason, true).into()));
        }
        // The token is all a Connect wants. The command gets to end by itself, so a failure it reports still counts.
        if !token_prefix.is_empty()
            && printed_token(&s.inner.lock().unwrap().raw, &token_prefix, false).is_some()
            && token_at.get_or_insert_with(Instant::now).elapsed() > TOKEN_GRACE
        {
            kill(&mut child);
            stopped_after_token = true;
            break None;
        }
        if let Some(w) = &writer {
            let code = s.inner.lock().unwrap().pending.take();
            if let Some(code) = code {
                // The code alone; Enter follows on its own write (ENTER_AFTER_CODE).
                let wrote =
                    unsafe { libc::write(w.as_raw_fd(), code.as_ptr() as *const _, code.len()) };
                if wrote == code.len() as isize {
                    let mut i = s.inner.lock().unwrap();
                    i.typed = Some(code);
                    i.taken = true;
                    i.dirty = true;
                    s.trace("code_written", None);
                    watch = connect.then(|| AfterCode::new(i.seen));
                    enter_at = Some(Instant::now() + ENTER_AFTER_CODE);
                } else {
                    s.trace("code_delivery_failed", None);
                    kill(&mut child);
                    break Some((
                        "failed".into(),
                        "The code could not reach the sign-in command. Choose Try again.".into(),
                    ));
                }
            }
        }
        if let (Some(w), Some(at)) = (&writer, enter_at) {
            if Instant::now() >= at {
                enter_at = None;
                let wrote = unsafe { libc::write(w.as_raw_fd(), b"\r".as_ptr() as *const _, 1) };
                if wrote == 1 {
                    s.trace("enter_written", None);
                } else {
                    s.trace("code_delivery_failed", None);
                    kill(&mut child);
                    break Some((
                        "failed".into(),
                        "The code could not reach the sign-in command. Choose Try again.".into(),
                    ));
                }
            }
        }
        if let Some(w) = watch.as_mut() {
            w.check(&s);
        }
        std::thread::sleep(Duration::from_millis(100));
    };
    drop(writer); // the master closes, so the reader ends on a pty too
    let _ = done_rx.recv_timeout(Duration::from_secs(3));
    s.trace(
        "cli_exited",
        child
            .try_wait()
            .ok()
            .flatten()
            .and_then(|status| status.code()),
    );
    // Kept only when the old login's files are still in it, so a failed restore never deletes them.
    let mut keep_side = false;
    let mut captured = false;
    let (state, message) = match verdict {
        Some(v) => v,
        None if s.canceled.load(Ordering::SeqCst) => ("cancelled".into(), "Canceled".into()),
        None if connect => {
            let status = child.wait().ok();
            let finished = status.is_some_and(|st| st.success());
            // Why Antigravity's own check refused a sign-in its command finished.
            let mut checked = None;
            let value = match &switch {
                _ if !finished && !stopped_after_token => None,
                Some((_, side)) if full => full_login(side, &real_env),
                // A sign-in a turn reads from its own home only counts once a session starts on it there, within what
                // is left of the sign-in's time.
                Some((_, side)) if plan_home.is_some() => match agy_session(
                    &exe,
                    side,
                    &env,
                    deadline.saturating_duration_since(Instant::now()),
                ) {
                    Ok(()) => {
                        crate::model_subscriptions::read_codex_auth(&side.join(&login.connect_file))
                    }
                    Err(why) => {
                        checked = Some(why);
                        None
                    }
                },
                // A plain file, read bounded and never through a link (a pipe or link there stalls nothing).
                Some((_, side)) => {
                    crate::model_subscriptions::read_codex_auth(&side.join(&login.connect_file))
                }
                None => printed_token(&s.inner.lock().unwrap().raw, &token_prefix, true),
            };
            match value {
                Some(value) => {
                    s.inner.lock().unwrap().captured = Some(value);
                    captured = true;
                    s.trace("credential_captured", None);
                    (String::new(), String::new())
                }
                None => {
                    // Why: the last screen since the code went in, scrubbed, and its category.
                    let since = watch
                        .as_ref()
                        .map(|w| s.inner.lock().unwrap().seen.saturating_sub(w.seen_at));
                    let reason = s.why(since);
                    let message = if plan_home.is_some() {
                        agy_failure(
                            &s.inner.lock().unwrap().raw,
                            checked.as_deref(),
                            status.and_then(|st| st.code()),
                        )
                    } else {
                        failure_message(reason, false).into()
                    };
                    ("failed".into(), message)
                }
            }
        }
        None if !child.wait().map(|st| st.success()).unwrap_or(false) => (
            "failed".into(),
            "The sign-in command stopped without signing in".into(),
        ),
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
            let headless =
                profile.is_none() && runner.sign_in.headless_secret(&s.runtime).is_some();
            match signed_in(&s.runtime, &exe, &env, headless) {
                (true, detail) => (
                    "signed_in".into(),
                    if detail.is_empty() {
                        "Signed in".into()
                    } else {
                        detail
                    },
                ),
                _ => (
                    "failed".into(),
                    format!(
                        "The command finished but {} is still not signed in",
                        s.runtime
                    ),
                ),
            }
        }
    };
    if let Some((_, side)) = &switch {
        if !keep_side {
            discard(side);
        }
    }
    if state == "signed_in" {
        // The new account's email, plan, usage windows and fallback hold are read afresh, not the old one's, after a
        // switch or a plain sign-in alike; a read of the old account still running is then not kept
        // (accounts::keep_if_current). With the account forgotten, remember() can no longer see the change of account,
        // so the windows it would have dropped are dropped here.
        let name = profile.as_ref().map(|p| p.name.clone()).unwrap_or_default();
        crate::accounts::forget(&name, &s.runtime);
        crate::profiles::forget_windows(&name, &s.runtime);
    }
    // A captured Connect ends once the server has taken it (`Logins::flush`).
    if !captured {
        s.finish(&state, &message, &runner, &default);
    }
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
    let ran = model_command(exe, &args, env)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
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
/// then the CLI's own logout runs and the sign-in is checked again. Only a confirmed existing login clears the mark.
fn run_sign_out(s: Arc<Session>, runner: Arc<Runner>, config: Config) {
    let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
    let fail = |msg: &str| s.finish("failed", msg, &runner, &default);
    let logout = crate::harness_tools::login_command(&s.runtime)
        .map(|l| l.logout)
        .unwrap_or_default();
    if logout.is_empty() {
        return fail(&format!(
            "Signing out from the app is not available for {}",
            s.runtime
        ));
    }
    let (exe, env, profile) = match login_env(&s.runtime, &config, &s.profile) {
        Ok(found) => found,
        Err(e) => return fail(&e),
    };
    let own = s.profile.is_empty();
    if own {
        if let Err(e) = runner.sign_in.mark_signed_out(&s.runtime) {
            return fail(&format!(
                "Could not record the sign-out on this computer ({:?})",
                e.kind()
            ));
        }
        // A key login that started before the mark could still be writing a login: let it end before logging out,
        // or it would write the login back after the check below said signed out.
        if !runner
            .sign_in
            .wait_for_key_logins(&s.runtime, crate::signin::KEY_LOGIN_DRAIN)
        {
            let _ = runner.sign_in.unmark_signed_out(&s.runtime);
            return fail(&format!(
                "{} is still signing in with a key on this computer; try again in a minute",
                s.runtime
            ));
        }
    }
    log_out(&exe, &logout, &env);
    match sign_in_status(&s.runtime, &exe, &env) {
        None => {
            return fail(&format!(
                "The logout finished but could not confirm whether {} signed out",
                s.runtime
            ))
        }
        Some((true, _)) => {
            if own {
                let _ = runner.sign_in.unmark_signed_out(&s.runtime);
            }
            return fail(&format!(
                "The logout finished but {} is still signed in",
                s.runtime
            ));
        }
        Some((false, _)) => {}
    }
    let account = profile.as_ref().map(|p| p.name.clone()).unwrap_or_default();
    crate::accounts::forget(&account, &s.runtime);
    crate::profiles::forget_windows(&account, &s.runtime);
    s.finish("signed_out", "Signed out", &runner, &default);
}

/// The CLI and environment the computer's own key login runs with (signin.rs key_login), for a clear with no job.
pub fn key_cli(runtime: &str) -> Option<(String, Env)> {
    let exe = crate::env::which(
        &crate::harness_tools::executable_for(runtime),
        &std::env::var("PATH").unwrap_or_default(),
    )?;
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
pub fn clear_key(
    sign: &crate::signin::SignIn,
    runtime: &str,
    cli: Option<(&str, &Env)>,
) -> Result<(), String> {
    sign.forget_keys(runtime)
        .map_err(|e| format!("Could not clear the key on this computer ({:?})", e.kind()))?;
    if !sign.wait_for_key_logins(runtime, crate::signin::KEY_LOGIN_DRAIN) {
        return Err(format!(
            "{runtime} is still signing in with a key on this computer; try again in a minute"
        ));
    }
    let logout = crate::harness_tools::login_command(runtime)
        .map(|l| l.logout)
        .unwrap_or_default();
    if crate::harness_tools::caps(runtime).api_key_login.is_empty() || logout.is_empty() {
        return Ok(());
    }
    let Some((exe, env)) = cli else { return Ok(()) };
    match signed_in(runtime, exe, env, false) {
        (true, detail) if by_key(&detail) => log_out(exe, &logout, env),
        _ => return Ok(()),
    }
    if signed_in(runtime, exe, env, false).0 {
        return Err(format!(
            "The key was removed but {runtime} is still signed in"
        ));
    }
    Ok(())
}

/// A clear_key job: the computer's own key for `runtime` is gone, and no key on this computer signs it in again
/// until a person adds one or signs in.
fn run_clear_key(s: Arc<Session>, runner: Arc<Runner>, config: Config) {
    let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
    let cli = login_env(&s.runtime, &config, "").ok();
    match clear_key(
        &runner.sign_in,
        &s.runtime,
        cli.as_ref().map(|(exe, env, _)| (exe.as_str(), env)),
    ) {
        Ok(()) => {
            let account = cli
                .and_then(|(_, _, p)| p)
                .map(|p| p.name)
                .unwrap_or_default();
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
    let Ok(mut child) = model_command(exe, &argv, env)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    else {
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
fn key_works(
    exe: &str,
    caps: &crate::harness_tools::Capabilities,
    names: &[String],
    new: &Env,
    env: &Env,
    side: &std::path::Path,
) -> bool {
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
    let Ok(mut child) = model_command(exe, &args, &env)
        .current_dir(dir)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    else {
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
    let Ok(out) = child.wait_with_output() else {
        return false;
    };
    let said = String::from_utf8_lossy(&out.stdout);
    let error = said
        .lines()
        .chain([said.trim()])
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .any(|v| v["is_error"] == true);
    out.status.success() && !error
}

/// A use_key job: the key a person just added in the app for this computer takes the place of its own key for
/// `runtime`. Fetched for this job by its id, never sent in the queue. A CLI that turns the key into a login of its own
/// signs in with it on the side, as a switch does; until it has and Tico agrees it goes in (`finishing`), nothing on
/// this computer changes, so a key that does not work, a cancel or a check that runs out leaves the old key and login
/// as they were. The regular key fetch and key login wait meanwhile. Once in, the old key from the terminal goes.
fn run_use_key(
    s: Arc<Session>,
    runner: Arc<Runner>,
    config: Config,
    handle: tokio::runtime::Handle,
) {
    let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
    let sign = &runner.sign_in;
    let finish = |state: &str, message: &str| s.finish(state, message, &runner, &default);
    let to_key = s.kind == "to_key";
    let left = || {
        if to_key {
            "the subscription is still in use"
        } else if sign.headless_secret(&s.runtime).is_some() {
            "the old key is still in use"
        } else {
            "still no key"
        }
    };
    if to_key
        && crate::harness_tools::login_command(&s.runtime)
            .is_some_and(|l| l.keychain.iter().any(|p| p == crate::runner::platform()))
    {
        // The subscription's login is in the system keychain here, not in files: a logout that half worked could not
        // be put back.
        let msg = format!("Switching to an API key isn't available for {} on this computer: sign out, then add a key", s.runtime);
        return finish("failed", &msg);
    }
    let Some(change) = sign.begin_key_change(&s.runtime, crate::signin::KEY_LOGIN_DRAIN) else {
        return finish(
            "failed",
            &format!(
                "{} key is already changing on this computer; try again in a minute",
                s.runtime
            ),
        );
    };
    if !sign.wait_for_key_logins(&s.runtime, crate::signin::KEY_LOGIN_DRAIN) {
        return finish(
            "failed",
            &format!(
                "{} is still signing in with a key on this computer; try again in a minute",
                s.runtime
            ),
        );
    }
    let names = crate::signin::headless_names(&s.runtime);
    let query = [("runtime", s.runtime.clone()), ("login", s.id.clone())];
    let reply = handle
        .block_on(runner.client.get_query("runner-model-credentials", &query))
        .unwrap_or_default();
    let mut new = Env::new();
    for row in reply["credentials"].as_array().cloned().unwrap_or_default() {
        let (k, v) = (
            text_of(&row["env"]),
            text_of(&row["value"]).trim().to_string(),
        );
        if row["source"] == "computer"
            && names.contains(&k)
            && !v.is_empty()
            && !v.contains(['\r', '\n', '\0'])
        {
            new.insert(k, v);
        }
    }
    let Some(key) = new.values().next().cloned() else {
        return finish("failed", "The new key was not found; add it again");
    };
    let caps = crate::harness_tools::caps(&s.runtime);
    let files = crate::harness_tools::login_command(&s.runtime)
        .map(|l| l.files)
        .unwrap_or_default();
    // (executable, the login's environment, its home, the side home it signed in on with the new key)
    let mut side = None;
    if !caps.api_key_login.is_empty() {
        let (exe, env, _) = match login_env(&s.runtime, &config, "") {
            Ok(found) => found,
            Err(e) => return finish("failed", &format!("{e}; {}", left())),
        };
        let (home, dir) = match begin_switch(&s.runtime, &s.id, &env, None) {
            Ok(found) => found,
            Err(e) => return finish("failed", &format!("{e}; {}", left())),
        };
        let mut on_side = env.clone();
        on_side.insert(caps.profile_home.clone(), dir.display().to_string());
        let works = login_with_key(&exe, &caps.api_key_login, &key, &on_side)
            && matches!(signed_in(&s.runtime, &exe, &on_side, false), (true, d) if by_key(&d));
        side = Some((exe, env, home, dir));
        if !works {
            discard_side(&s.runtime, side.as_ref());
            return finish(
                "failed",
                &format!("The new key did not sign {} in; {}", s.runtime, left()),
            );
        }
    } else if to_key {
        // A CLI that reads its key from the environment: the provider is asked about the key in a side home with no
        // login, so the subscription cannot answer for it. The side home then keeps the subscription's login files
        // while it is signed out (to_key_in_place).
        if caps.api_key_check.is_empty() {
            return finish(
                "failed",
                &format!("{} cannot check a key from here; {}", s.runtime, left()),
            );
        }
        let (exe, env, _) = match login_env(&s.runtime, &config, "") {
            Ok(found) => found,
            Err(e) => return finish("failed", &format!("{e}; {}", left())),
        };
        let (home, dir) = match begin_switch(&s.runtime, &s.id, &env, None) {
            Ok(found) => found,
            Err(e) => return finish("failed", &format!("{e}; {}", left())),
        };
        let works = key_works(&exe, &caps, &names, &new, &env, &dir);
        side = Some((exe, env, home, dir));
        if !works {
            discard_side(&s.runtime, side.as_ref());
            return finish(
                "failed",
                &format!("The new key did not work for {}; {}", s.runtime, left()),
            );
        }
    }
    if let Err((state, message)) = confirm_commit(&s, "Tico did not confirm the new key in time") {
        discard_side(&s.runtime, side.as_ref());
        return finish(
            &state,
            &if state == "cancelled" {
                message
            } else {
                format!("{message}; {}", left())
            },
        );
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
                    log(&format!(
                        "Tico runner: {} old login could not be put back: {e}",
                        s.runtime
                    ));
                }
                false
            }
            Err(e) => {
                log(&format!(
                    "Tico runner: {} new key login could not be put in place: {e}",
                    s.runtime
                ));
                false
            }
        };
    }
    discard_side(&s.runtime, side.as_ref());
    let saved = sign
        .set_computer_keys(&s.runtime, &new)
        .and_then(|_| sign.unmark_signed_out(&s.runtime))
        .and_then(|_| sign.unmark_key_off(&s.runtime));
    if let Err(e) = sign.forget_hand_keys(&s.runtime) {
        log(&format!(
            "Tico runner: {} old key could not be deleted from _shared.env ({:?})",
            s.runtime,
            e.kind()
        ));
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
            return finish(
                "failed",
                &format!(
                    "The new key is saved but did not sign {} in here",
                    s.runtime
                ),
            );
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
fn to_key_in_place(
    s: &Session,
    sign: &crate::signin::SignIn,
    config: &Config,
    new: &Env,
    side: Option<&(String, Env, std::path::PathBuf, std::path::PathBuf)>,
    files: &[String],
) -> (String, String) {
    let failed = |why: String| ("failed".to_string(), why);
    let before = match sign.keys_before(&s.runtime) {
        Ok(found) => found,
        Err(e) => {
            discard_side(&s.runtime, side);
            return failed(format!(
                "Could not read this computer's keys ({:?}); the subscription is still in use",
                e.kind()
            ));
        }
    };
    let Some((exe, env, home, dir)) = side else {
        return failed("The new key was not checked; the subscription is still in use".into());
    };
    let (exe, env) = (exe.clone(), env.clone());
    let profile = login_env(&s.runtime, config, "")
        .ok()
        .and_then(|(_, _, p)| p);
    // A CLI that turns the key into a login of its own (Codex) puts it in place of the subscription's; one that reads
    // the key from the environment (Claude Code) keeps its subscription's login files aside until it is signed out.
    let adopt = !crate::harness_tools::caps(&s.runtime)
        .api_key_login
        .is_empty();
    // What `undo` puts back: the files the key's login replaced, or the subscription's files kept aside.
    let moved = std::cell::RefCell::new(vec![]);
    if adopt {
        match adopt_files(files, home, dir, &env) {
            Ok(m) => *moved.borrow_mut() = m,
            Err(e) => {
                if !e.contains("could not be put back") {
                    discard_side(&s.runtime, side);
                }
                return failed(
                    e.replace("The new account", "The new key's login")
                        .replace("the old one", "the subscription"),
                );
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
    let saved = sign
        .set_computer_keys(&s.runtime, new)
        .and_then(|_| sign.unmark_signed_out(&s.runtime))
        .and_then(|_| sign.unmark_key_off(&s.runtime))
        .and_then(|_| sign.forget_hand_keys(&s.runtime));
    if let Err(e) = saved {
        return undo(format!(
            "The new key could not be saved on this computer ({:?})",
            e.kind()
        ));
    }
    let logout = crate::harness_tools::login_command(&s.runtime)
        .map(|l| l.logout)
        .unwrap_or_default();
    // A status check that gives no answer (it could not run, timed out or said something unreadable) is not "signed
    // out": the switch is undone rather than taken on trust.
    let status = || sign_in_status(&s.runtime, &exe, &env);
    match status() {
        // One login file: the key's login is the login now, and the subscription's went with the old file.
        Some((true, detail)) if by_key(&detail) => {}
        _ if adopt => {
            return undo(format!(
                "The new key's login did not work in place on {}",
                s.runtime
            ))
        }
        None => {
            return undo(format!(
                "Could not check whether {} is signed in to its subscription",
                s.runtime
            ))
        }
        Some((true, _)) if logout.is_empty() => {
            return undo(format!(
                "{} cannot sign its subscription out from here",
                s.runtime
            ))
        }
        Some((true, _)) => {
            // Everything the logout may change is kept aside first, so a logout that half worked is put back whole.
            match keep_aside(files, home, dir, &env) {
                Ok(kept) => *moved.borrow_mut() = kept,
                Err(e) => {
                    return undo(format!(
                        "The subscription's login could not be kept aside ({e})"
                    ))
                }
            }
            log_out(&exe, &logout, &env);
            match status() {
                Some((true, d)) if !by_key(&d) => {
                    return undo(format!(
                        "The subscription could not be signed out on {}",
                        s.runtime
                    ))
                }
                None => {
                    return undo(format!(
                        "Could not check that {} signed its subscription out",
                        s.runtime
                    ))
                }
                _ => {}
            }
        }
        // Nothing signed in apart from the key: nothing to sign out.
        Some((false, _)) => {}
    }
    discard_side(&s.runtime, side);
    crate::accounts::forget(&profile.map(|p| p.name).unwrap_or_default(), &s.runtime);
    (
        "done".into(),
        "Switched to the API key; the subscription is signed out".into(),
    )
}

/// Copy each of a login's `files` under `home` into the side home `dir` (`.tico-old`, as `adopt_files` keeps them), so
/// `restore` puts them back as they were; one that was not there is removed again.
fn keep_aside(
    files: &[String],
    home: &std::path::Path,
    dir: &std::path::Path,
    env: &Env,
) -> Result<Vec<Moved>, String> {
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
            file_command(
                &["mkdir", "-p", "-m", "700", &parent.display().to_string()],
                env,
            )?;
        }
        file_command(
            &[
                "cp",
                "-p",
                &to.display().to_string(),
                &copy.display().to_string(),
            ],
            env,
        )?;
        kept.push(Moved {
            to,
            kept: Some(copy),
        });
    }
    Ok(kept)
}

/// Delete the side home a use_key job signed in on, if it made one.
fn discard_side(
    runtime: &str,
    side: Option<&(String, Env, std::path::PathBuf, std::path::PathBuf)>,
) {
    if let Some((_, env, _, dir)) = side {
        if let Err(e) = file_command(&["rm", "-rf", &dir.display().to_string()], env) {
            log(&format!(
                "Tico runner: {runtime} new key left {}: {e}",
                dir.display()
            ));
        }
    }
}

/// Whether `runtime` is surely not signed in on `profile`: its home holds no file but what Tico put there when it
/// made the folder, or its status check says signed out. A check that could not run (the CLI is gone, it timed
/// out) is no answer, so its login is taken to be there.
fn surely_signed_out(
    runtime: &str,
    config: &Config,
    profile: &crate::profiles::Profile,
    env: &Env,
) -> bool {
    if profile
        .home(runtime)
        .is_some_and(|home| login_files(&home, env) == Some(false))
    {
        return true;
    }
    login_env(runtime, config, &profile.name)
        .ok()
        .and_then(|(exe, env, _)| sign_in_status(runtime, &exe, &env))
        .is_some_and(|(on, _)| !on)
}

/// Whether `home` holds any file other than the .gitconfig Tico copies in, looked at as the CLI's user (under
/// isolation the home is that user's). None when it could not be looked at.
fn login_files(home: &std::path::Path, env: &Env) -> Option<bool> {
    if !home.exists() {
        return Some(false);
    }
    let (dir, gitconfig) = (
        home.display().to_string(),
        home.join(".gitconfig").display().to_string(),
    );
    let out = model_command(
        "find",
        &[
            &dir, "!", "-type", "d", "!", "-path", &gitconfig, "-print", "-quit",
        ],
        env,
    )
    .stdin(Stdio::null())
    .stdout(Stdio::piped())
    .stderr(Stdio::null())
    .output()
    .ok()?;
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
        return fail(&format!(
            "Subscription {} is this computer's own login; sign it out instead",
            s.profile
        ));
    }
    let (exe, env, profile) = match login_env(&s.runtime, &config, &s.profile) {
        Ok((exe, env, Some(profile))) => (exe, env, profile),
        Ok(_) => {
            return fail(&format!(
                "Subscription {} is not on this computer",
                s.profile
            ))
        }
        Err(e) => return fail(&e),
    };
    if let Err((state, msg)) = confirm_commit(
        &s,
        "Tico did not confirm the removal in time, so nothing was removed",
    ) {
        return s.finish(&state, &msg, &runner, &default);
    }
    if sign_in_status(&s.runtime, &exe, &env).is_none_or(|(on, _)| on) {
        let logout = crate::harness_tools::login_command(&s.runtime)
            .map(|l| l.logout)
            .unwrap_or_default();
        if logout.is_empty() {
            return fail(&format!(
                "Removing a subscription from the app is not available for {}",
                s.runtime
            ));
        }
        let args: Vec<&str> = logout.iter().map(String::as_str).collect();
        if let Ok(mut child) = model_command(&exe, &args, &env)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        {
            let until = Instant::now() + LOGOUT_TIMEOUT;
            while !matches!(child.try_wait(), Ok(Some(_)) | Err(_)) {
                if Instant::now() > until {
                    kill(&mut child);
                    break;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        }
        match sign_in_status(&s.runtime, &exe, &env) {
            None => {
                return fail(&format!(
                    "The logout finished but could not confirm whether {} signed out on {}",
                    s.runtime, s.profile
                ))
            }
            Some((true, _)) => {
                return fail(&format!(
                    "The logout finished but {} is still signed in on {}",
                    s.runtime, s.profile
                ))
            }
            Some((false, _)) => {}
        }
    }
    crate::accounts::forget(&profile.name, &s.runtime);
    crate::profiles::forget_windows(&profile.name, &s.runtime);
    // Another tool that may still be signed in on it keeps the subscription; only this tool's login went.
    let stays: Vec<String> = crate::harness_tools::profile_runtimes()
        .into_iter()
        .filter(|r| *r != s.runtime && !surely_signed_out(r, &config, &profile, &env))
        .map(|r| crate::harness_tools::name_for(&r))
        .collect();
    if !stays.is_empty() {
        let msg = format!(
            "{} signed out of {}; it stays on this computer for {}",
            s.runtime,
            s.profile,
            stays.join(" and ")
        );
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
                log(&format!(
                    "Tico runner: subscription {} unlisted; its folder is not Tico's to delete",
                    s.profile
                ));
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
        Logins {
            sessions: HashMap::new(),
            polled: None,
            lifetime: Duration::from_secs(LIFETIME_S),
        }
    }
}

impl Logins {
    pub fn busy(&self) -> bool {
        !self.sessions.is_empty()
    }

    fn begin(&mut self, runner: &Arc<Runner>, work: &Value) {
        let s = Session::new(work);
        // Read again: a profile `profile add` (or a sign-in) made since start is in the file, not in memory.
        let config = runner.config_now();
        let default = profile_for(&config, "").map(|p| p.name).unwrap_or_default();
        let named = (!s.profile.is_empty())
            .then(|| profile_for(&config, &s.profile))
            .flatten();
        // A key job runs for a runtime with no browser sign-in too, and only on the computer's own login.
        let key_job = ["clear_key", "use_key", "to_key"].contains(&s.kind.as_str());
        if !s.profile.is_empty()
            && ["sign_in", "switch", "to_subscription", "connect"].contains(&s.kind.as_str())
        {
            // One login per tool per computer, used by all its bots: no named subscription signs in any more.
            s.finish("failed", "Each computer has one login per AI tool; sign in the computer's login, or add another runner for a different account", runner, &default);
        } else if key_job && !s.profile.is_empty() {
            s.finish(
                "failed",
                &format!(
                    "Subscription {} holds no key of this computer's; only its own login has one",
                    s.profile
                ),
                runner,
                &default,
            );
        } else if key_job && crate::signin::headless_names(&s.runtime).is_empty() {
            s.finish(
                "failed",
                &format!("This computer does not hold a key for {}", s.runtime),
                runner,
                &default,
            );
        } else if key_job
            && self
                .sessions
                .values()
                .any(|o| o.live() && o.runtime == s.runtime && o.profile == s.profile)
        {
            s.finish(
                "failed",
                &format!("Another sign-in is already running for {}", s.runtime),
                runner,
                &default,
            );
        } else if s.kind == "clear_key" {
            let (s2, r2) = (s.clone(), runner.clone());
            std::thread::spawn(move || run_clear_key(s2, r2, config));
        } else if s.kind == "use_key" || s.kind == "to_key" {
            match tokio::runtime::Handle::try_current() {
                Ok(handle) => {
                    let (s2, r2) = (s.clone(), runner.clone());
                    std::thread::spawn(move || run_use_key(s2, r2, config, handle));
                }
                Err(_) => s.finish(
                    "failed",
                    "The runner could not fetch the new key",
                    runner,
                    &default,
                ),
            }
        } else if crate::harness_tools::login_command(&s.runtime).is_none() {
            s.finish(
                "failed",
                &format!("Browser sign-in is not available for {}", s.runtime),
                runner,
                &default,
            );
        } else if ![
            "sign_in",
            "switch",
            "sign_out",
            "use_shared_key",
            "to_subscription",
            "remove",
            "connect",
        ]
        .contains(&s.kind.as_str())
        {
            // Never a sign-in in place of something this runner does not know.
            s.finish(
                "failed",
                &format!(
                    "Update this computer's runner to {}",
                    s.kind.replace('_', " ")
                ),
                runner,
                &default,
            );
        } else if self
            .sessions
            .values()
            .any(|o| o.live() && o.runtime == s.runtime && o.profile == s.profile)
        {
            s.finish(
                "failed",
                &format!("Another sign-in is already running for {}", s.runtime),
                runner,
                &default,
            );
        } else if !s.profile.is_empty()
            && self.sessions.values().any(|o| {
                o.live() && o.profile == s.profile && (o.kind == "remove" || s.kind == "remove")
            })
        {
            // A removal may delete the subscription's whole folder: nothing else runs on that subscription meanwhile.
            s.finish(
                "failed",
                &format!(
                    "Something else is running on {}; try again when it ends",
                    s.profile
                ),
                runner,
                &default,
            );
        } else if named.as_ref().is_some_and(|p| p.share_operator)
            && !crate::harness_tools::caps(&s.runtime).own_config_home
        {
            // A host with its own config folder never uses the operator's login, so a shared profile still signs in to Tico's own.
            s.finish(
                "failed",
                &format!(
                    "Profile {} shares the operator's own logins; sign in normally",
                    s.profile
                ),
                runner,
                &default,
            );
        } else if (s.kind == "switch" || s.kind == "to_subscription")
            && crate::harness_tools::login_command(&s.runtime)
                .is_some_and(|l| l.keychain.iter().any(|p| p == crate::runner::platform()))
        {
            // The login is in the system keychain here, not in files: there is no side to sign in on.
            let msg = format!("Switching accounts isn't available for {} on this computer: sign out, then sign in", s.runtime);
            s.finish("failed", &msg, runner, &default);
        } else if s.kind == "use_shared_key" {
            let state = match runner
                .sign_in
                .unmark_signed_out(&s.runtime)
                .and_then(|()| runner.sign_in.unmark_key_off(&s.runtime))
            {
                Ok(()) => ("done", "The shared key signs it in again".to_string()),
                Err(e) => (
                    "failed",
                    format!("Could not change this computer's sign-out ({:?})", e.kind()),
                ),
            };
            s.finish(state.0, &state.1, runner, &default);
        } else if s.kind == "remove" && named.is_none() {
            s.finish(
                "failed",
                &format!("Subscription {} is not on this computer", s.profile),
                runner,
                &default,
            );
        } else if s.kind == "remove" {
            let (s2, r2) = (s.clone(), runner.clone());
            std::thread::spawn(move || run_remove(s2, r2, config));
        } else if s.kind == "sign_out" && !s.profile.is_empty() && named.is_none() {
            s.finish(
                "failed",
                &format!("Subscription {} is not on this computer", s.profile),
                runner,
                &default,
            );
        } else if s.kind == "sign_out" {
            let (s2, r2) = (s.clone(), runner.clone());
            std::thread::spawn(move || run_sign_out(s2, r2, config));
        } else {
            let (s2, r2, lifetime) = (s.clone(), runner.clone(), self.lifetime);
            std::thread::spawn(move || run_session(s2, r2, config, lifetime));
        }
        self.sessions.insert(s.id.clone(), s);
    }

    /// Ask the server what to run, then say what happened.
    pub async fn poll(
        &mut self,
        runner: &Arc<Runner>,
        force: bool,
    ) -> Result<(), crate::client::ApiError> {
        if !force
            && self
                .polled
                .is_some_and(|t| t.elapsed().as_secs_f64() < POLL_S)
        {
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
                s.canceled.store(true, Ordering::SeqCst); // canceled or expired on the server
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
            Self::send_captured(&s, runner).await;
            let body = s.report().filter(|b| b["state"] != "cancelled"); // the server ended it: nothing to tell it
            if let Some(body) = body {
                let reply = match runner
                    .client
                    .post(&format!("runner-logins/{id}/report"), &body, None)
                    .await
                {
                    Ok(reply) => reply,
                    Err(e) => {
                        if !e.retryable && e.status == 422 && body.get("trace").is_some() {
                            s.diag.lock().unwrap().refused = true;
                        }
                        s.inner.lock().unwrap().dirty = true; // said again on the next poll
                        continue;
                    }
                };
                // A switch goes ahead only once the server holds it as finishing; if it had already ended there
                // (canceled, expired), the runner stops too.
                match text_of(&reply["state"]).as_str() {
                    "finishing" if body["state"] == "finishing" => {
                        s.confirmed.store(true, Ordering::SeqCst)
                    }
                    // A removal Tico refused at its commit point (a bot runs on it now) stops before anything is removed.
                    "cancelled" | "expired" | "failed" if s.live() => {
                        s.canceled.store(true, Ordering::SeqCst)
                    }
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

    /// A Connect's result goes to the server once, and is dropped here as soon as the server has answered for it. A
    /// connection that fails keeps it for the next poll, until the server has let the job go (`cancelled`).
    async fn send_captured(s: &Arc<Session>, runner: &Arc<Runner>) {
        let Some(value) = s.inner.lock().unwrap().captured.clone() else {
            return;
        };
        let path = format!("runner-logins/{}/subscription", s.id);
        let verdict = if s.canceled.load(Ordering::SeqCst) {
            ("cancelled".to_string(), "Canceled".to_string())
        } else {
            match runner
                .client
                .post(
                    &path,
                    &json!({"value": value}),
                    Some(&format!("connect:{}", s.id)),
                )
                .await
            {
                Ok(reply) => (text_of(&reply["state"]), text_of(&reply["message"])),
                Err(e) if e.retryable => {
                    let mut i = s.inner.lock().unwrap();
                    if !i.save_retry_logged {
                        s.trace("save_temporarily_failed", None);
                        i.save_retry_logged = true;
                    }
                    return;
                }
                Err(e) => {
                    s.trace("save_failed", None);
                    ("failed".to_string(), redact(&e.detail))
                }
            }
        };
        s.inner.lock().unwrap().captured = None;
        s.finish(
            if verdict.0 == "done" {
                "done"
            } else if verdict.0 == "cancelled" {
                "cancelled"
            } else {
                "failed"
            },
            &verdict.1,
            runner,
            "",
        );
    }

    pub fn stop(&self) {
        for s in self.sessions.values() {
            s.canceled.store(true, Ordering::SeqCst);
        }
    }
}

#[cfg(test)]
#[path = "login_tests.rs"]
mod tests;
