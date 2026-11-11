//! What the runner needs from the real `claude` and `codex` CLIs, checked against the CLIs themselves.
//!
//! Every other test drives a fake `claude` or `codex` script. The product drives the real ones, which change every
//! week, and a cloud computer installs the newest (`[update] policy = "latest"`, docs/cloud-machines.md). These tests
//! run whichever `claude` and `codex` are on PATH, in an empty home, up to authentication and never through it, and
//! read what they print with the runner's own code: `login::parse`, the harness manifests, `hosts::is_auth_rejected`.
//!
//! One command, on any machine with both CLIs: `scripts/cli_contract.sh` (docs/harnesses.md, Contract check).
//! They are `#[ignore]`d so no ordinary test run starts a real CLI or reaches a provider.
//!
//! No sign-in is ever finished: `claude setup-token` gets a made-up code, `codex login --device-auth` is stopped
//! as soon as it has shown its link and code. A failure never prints a link, a code or a token: only `parse`'s
//! scrubbed lines.
//!
//! `login.rs` keeps `spawn`, `oauth_retry` and `oauth_failure` private, so the terminal here is opened the way
//! `login::spawn` opens it (50 rows, 500 columns, no echo) and the retry screen is read from `parse`'s lines with the
//! words `oauth_retry` looks for. Declared from `login.rs` this file could call them directly.

use std::io::{BufRead, BufReader, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{json, Value};

use crate::env::Env;
use crate::harness_tools::{self, Manifest};
use crate::login::parse;

/// Never a real code. Claude's codes are `code#state`.
const MADE_UP_CODE: &str = "tico-contract-not-a-code#tico-contract-not-a-state";

/// An empty home for one test, with `open` and `xdg-open` that do nothing so no CLI opens a browser.
struct Home(PathBuf);

impl Home {
    fn new() -> Home {
        let root = std::env::temp_dir().join(format!(
            "tico-cli-contract-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(root.join("bin")).unwrap();
        std::fs::create_dir_all(root.join(".codex")).unwrap();
        std::fs::create_dir_all(root.join("work")).unwrap();
        for name in ["open", "xdg-open", "no-browser"] {
            crate::util::write_script(&root.join("bin").join(name), "#!/bin/sh\nexit 0\n");
        }
        Home(root)
    }

    fn env(&self) -> Env {
        let path = std::env::var("PATH").unwrap_or_default();
        Env::from([
            (
                "PATH".to_string(),
                format!("{}:{path}", self.0.join("bin").display()),
            ),
            ("HOME".to_string(), self.0.display().to_string()),
            (
                "CODEX_HOME".to_string(),
                self.0.join(".codex").display().to_string(),
            ),
            (
                "BROWSER".to_string(),
                self.0.join("bin/no-browser").display().to_string(),
            ),
            ("TERM".to_string(), "xterm-256color".to_string()),
        ])
    }

    fn command(&self, exe: &str, args: &[String]) -> Command {
        let mut c = Command::new(exe);
        c.args(args)
            .env_clear()
            .envs(self.env())
            .current_dir(self.0.join("work"));
        c
    }

    /// The command's exit code and everything it printed, or a failure naming the command when it is not
    /// installed or does not end in `secs`.
    fn run(&self, exe: &str, args: &[String], stdin: &str, secs: u64) -> (Option<i32>, String) {
        let mut c = self.command(exe, args);
        c.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = c.spawn().unwrap_or_else(|e| {
            panic!(
                "`{exe}` could not start ({e}): the contract check needs the real `{exe}` on PATH"
            )
        });
        let mut input = child.stdin.take().unwrap();
        input.write_all(stdin.as_bytes()).ok();
        drop(input);
        let read = |mut from: Box<dyn Read + Send>| {
            std::thread::spawn(move || {
                let mut text = String::new();
                let mut bytes = vec![];
                from.read_to_end(&mut bytes).ok();
                text.push_str(&String::from_utf8_lossy(&bytes));
                text
            })
        };
        let out = read(Box::new(child.stdout.take().unwrap()));
        let err = read(Box::new(child.stderr.take().unwrap()));
        let until = Instant::now() + Duration::from_secs(secs);
        let code = loop {
            match child.try_wait() {
                Ok(Some(status)) => break status.code(),
                Ok(None) if Instant::now() < until => std::thread::sleep(Duration::from_millis(50)),
                _ => {
                    stop(&mut child);
                    panic!("`{exe} {}` did not end in {secs} s", args.join(" "));
                }
            }
        };
        (
            code,
            format!("{}\n{}", out.join().unwrap(), err.join().unwrap()),
        )
    }
}

impl Drop for Home {
    fn drop(&mut self) {
        std::fs::remove_dir_all(&self.0).ok();
    }
}

fn stop(child: &mut Child) {
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
    child.kill().ok();
    child.wait().ok();
}

fn manifest(id: &str) -> Manifest {
    harness_tools::load_all()
        .unwrap()
        .remove(id)
        .unwrap_or_else(|| panic!("no harness manifest {id}"))
}

fn strings(items: &[&str]) -> Vec<String> {
    items.iter().map(|s| s.to_string()).collect()
}

/// A login command running the way `login::spawn` runs it, and what it has printed so far.
struct Screen {
    child: Child,
    raw: Arc<Mutex<String>>,
    writer: Option<OwnedFd>,
}

impl Screen {
    fn start(home: &Home, exe: &str, argv: &[String], terminal: bool) -> Screen {
        let mut cmd = home.command(exe, argv);
        unsafe {
            cmd.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
        let raw = Arc::new(Mutex::new(String::new()));
        let (reader, writer): (OwnedFd, Option<OwnedFd>) = if terminal {
            let (mut master, mut slave) = (0, 0);
            let size = libc::winsize {
                ws_row: 50,
                ws_col: 500,
                ws_xpixel: 0,
                ws_ypixel: 0,
            };
            let opened = unsafe {
                libc::openpty(
                    &mut master,
                    &mut slave,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    &size as *const _ as *mut _,
                )
            };
            assert_eq!(opened, 0, "no terminal could be opened");
            let (master, slave) =
                unsafe { (OwnedFd::from_raw_fd(master), OwnedFd::from_raw_fd(slave)) };
            unsafe {
                let mut t: libc::termios = std::mem::zeroed();
                libc::tcgetattr(slave.as_raw_fd(), &mut t);
                t.c_lflag &= !libc::ECHO;
                libc::tcsetattr(slave.as_raw_fd(), libc::TCSANOW, &t);
                libc::fcntl(master.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC);
            }
            cmd.stdin(Stdio::from(slave.try_clone().unwrap()))
                .stdout(Stdio::from(slave.try_clone().unwrap()))
                .stderr(Stdio::from(slave));
            let writer = master.try_clone().unwrap();
            (master, Some(writer))
        } else {
            let mut fds = [0; 2];
            assert_eq!(unsafe { libc::pipe(fds.as_mut_ptr()) }, 0);
            let (read, write) =
                unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) };
            unsafe {
                libc::fcntl(read.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC);
            }
            cmd.stdin(Stdio::null())
                .stdout(Stdio::from(write.try_clone().unwrap()))
                .stderr(Stdio::from(write));
            (read, None)
        };
        let child = cmd.spawn().unwrap_or_else(|e| {
            panic!(
                "`{exe}` could not start ({e}): the contract check needs the real `{exe}` on PATH"
            )
        });
        drop(cmd);
        let seen = raw.clone();
        std::thread::spawn(move || {
            let mut file = std::fs::File::from(reader);
            let mut buf = [0u8; 8192];
            let mut bytes: Vec<u8> = vec![];
            while let Ok(n) = file.read(&mut buf) {
                if n == 0 {
                    break;
                }
                bytes.extend_from_slice(&buf[..n]);
                *seen.lock().unwrap() = String::from_utf8_lossy(&bytes).into_owned();
            }
        });
        Screen { child, raw, writer }
    }

    fn raw(&self) -> String {
        self.raw.lock().unwrap().clone()
    }

    /// Waits until `ready` says yes about what has been printed; false after `secs`.
    fn wait(&mut self, secs: u64, ready: impl Fn(&str) -> bool) -> bool {
        let until = Instant::now() + Duration::from_secs(secs);
        loop {
            if ready(&self.raw()) {
                return true;
            }
            if Instant::now() > until {
                return false;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }

    fn type_in(&self, text: &str) {
        let w = self.writer.as_ref().unwrap();
        let wrote = unsafe { libc::write(w.as_raw_fd(), text.as_ptr() as *const _, text.len()) };
        assert_eq!(wrote, text.len() as isize, "the terminal took no input");
    }

    /// What the command shows, for a failure message: `parse`'s scrubbed lines, with any one-time code taken out.
    fn shown(&self, hide: &str) -> String {
        let (_, _, lines, _) = parse(&self.raw(), hide);
        let codes = Regex::new(r"[A-Z0-9]{3,8}-[A-Z0-9]{3,8}").unwrap();
        codes
            .replace_all(&lines.join(" | "), "[code]")
            .replace(hide, "[typed]")
    }
}

impl Drop for Screen {
    fn drop(&mut self) {
        stop(&mut self.child);
    }
}

/// `oauth_retry`'s reading of a screen, from `parse`'s lines: no spaces, lower case.
fn squeezed(raw: &str, hide: &str) -> String {
    let (_, _, lines, _) = parse(raw, hide);
    lines
        .join("")
        .chars()
        .filter(|c| !c.is_whitespace())
        .flat_map(char::to_lowercase)
        .collect()
}

#[test]
#[ignore = "runs the real claude and codex CLIs: scripts/cli_contract.sh"]
fn both_clis_report_a_version_the_runner_can_read() {
    let home = Home::new();
    for id in ["claude-code", "codex"] {
        let m = manifest(id);
        let (code, out) = home.run(&m.executable, &m.version_args, "", 30);
        assert_eq!(code, Some(0), "`{} --version` failed", m.executable);
        let found = Regex::new(&m.version_pattern)
            .unwrap()
            .captures(&out)
            .map(|c| c[1].to_string())
            .unwrap_or_default();
        assert!(
            harness_tools::version_key(&found).len() == 3,
            "`{} --version` printed no version the manifest's pattern reads: {:?}",
            m.executable,
            out.trim()
        );
        println!("cli contract: {} {found}", m.executable);
        if id == "claude-code" {
            assert!(
                crate::hosts::claude::version_tuple(&out).is_some(),
                "Claude Code's version decides steering; it could not be read from {:?}",
                out.trim()
            );
        }
    }
}

#[test]
#[ignore = "runs the real claude and codex CLIs: scripts/cli_contract.sh"]
fn claude_still_has_every_command_and_flag_the_runner_passes() {
    let home = Home::new();
    let m = manifest("claude-code");
    let help = |args: &[&str]| -> String {
        let mut argv = strings(args);
        argv.push("--help".into());
        let (code, out) = home.run("claude", &argv, "", 30);
        assert_eq!(code, Some(0), "`claude {}` failed", argv.join(" "));
        out
    };
    let top = help(&[]);
    // A turn (hosts/claude.rs `argv`): each of these stops every bot's turn if the CLI drops it.
    let mut missing = vec![];
    let mut turn = vec!["-p", "--print"];
    turn.extend(
        crate::hosts::claude::STREAM_ARGS
            .iter()
            .filter(|a| a.starts_with("--")),
    );
    turn.extend([
        "--setting-sources",
        "--strict-mcp-config",
        "--tools",
        "--disable-slash-commands",
        "--settings",
        "--mcp-config",
        "--add-dir",
        "--model",
        "--effort",
        "--resume",
        "--fork-session",
        "--session-id",
    ]);
    for flag in turn {
        if !top.contains(flag) {
            missing.push(format!("claude {flag}"));
        }
    }
    for value in ["stream-json", "bypassPermissions"] {
        if !top.contains(value) {
            missing.push(format!("claude … {value}"));
        }
    }
    // Sign in, sign out, Connect and the status check, as the manifest names them.
    let login = m.login.clone().unwrap();
    let status = m.capabilities.sign_in_status.args.clone();
    for argv in [&login.command, &login.logout, &login.connect, &status] {
        let (words, flags): (Vec<&String>, Vec<&String>) =
            argv.iter().partition(|a| !a.starts_with('-'));
        let words: Vec<&str> = words.iter().map(|s| s.as_str()).collect();
        let mut argv = strings(&words);
        argv.push("--help".into());
        let (code, out) = home.run("claude", &argv, "", 30);
        if code != Some(0) {
            missing.push(format!("claude {}", words.join(" ")));
        }
        for flag in flags {
            if !out.contains(flag.as_str()) {
                missing.push(format!("claude {} {flag}", words.join(" ")));
            }
        }
    }
    assert!(
        missing.is_empty(),
        "this Claude Code no longer lists what the runner passes: {}",
        missing.join(", ")
    );
}

#[test]
#[ignore = "runs the real claude and codex CLIs: scripts/cli_contract.sh"]
fn codex_still_has_every_command_and_flag_the_runner_passes() {
    let home = Home::new();
    let m = manifest("codex");
    let (code, top) = home.run("codex", &strings(&["--help"]), "", 30);
    assert_eq!(code, Some(0), "`codex --help` failed");
    let mut missing = vec![];
    // Turns run in `codex app-server` (hosts/codex.rs).
    if !top.contains("app-server") {
        missing.push("codex app-server".to_string());
    }
    let login = m.login.clone().unwrap();
    for argv in [
        &login.command,
        &login.logout,
        &login.connect,
        &m.capabilities.api_key_login,
        &m.capabilities.sign_in_status.args,
    ] {
        let (words, flags): (Vec<&String>, Vec<&String>) =
            argv.iter().partition(|a| !a.starts_with('-'));
        let words: Vec<&str> = words.iter().map(|s| s.as_str()).collect();
        let mut argv = strings(&words);
        argv.push("--help".into());
        let (code, out) = home.run("codex", &argv, "", 30);
        if code != Some(0) {
            missing.push(format!("codex {}", words.join(" ")));
        }
        for flag in flags {
            if !out.contains(flag.as_str()) {
                missing.push(format!("codex {} {flag}", words.join(" ")));
            }
        }
    }
    assert!(
        missing.is_empty(),
        "this Codex no longer lists what the runner passes: {}",
        missing.join(", ")
    );
}

#[test]
#[ignore = "runs the real claude and codex CLIs: scripts/cli_contract.sh"]
fn a_signed_out_cli_says_so_in_the_words_the_status_check_reads() {
    let home = Home::new();
    // Claude Code: one JSON object whose `loggedIn` is false (login.rs `sign_in_status`, "json").
    let status = manifest("claude-code").capabilities.sign_in_status;
    assert_eq!(status.check, "json");
    let (_, out) = home.run("claude", &status.args, "", status.timeout_s);
    let said = serde_json::from_str::<Value>(out.lines().next().unwrap_or(""))
        .ok()
        .or_else(|| serde_json::from_str(out.trim()).ok());
    let said = said.expect("`claude auth status --json` printed no JSON: readiness cannot tell signed in from signed out");
    assert_eq!(
        said[&status.field],
        json!(false),
        "`claude auth status --json` in an empty home has no false `{}`",
        status.field
    );
    assert!(
        said.get(&status.method_field).is_some(),
        "`claude auth status --json` no longer has `{}`, which names how a computer is signed in",
        status.method_field
    );
    // Codex: its signed-out words (the "command" check), whatever its exit code.
    let status = manifest("codex").capabilities.sign_in_status;
    assert_eq!(status.check, "command");
    let (_, out) = home.run("codex", &status.args, "", status.timeout_s);
    assert!(
        out.to_lowercase().contains(&status.not_ready),
        "`codex login status` in an empty home does not say {:?}: a signed-out Codex would read as unknown",
        status.not_ready
    );
    assert!(
        !out.to_lowercase()
            .replace(&status.not_ready, "")
            .contains(&status.ready),
        "`codex login status` in an empty home says {:?}",
        status.ready
    );
}

#[test]
#[ignore = "runs the real claude and codex CLIs: scripts/cli_contract.sh"]
fn claude_connect_shows_a_link_and_a_code_prompt_and_a_wrong_code_gives_the_retry_screen() {
    let home = Home::new();
    let login = manifest("claude-code").login.unwrap();
    assert!(login.terminal, "Claude Code signs in in a terminal");
    let mut screen = Screen::start(&home, "claude", &login.connect, true);
    // 1. The link and the prompt for the code: what a person is shown before anything else (`instructions_ready`).
    let ready = screen.wait(60, |raw| {
        let (url, _, _, prompt) = parse(raw, "");
        !url.is_empty() && prompt
    });
    let (url, _, _, prompt) = parse(&screen.raw(), "");
    assert!(
        ready,
        "`claude setup-token` showed {} within 60 s, so Connect never gets to ask for the code. It shows: {}",
        match (url.is_empty(), prompt) {
            (true, true) => "a prompt for the code but no link the runner can read",
            (false, false) => "a link but no prompt for a code the runner knows",
            _ => "neither a link nor a prompt for a code",
        },
        screen.shown("")
    );
    assert!(
        url.starts_with("https://") && url.len() > 60 && !url.contains(char::is_whitespace),
        "the sign-in link the runner read from `claude setup-token` is cut short or broken ({} characters)",
        url.len()
    );
    // 2. The code, then Enter on its own a moment later, as the runner types them (ENTER_AFTER_CODE).
    let before = screen.raw().len();
    screen.type_in(MADE_UP_CODE);
    std::thread::sleep(Duration::from_millis(1200));
    assert!(
        !screen.raw().contains(MADE_UP_CODE),
        "`claude setup-token` now shows the pasted code in the clear: it would reach the runner's log"
    );
    assert!(
        screen.raw().len() > before,
        "`claude setup-token` drew nothing when the code was typed: it may no longer be reading the terminal"
    );
    screen.type_in("\r");
    // 3. A code Anthropic refuses: the screen `oauth_retry` ends the sign-in on, in the category a person is told.
    let retry = screen.wait(60, |raw| {
        let text = squeezed(raw, MADE_UP_CODE);
        text.contains("oautherror:") && text.contains("pressentertoretry")
    });
    assert!(
        retry,
        "after a wrong code `claude setup-token` did not show \"OAuth error: … Press Enter to retry\" within 60 s, \
         so the runner cannot tell a refused code and the person waits until the sign-in times out. It shows: {}",
        screen.shown(MADE_UP_CODE)
    );
    let text = squeezed(&screen.raw(), MADE_UP_CODE);
    let error = text.split_once("oautherror:").unwrap().1;
    assert!(
        error.contains("thatcodewasn'taccepted") || error.contains("thatcodewasnotaccepted"),
        "a refused code is no longer worded \"That code wasn't accepted\": the person would be told a general failure. It shows: {}",
        screen.shown(MADE_UP_CODE)
    );
    // 4. Nothing a person would be shown carries the link.
    let (_, _, lines, _) = parse(&screen.raw(), MADE_UP_CODE);
    assert!(
        !lines.iter().any(|l| l.contains("https://")),
        "a line of `claude setup-token` still carries a link after scrubbing"
    );
}

#[test]
#[ignore = "runs the real claude and codex CLIs: scripts/cli_contract.sh"]
fn codex_device_sign_in_shows_a_link_and_a_one_time_code() {
    let home = Home::new();
    let login = manifest("codex").login.unwrap();
    assert!(
        !login.terminal,
        "Codex's device sign-in runs without a terminal"
    );
    assert_eq!(login.connect, login.command);
    let mut screen = Screen::start(&home, "codex", &login.command, false);
    let ready = screen.wait(60, |raw| {
        let (url, code, _, _) = parse(raw, "");
        !url.is_empty() && !code.is_empty()
    });
    let (url, code, _, _) = parse(&screen.raw(), "");
    assert!(
        ready,
        "`codex login --device-auth` showed {} within 60 s, so a ChatGPT plan cannot be signed in. It shows: {}",
        match (url.is_empty(), code.is_empty()) {
            (true, false) => "a code but no link the runner can read",
            (false, true) => "a link but no one-time code the runner can read",
            _ => "neither a link nor a one-time code",
        },
        screen.shown("")
    );
    assert!(
        url.starts_with("https://") && !url.contains('\x1b'),
        "the link read from `codex login --device-auth` is broken"
    );
    // Stopped here: the code is never entered anywhere, and nothing was signed in.
    drop(screen);
    assert!(
        !home.0.join(".codex/auth.json").exists(),
        "a login file appeared without a sign-in"
    );
}

#[test]
#[ignore = "runs the real claude and codex CLIs: scripts/cli_contract.sh"]
fn a_claude_turn_with_no_login_ends_as_a_refused_sign_in() {
    let home = Home::new();
    let mut argv = strings(&["-p"]);
    argv.extend(
        crate::hosts::claude::STREAM_ARGS
            .iter()
            .map(|s| s.to_string()),
    );
    argv.extend(strings(&[
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--tools",
        "",
        "--disable-slash-commands",
        "--settings",
        "{\"disableAllHooks\":true}",
        "--session-id",
        &uuid::Uuid::new_v4().to_string(),
    ]));
    let input = json!({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}});
    let (_, out) = home.run("claude", &argv, &format!("{input}\n"), 120);
    let events: Vec<Value> = out
        .lines()
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect();
    let kinds: Vec<String> = events
        .iter()
        .map(|e| crate::util::text_of(&e["type"]))
        .collect();
    assert!(
        kinds.iter().any(|k| k == "system"),
        "a Claude turn with the runner's flags printed no stream-json `system` event (it printed: {kinds:?}): \
         a flag the runner passes may have been refused"
    );
    let result = events
        .iter()
        .find(|e| e["type"] == "result")
        .unwrap_or_else(|| {
            panic!(
                "a Claude turn with no login ended with no `result` event (it printed: {kinds:?})"
            )
        });
    let said = crate::util::text_of(&result["result"]);
    assert_eq!(
        result["is_error"],
        json!(true),
        "a Claude turn with no login did not end as an error"
    );
    assert!(
        crate::hosts::is_auth_rejected(&said),
        "a Claude turn with no login says {said:?}, which the runner does not read as \"sign in again\": \
         the bot's task would fail instead of asking for a sign-in"
    );
}

/// The `is_error` answer Switch to API key reads (login.rs `key_works`) for a key the provider refuses.
#[test]
#[ignore = "runs the real claude and codex CLIs: scripts/cli_contract.sh"]
fn claude_answers_a_refused_api_key_as_an_error_the_key_check_reads() {
    let home = Home::new();
    let check = manifest("claude-code").capabilities.api_key_check;
    let mut c = home.command("claude", &check);
    c.env("ANTHROPIC_API_KEY", "sk-ant-api03-tico-contract-not-a-key")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let out = c.output().expect("claude could not start");
    let said = String::from_utf8_lossy(&out.stdout);
    let all = format!("{said}\n{}", String::from_utf8_lossy(&out.stderr));
    assert!(
        !all.to_lowercase().contains("unknown option"),
        "Claude Code refuses a flag of the API key check ({}): every new key would read as not working",
        check.join(" ")
    );
    let answers: Vec<Value> = said
        .lines()
        .chain([said.trim()])
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect();
    assert!(
        answers.iter().any(|v| v["is_error"] == true),
        "the API key check printed no JSON answer with `is_error` for a key Anthropic refuses"
    );
}

/// `codex app-server`, spoken to as hosts/codex.rs speaks to it.
struct AppServer {
    child: Child,
    stdin: std::process::ChildStdin,
    messages: mpsc::Receiver<Value>,
}

impl AppServer {
    fn start(home: &Home) -> AppServer {
        let mut c = home.command("codex", &strings(&["app-server"]));
        c.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        unsafe {
            c.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
        let mut child = c.spawn().expect("codex could not start");
        let stdout = child.stdout.take().unwrap();
        let (tx, messages) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if let Ok(v) = serde_json::from_str::<Value>(&line) {
                    if tx.send(v).is_err() {
                        break;
                    }
                }
            }
        });
        AppServer {
            stdin: child.stdin.take().unwrap(),
            child,
            messages,
        }
    }

    fn send(&mut self, message: Value) {
        writeln!(self.stdin, "{message}").expect("codex app-server closed its input");
    }

    /// The first message `wanted` accepts, or None after `secs`.
    fn wait(&mut self, secs: u64, wanted: impl Fn(&Value) -> bool) -> Option<Value> {
        let until = Instant::now() + Duration::from_secs(secs);
        while let Some(left) = until.checked_duration_since(Instant::now()) {
            match self.messages.recv_timeout(left) {
                Ok(m) if wanted(&m) => return Some(m),
                Ok(_) => {}
                Err(_) => return None,
            }
        }
        None
    }

    fn request(&mut self, id: u64, method: &str, params: Value) -> Value {
        self.send(json!({"id": id, "method": method, "params": params}));
        let answer = self
            .wait(60, |m| m["id"] == json!(id))
            .unwrap_or_else(|| panic!("codex app-server gave no answer to `{method}` in 60 s"));
        assert!(
            answer.get("error").is_none(),
            "codex app-server refused `{method}` as the runner sends it: {}",
            crate::login::redact(&answer["error"]["message"].to_string())
        );
        answer["result"].clone()
    }
}

impl Drop for AppServer {
    fn drop(&mut self) {
        stop(&mut self.child);
    }
}

#[test]
#[ignore = "runs the real claude and codex CLIs: scripts/cli_contract.sh"]
fn a_codex_turn_with_no_login_ends_as_a_refused_sign_in() {
    let home = Home::new();
    let mut server = AppServer::start(&home);
    let info = json!({"name": "tico-keeper", "title": "Tico keeper", "version": "0.1"});
    server.request(
        1,
        "initialize",
        json!({"clientInfo": info, "capabilities": {"experimentalApi": true}}),
    );
    server.send(json!({"method": "initialized", "params": {}}));
    let started = server.request(
        2,
        "thread/start",
        json!({"cwd": home.0.join("work"), "approvalPolicy": "never", "sandbox": "read-only"}),
    );
    let thread = crate::util::text_of(&started["thread"]["id"]);
    assert!(!thread.is_empty(), "`thread/start` returned no thread id");
    let turn = server.request(
        3,
        "turn/start",
        json!({"threadId": thread, "input": [{"type": "text", "text": "hi"}]}),
    );
    assert!(
        !crate::util::text_of(&turn["turn"]["id"]).is_empty(),
        "`turn/start` returned no turn id"
    );
    // The end of the turn as hosts/codex.rs reads it: an `error` that will not be retried, or a failed `turn/completed`.
    let ended = server
        .wait(180, |m| {
            (m["method"] == "error" && !crate::util::truthy(m["params"].get("willRetry")))
                || m["method"] == "turn/completed"
        })
        .expect("a Codex turn with no login had not ended after 3 minutes: the bot's task would sit as running");
    let error = if ended["method"] == "error" {
        &ended["params"]["error"]
    } else {
        &ended["params"]["turn"]["error"]
    };
    let said = crate::util::text_of(&error["message"]);
    assert!(
        crate::hosts::is_auth_rejected(&said),
        "a Codex turn with no login ends with {:?}, which the runner does not read as \"sign in again\": \
         the bot's task fails with a provider error and nobody is asked to sign in",
        crate::login::redact(&said)
    );
}
