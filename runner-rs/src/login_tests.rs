use super::*;

#[test]
fn the_screen_after_a_code_is_logged_without_its_link_code_or_token() {
    let raw = "\x1b[2GSelect\x1b[9Gan\x1b[12Gorganization\r\n\x1b]8;;https://claude.com/cai/oauth/authorize?code=true\x07link\x1b]8;;\x07 \
               https://claude.com/x?state=abc \r\nsecretcode12345\r\nsk-ant-oat01-abcdefghijklmnopqrstuvwxyz0123456789\r\n> Acme\r\n";
    let digest = screen_digest(raw, "secretcode12345");
    assert!(digest.contains("Select an organization"), "{digest}");
    assert!(digest.contains("> Acme"), "{digest}");
    for leak in [
        "secretcode12345",
        "sk-ant-oat01",
        "abcdefghijklmnop",
        "https://",
        "state=abc",
    ] {
        assert!(!digest.contains(leak), "{leak} in {digest}");
    }
    let long = "word ".repeat(200);
    assert!(screen_digest(&long, "").chars().count() <= 240);
    // The masked code's tail, and pieces of a split token too short for the secret patterns.
    let digest = screen_digest(
        "Code: ************a1B2c3 ok\r\nabcdefghij0123456789_\x1b[1Cklmnopqrstuvwxyz-01234 end",
        "",
    );
    assert_eq!(digest, "Code: ******** ok [redacted] [redacted] end");
}

#[test]
fn connect_takes_a_printed_token_only_once_it_has_all_arrived() {
    let token = format!("sk-ant-oat01-{}", "a1_B-".repeat(20));
    let shown = format!("\x1b[1mYour token:\x1b[0m\r\n{token}\x1b[0m\r\nStore it safely.\r\n");
    assert_eq!(
        printed_token(&shown, "sk-ant-oat", false).as_deref(),
        Some(token.as_str())
    );
    // Still arriving: no verdict until something follows it, or the command has ended.
    assert_eq!(printed_token(&token, "sk-ant-oat", false), None);
    assert_eq!(
        printed_token(&token, "sk-ant-oat", true).as_deref(),
        Some(token.as_str())
    );
    // The CLI naming the format is not a token.
    assert_eq!(
        printed_token(
            "It looks like sk-ant-oat01-… or sk-ant-oat01-abc\n",
            "sk-ant-oat",
            true
        ),
        None
    );
    assert_eq!(printed_token("no such thing\n", "sk-ant-oat", true), None);
    // Painted over a row that had text: the renderer steps over each cell that already shows the right character
    // (here the prompt's `a`, `e` and `d` land where the token has the same letters), so the stream has gaps.
    let token = format!("sk-ant-oat01-{}", "paste-code-here_".repeat(6));
    let old = "Paste code here if prompted > ";
    let mut repaint = format!("{old}\r\n\x1b[1A\r");
    let mut skipped = 0;
    for (at, c) in token.chars().enumerate() {
        if old.chars().nth(at) == Some(c) {
            repaint.push_str("\x1b[1C");
            skipped += 1;
        } else {
            repaint.push(c);
        }
    }
    repaint.push_str("\x1b[K\r\n\x1b[2CStore this token securely.\r\n");
    assert!(skipped > 0, "the case needs a stepped-over cell");
    assert_eq!(token_in(&plain(&repaint), "sk-ant-oat", true), None);
    assert_eq!(printed_token(&repaint, "sk-ant-oat", false), None);
    assert_eq!(
        printed_token(&repaint, "sk-ant-oat", true).as_deref(),
        Some(token.as_str())
    );
    // Stepped over after its 90th character: the stream alone reads a token cut short, the screen all of it.
    let late = format!(
        "{}Q\r{}\x1b[1C{}\r\n",
        " ".repeat(95),
        &token[..95],
        &token[96..]
    );
    let late_token = format!("{}Q{}", &token[..95], &token[96..]);
    assert_eq!(
        token_in(&plain(&late), "sk-ant-oat", true).map(|t| t.len()),
        Some(95)
    );
    assert_eq!(
        printed_token(&late, "sk-ant-oat", true).as_deref(),
        Some(late_token.as_str())
    );
    // Text a repaint erased is not on the screen.
    assert_eq!(
        screen("one\r\ntwo two\x1b[1A\r\x1b[2K\x1b[3Cup\x1b[1B\r\x1b[4C\x1b[K"),
        vec!["   up", "two "]
    );
    // Nor is a piece of one.
    assert_eq!(
        printed_token(&format!("{}\n", &token[..80]), "sk-ant-oat", true),
        None
    );
}

#[test]
fn oauth_retry_waits_for_the_blocked_state() {
    let error =
        "\x1b[31mOAuth error:\x1b[0m That code was not accepted.\r\nPress\x1b[1GEnter to re";
    assert!(!oauth_retry(error), "a partial prompt is still arriving");
    assert!(oauth_retry(&format!("{error}try.")));
    assert!(!oauth_retry("Paste code here if prompted >"));
    assert!(!oauth_retry(
        "Your OAuth token:\nvalid-token\nPress Enter to continue"
    ));
    assert!(
        !oauth_retry("Press Enter to retry"),
        "an unrelated retry is not an OAuth failure"
    );
    assert_eq!(
        oauth_failure("OAuth error: That code wasn't accepted. expired, reused or incomplete"),
        "oauth_code_rejected"
    );
    assert_eq!(
        oauth_failure("OAuth error: fetch failed https://private.example/?code=never-log-this"),
        "oauth_network_error"
    );
    assert_eq!(
        oauth_failure("OAuth error: secret response sk-ant-oat01-never-log-this"),
        "oauth_error"
    );
    // A command that ends by itself: the provider held the token back. Words arrive split by cursor moves.
    for refused in [
        "This\x1b[1Cstep creates a long-lived token, which this policy does\x1b[1Cnot\r\npermit \u{2014} use an API key instead.",
        "Couldn\u{2019}t verify your organization\u{2019}s settings (timeout)",
        "Anthropic reported the token revoked; it was not shown.",
        "The token could not be confirmed revoked and was not shown",
    ] {
        assert_eq!(oauth_failure(refused), "provider_refused", "{refused}");
    }
    assert_eq!(oauth_failure("Your account is on hold."), "account_on_hold");
    assert_eq!(oauth_failure("Opening browser to sign in"), "cli_stopped");
    for category in ["provider_refused", "account_on_hold"] {
        for waiting in [false, true] {
            assert!(failure_message(category, waiting)
                .starts_with("This account was not given a token."));
        }
    }
    assert_eq!(
        failure_message("cli_stopped", false),
        "The sign-in command stopped without signing in"
    );
}

/// An Antigravity Connect signs in as a plan turn runs: in a side home of its own, told it runs over SSH, with no
/// display and no other Google sign-in; the code goes to its input, and its file counts only once `agy acp` starts
/// a session with a model there. Each failure says why in a person's words. The computer's own sign-in, and its
/// keyring, are never touched.
#[test]
fn an_antigravity_connect_signs_in_on_the_side_and_counts_only_what_agy_accepts() {
    const CHILD: &str = "TICO_TEST_AGY_CONNECT";
    let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
        let root = std::env::temp_dir().join(format!(
            "tico-agy-connect-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(root.join("bin")).unwrap();
        crate::util::write_script(
            &root.join("bin/agy"),
            r#"#!/usr/bin/env python3
import json, os, sys
env = os.environ
if env.get("SSH_CONNECTION") != "127.0.0.1 0 127.0.0.1 0": sys.exit(3)
if any(env.get(k) for k in ("DISPLAY", "WAYLAND_DISPLAY", "GEMINI_API_KEY", "AGY_ADC_AUTH")): sys.exit(4)
home = env.get("GEMINI_HOME", "")
if ".tico-switch-" not in home: sys.exit(5)
token = os.path.join(home, "antigravity-cli", "antigravity-oauth-token")
said = lambda name: os.path.exists(os.path.join(env["HOME"], name))
if sys.argv[1:] == ["acp", "--login", "--gemini-dir", home, "--app-data-dir", "antigravity-cli"]:
    print("Open this link: https://accounts.acme.example/o/oauth2/auth?redirect_uri=https%3A%2F%2Fsignin.acme.example%2Foauth-callback&state=s&response_type=code", flush=True)
    print("Then paste the authorization code, or the full URL:", flush=True)
    code = sys.stdin.readline().strip()
    if said("exits"): sys.exit(7)
    if code != "4/pasted-code": sys.exit(6)
    os.makedirs(os.path.dirname(token), exist_ok=True)
    open(token, "w").write('{"token":{"access_token":"ya29.synthetic-new-access-token"}}')
    sys.exit(0)
if sys.argv[1:] != ["acp", "--gemini-dir", home, "--app-data-dir", "antigravity-cli"] or env.get("AGY_ACP_FORCE_FILE_STORAGE") != "1":
    sys.exit(2)
# A tool of agy's that ignores SIGTERM and holds its output: it goes with agy's group.
import subprocess
helper = subprocess.Popen(["sh", "-c", "trap '' TERM; exec sleep 30"])
open(os.path.join(env["HOME"], "helper.pid"), "w").write(str(helper.pid))
for line in sys.stdin:
    m = json.loads(line)
    i, method = m["id"], m["method"]
    if method == "session/new" and (said("refuses") or not os.path.getsize(token)):
        reply = {"error": {"code": -32000, "message": "Authentication required"}}
    elif method == "session/new" and said("subscription"):
        reply = {"error": {"code": -32603, "message": "SUBSCRIPTION_REQUIRED: this account has no Antigravity plan"}}
    elif method == "session/new":
        reply = {"result": {"sessionId": "s1", "configOptions": [{"id": "model", "type": "select", "currentValue": "gemini-4-neon", "options": [{"value": "gemini-4-neon"}]}]}}
    else:
        reply = {"result": {}}
    print(json.dumps(dict(reply, jsonrpc="2.0", id=i)), flush=True)
"#,
        );
        let out = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "login::tests::an_antigravity_connect_signs_in_on_the_side_and_counts_only_what_agy_accepts",
                "--nocapture",
            ])
            .env(CHILD, &root)
            .env("PATH", format!("{}:/usr/bin:/bin", root.join("bin").display()))
            .env("HOME", &root)
            .env("DISPLAY", ":0")
            .env("GEMINI_API_KEY", "AIza-computer-key")
            .env("AGY_ADC_AUTH", "1") // the computer's Google Cloud credentials, which no plan may use
            .env_remove("GEMINI_HOME")
            .env_remove("SSH_CONNECTION")
            .env_remove(crate::isolation::UID_ENV)
            .env_remove(crate::isolation::GID_ENV)
            .output()
            .unwrap();
        std::fs::remove_dir_all(&root).ok();
        assert!(
            out.status.success(),
            "{}{}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        );
        return;
    };
    let own = root.join(".gemini");
    std::fs::create_dir_all(own.join("antigravity-cli")).unwrap();
    std::fs::write(
        own.join("antigravity-cli/antigravity-oauth-token"),
        "computer",
    )
    .unwrap();
    std::fs::create_dir_all(root.join("projects")).unwrap();
    let path = root.join("runner.json");
    std::fs::write(&path, json!({"url": "http://127.0.0.1:9", "token": "synthetic", "runner_id": "r1", "projects_dir": root.join("projects")}).to_string()).unwrap();
    let config = Config::load(&path).unwrap();
    // One a Connect cut off by a stop left: it goes when the runner starts. Another runner's are not its to sweep.
    let sides = plan_sides(&config.state_dir());
    std::fs::create_dir_all(sides.join(".tico-switch-old/antigravity-cli")).unwrap();
    let other = root.join("other-state/plan-sides/.tico-switch-live");
    std::fs::create_dir_all(&other).unwrap();
    sweep_plan_sides(&config.state_dir());
    assert!(!sides.exists() && other.exists());
    let client = crate::client::Client::new(&config.url(), &config.token(), 1, 0).unwrap();
    let runner = Arc::new(Runner::new(
        config.clone(),
        client,
        crate::state::State::open(&root.join("state")).unwrap(),
    ));
    for (id, said) in [
        ("g1", ""),
        ("g2", "refuses"),
        ("g3", "subscription"),
        ("g4", "exits"),
    ] {
        if !said.is_empty() {
            std::fs::write(root.join(said), "").unwrap();
        }
        let login = Session::new(&json!({"id": id, "runtime": "antigravity", "kind": "connect"}));
        let (s2, r2, c2) = (login.clone(), runner.clone(), config.clone());
        let running = std::thread::spawn(move || run_session(s2, r2, c2, Duration::from_secs(20)));
        wait_for(|| {
            login
                .inner
                .lock()
                .unwrap()
                .url
                .starts_with("https://accounts.acme.example/o/oauth2/auth")
        });
        login.submit("4/pasted-code");
        running.join().unwrap();
        if let Ok(pid) = std::fs::read_to_string(root.join("helper.pid")) {
            let pid: i32 = pid.trim().parse().unwrap();
            let gone = (0..50).any(|_| {
                std::thread::sleep(Duration::from_millis(20));
                (unsafe { libc::kill(pid, 0) }) == -1
            });
            assert!(gone, "{id}: the tool agy left in its group is gone");
            std::fs::remove_file(root.join("helper.pid")).unwrap();
        }
        let i = login.inner.lock().unwrap();
        if said.is_empty() {
            assert_eq!(
                i.captured.as_deref().map(str::trim),
                Some("{\"token\":{\"access_token\":\"ya29.synthetic-new-access-token\"}}")
            );
        } else {
            let why = match said {
                "refuses" => AGY_NO_SESSION.to_string(),
                "subscription" => AGY_SUBSCRIPTION.to_string(),
                _ => "Antigravity exited with code 7. Start sign-in again.".to_string(),
            };
            assert!(i.captured.is_none() && i.state == "failed", "{:?}", i.state);
            assert_eq!(i.message, why);
            std::fs::remove_file(root.join(said)).unwrap();
        }
    }
    assert_eq!(
        std::fs::read_to_string(own.join("antigravity-cli/antigravity-oauth-token")).unwrap(),
        "computer"
    );
    let left: Vec<_> = std::fs::read_dir(&own)
        .unwrap()
        .flatten()
        .map(|e| e.file_name())
        .collect();
    assert_eq!(left, vec![std::ffi::OsString::from("antigravity-cli")]);
    assert_eq!(
        std::fs::read_dir(&sides).map_or(0, |d| d.count()),
        0,
        "no side home stays"
    );
}

/// A Connect relays the link, leaves the login it does not need alone, and holds its one result for the server.
#[test]
fn connect_captures_the_result_and_leaves_the_computers_own_login() {
    const CHILD: &str = "TICO_TEST_CONNECT";
    let token = format!("sk-ant-oat01-{}", "x".repeat(95));
    let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
        let root =
            std::env::temp_dir().join(format!("tico-connect-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join("bin")).unwrap();
        crate::util::write_script(
            &root.join("bin/claude"),
            &format!(
                r#"#!/bin/sh
case "$*" in
  setup-token)
    test -z "$ANTHROPIC_API_KEY" || exit 3
    echo 'Use the url below to sign in'; echo 'https://claude.example/authorize?x=1'; echo 'Paste code here if prompted >'
    # Like the real CLI: an Enter that arrives with the code is part of a paste, and the sign-in never finishes.
    stty -icanon min 1 time 0
    code=$(head -c 11)
    stty min 0 time 5
    test -z "$(head -c 1)" || exit 6
    stty icanon min 1
    read rest
    test "$code" = pasted-code || exit 4
    if test -e "$HOME/rejects-code"; then
      trap 'touch "$HOME/rejected-stopped"; exit 0' TERM
      printf "\033[31mOAuth error:\033[0m That code wasn't accepted. %s {token}\r\nPress\033[1GEnter to retry." "$code"
      while :; do sleep 0.1; done
    fi
    if test -e "$HOME/refuses"; then
      # Like the real CLI when the account's organization holds the token back: no retry, it ends by itself.
      # Its last screen has the masked code with its tail, a link, and a token a repaint split into three pieces.
      printf "\033[2C%s ************d-code https://claude.example/authorize?x=1\r\n" "$code"
      printf "sk-ant-oat01-abcdefghijklmnopqrstuvwxyz0123\033[1CQq7Qq7Qq7Qq7Qq7Qq7Qq7\033[1CZz9Zz9Zz9Zz9Zz9Zz9Zz9\r\n"
      printf "\033[2CThis\033[1Cstep creates a long-lived token, which this policy does\033[1Cnot permit.\r\n"
      exit 0
    fi
    echo 'Your OAuth token:'; echo '{token}'; echo 'Store it safely.'
    test ! -e "$HOME/fails-after-token" || exit 7
    sleep 30 ;;
  --version) echo 1.0.0 ;;
  *) exit 2 ;;
esac
"#
            ),
        );
        crate::util::write_script(
            &root.join("bin/codex"),
            r#"#!/bin/sh
case "$*" in
  "login --device-auth")
    echo 'Open https://auth.example/device'; echo 'Enter code ABCD-1234'
    case "$CODEX_HOME" in *.tico-switch-*) ;; *) exit 5 ;; esac
    echo '{"tokens":{"refresh_token":"r-new"}}' > "$CODEX_HOME/auth.json"
    test ! -e "$HOME/fails-after-token" || exit 7 ;;
  --version) echo 1.0.0 ;;
  *) exit 2 ;;
esac
"#,
        );
        let mut cmd = Command::new(std::env::current_exe().unwrap());
        cmd.args([
            "--exact",
            "login::tests::connect_captures_the_result_and_leaves_the_computers_own_login",
            "--nocapture",
        ])
        .env(CHILD, &root)
        .env(
            "PATH",
            format!("{}:/usr/bin:/bin", root.join("bin").display()),
        )
        .env("HOME", &root)
        .env("CODEX_HOME", root.join(".codex"))
        .env("ANTHROPIC_API_KEY", "own-key")
        .env_remove(crate::isolation::UID_ENV)
        .env_remove(crate::isolation::GID_ENV);
        let out = cmd.output().unwrap();
        std::fs::remove_dir_all(&root).ok();
        assert!(
            out.status.success(),
            "{}{}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        );
        let diagnostics = String::from_utf8_lossy(&out.stdout);
        for phase in [
            "cli_started",
            "instructions_ready",
            "code_received",
            "code_written",
            "enter_written",
            "oauth_code_rejected",
            "final_screen",
            "provider_refused",
            "credential_captured",
            "cli_exited",
            "canceled",
        ] {
            assert!(
                diagnostics.contains(&format!("\"phase\":\"{phase}\"")),
                "missing {phase}: {diagnostics}"
            );
        }
        assert!(
            !diagnostics.contains("pasted-code")
                && !diagnostics.contains("sk-ant-oat")
                && !diagnostics.contains("abcdefghijklmnop")
                && !diagnostics.contains("Qq7Qq7")
                && !diagnostics.contains("Zz9Zz9")
                && !diagnostics.contains("d-code")
                && !diagnostics.contains("https://claude.example"),
            "secret in diagnostics: {diagnostics}"
        );
        let last = diagnostics
            .lines()
            .find(|l| {
                l.contains("\"phase\":\"final_screen\"") && l.contains("\"login_id\":\"refused\"")
            })
            .expect("the refused sign-in's last screen is logged");
        assert!(
            last.contains("\"reason\":\"provider_refused\"")
                && last.contains("this policy does not permit"),
            "{last}"
        );
        return;
    };
    std::fs::create_dir_all(root.join("projects")).unwrap();
    std::fs::create_dir_all(root.join(".codex")).unwrap();
    std::fs::write(root.join(".codex/auth.json"), "{\"own\":true}").unwrap();
    let path = root.join("runner.json");
    std::fs::write(&path, json!({"url": "http://127.0.0.1:9", "token": "synthetic", "runner_id": "r1", "projects_dir": root.join("projects")}).to_string()).unwrap();
    let config = Config::load(&path).unwrap();
    let client = crate::client::Client::new(&config.url(), &config.token(), 1, 0).unwrap();
    let runner = Arc::new(Runner::new(
        config.clone(),
        client,
        crate::state::State::open(&root.join("state")).unwrap(),
    ));

    // Claude Code prints it; the pasted code reaches the CLI, and the token is never in what is reported.
    let login = Session::new(&json!({"id": "c1", "runtime": "claude", "kind": "connect"}));
    let (s2, r2, c2) = (login.clone(), runner.clone(), config.clone());
    let running = std::thread::spawn(move || run_session(s2, r2, c2, Duration::from_secs(20)));
    wait_for(|| {
        login
            .inner
            .lock()
            .unwrap()
            .url
            .starts_with("https://claude.example/")
    });
    login.submit("pasted-code");
    running.join().unwrap();
    {
        let i = login.inner.lock().unwrap();
        assert_eq!(i.captured.as_deref(), Some(token.as_str()));
        assert!(!i.finished, "it ends once the server has taken the value");
        assert!(i.lines.is_empty() && i.raw.is_empty());
    }
    let shown = login.report().map(|b| b.to_string()).unwrap_or_default();
    assert!(!shown.contains("sk-ant"), "{shown}");

    // Codex leaves a file in a home of its own, and the computer's own login stays.
    let login = Session::new(&json!({"id": "c2", "runtime": "codex", "kind": "connect"}));
    run_session(
        login.clone(),
        runner.clone(),
        config.clone(),
        Duration::from_secs(20),
    );
    assert_eq!(
        login
            .inner
            .lock()
            .unwrap()
            .captured
            .as_deref()
            .map(str::trim),
        Some("{\"tokens\":{\"refresh_token\":\"r-new\"}}")
    );
    assert_eq!(
        std::fs::read_to_string(root.join(".codex/auth.json")).unwrap(),
        "{\"own\":true}"
    );
    let left: Vec<_> = std::fs::read_dir(root.join(".codex"))
        .unwrap()
        .flatten()
        .map(|e| e.file_name())
        .collect();
    assert_eq!(left.len(), 1, "no side home stays: {left:?}");

    // A command that says it failed gives nothing, whatever it printed or wrote first.
    std::fs::write(root.join("fails-after-token"), "").unwrap();
    for (id, runtime) in [("c4", "claude"), ("c5", "codex")] {
        let login = Session::new(&json!({"id": id, "runtime": runtime, "kind": "connect"}));
        let (s2, r2, c2) = (login.clone(), runner.clone(), config.clone());
        let running = std::thread::spawn(move || run_session(s2, r2, c2, Duration::from_secs(20)));
        if runtime == "claude" {
            wait_for(|| !login.inner.lock().unwrap().url.is_empty());
            login.submit("pasted-code");
        }
        running.join().unwrap();
        let i = login.inner.lock().unwrap();
        assert!(
            i.captured.is_none() && i.finished && i.state == "failed",
            "{runtime}: {}",
            i.state
        );
    }
    std::fs::remove_file(root.join("fails-after-token")).unwrap();

    // A rejected code leaves the real CLI alive waiting for Enter. End it so the browser can offer a fresh
    // attempt, without exposing even an error that echoes the code or includes a token.
    std::fs::write(root.join("rejects-code"), "").unwrap();
    let login = Session::new(&json!({"id": "rejected", "runtime": "claude", "kind": "connect"}));
    let (s2, r2, c2) = (login.clone(), runner.clone(), config.clone());
    let running = std::thread::spawn(move || run_session(s2, r2, c2, Duration::from_secs(20)));
    wait_for(|| !login.inner.lock().unwrap().url.is_empty());
    login.submit("pasted-code");
    wait_for(|| login.inner.lock().unwrap().finished);
    running.join().unwrap();
    assert_eq!(state(&login).0, "failed");
    assert!(
        root.join("rejected-stopped").exists(),
        "the blocked command was stopped"
    );
    {
        let i = login.inner.lock().unwrap();
        assert!(i.captured.is_none() && i.raw.is_empty() && i.lines.is_empty());
    }
    let shown = login.report().unwrap().to_string();
    assert!(
        !shown.contains("pasted-code") && !shown.contains("sk-ant"),
        "{shown}"
    );
    assert!(!login.inner.lock().unwrap().message.is_empty());
    std::fs::remove_file(root.join("rejects-code")).unwrap();

    // The code is taken, then the command ends by itself with no token and no error to retry: the person reads why,
    // in Tico's words, and the runner's log keeps the scrubbed last screen.
    std::fs::write(root.join("refuses"), "").unwrap();
    let login = Session::new(&json!({"id": "refused", "runtime": "claude", "kind": "connect"}));
    let (s2, r2, c2) = (login.clone(), runner.clone(), config.clone());
    let running = std::thread::spawn(move || run_session(s2, r2, c2, Duration::from_secs(20)));
    wait_for(|| !login.inner.lock().unwrap().url.is_empty());
    login.submit("pasted-code");
    running.join().unwrap();
    assert_eq!(
        state(&login),
        (
            "failed".to_string(),
            "This account was not given a token. Its organization's settings may not allow it."
                .to_string()
        )
    );
    {
        let i = login.inner.lock().unwrap();
        assert!(i.captured.is_none() && i.finished && i.raw.is_empty() && i.lines.is_empty());
    }
    let recent = crate::util::recent_errors();
    assert!(
        recent
            .iter()
            .any(|l| l.contains("final_screen") && l.contains("provider_refused"))
            && recent.iter().any(|l| l.contains("failed_steps")
                && l.contains("\"login_id\":\"refused\"")
                && l.contains("cli_started@")
                && l.contains(" enter_written@")
                && l.contains("=0 provider_refused@")),
        "the heartbeat carries the category: {recent:?}"
    );
    // What the server is told: the phases in order, the category and the scrubbed screen, with no link, typed
    // code or token-shaped text, and the CLI's words nowhere a person reads them.
    let body = login.report().unwrap();
    let shown = body.to_string();
    assert!(
        !shown.contains("pasted-code")
            && !shown.contains("sk-ant")
            && !shown.contains("abcdefghijklmnop")
            && !shown.contains("Qq7Qq7")
            && !shown.contains("Zz9Zz9")
            && !shown.contains("d-code")
            && !shown.contains("https://"),
        "{shown}"
    );
    assert!(!body["message"].as_str().unwrap().contains("policy"));
    let trace = &body["trace"];
    assert_eq!(trace["category"], "provider_refused");
    assert!(
        trace["screen"]
            .as_str()
            .unwrap()
            .contains("this policy does not permit"),
        "{trace}"
    );
    let phases: Vec<&str> = trace["phases"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| p["phase"].as_str().unwrap())
        .collect();
    assert_eq!(
        phases,
        [
            "cli_started",
            "instructions_ready",
            "code_received",
            "code_written",
            "enter_written",
            "cli_exited",
            "provider_refused",
            "failed"
        ]
    );
    assert_eq!(trace["phases"][5]["exit_code"], 0);
    // A server from before `trace` refused it: the verdict is said again without.
    login.diag.lock().unwrap().refused = true;
    login.inner.lock().unwrap().dirty = true;
    assert!(login.report().unwrap().get("trace").is_none());
    std::fs::remove_file(root.join("refuses")).unwrap();

    // Cancelled: nothing is captured.
    let login = Session::new(&json!({"id": "c3", "runtime": "claude", "kind": "connect"}));
    let (s2, r2, c2) = (login.clone(), runner.clone(), config.clone());
    let running = std::thread::spawn(move || run_session(s2, r2, c2, Duration::from_secs(20)));
    wait_for(|| !login.inner.lock().unwrap().url.is_empty());
    login.canceled.store(true, Ordering::SeqCst);
    running.join().unwrap();
    let i = login.inner.lock().unwrap();
    assert!(i.captured.is_none() && i.finished && i.state == "cancelled");
}

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
    let env: Env = [
        ("HOME".to_string(), home.display().to_string()),
        ("PATH".to_string(), std::env::var("PATH").unwrap()),
    ]
    .into();
    let err = begin_switch("claude", "setup1", &env, None).unwrap_err();
    assert!(err.starts_with("cp failed"), "{err}");
    assert!(
        !home.join(".tico-switch-setup1").exists(),
        "the half-made side home is gone"
    );
    std::fs::set_permissions(&seed, std::fs::Permissions::from_mode(0o600)).unwrap();
    std::fs::remove_dir_all(&home).unwrap();
}

#[test]
fn login_and_verdict_commands_use_the_bot_identity_and_profile_home() {
    const CHILD: &str = "TICO_TEST_LOGIN_IDENTITY";
    if std::env::var_os(CHILD).is_none() {
        // A separate process keeps the configured isolation identity out of other parallel tests.
        let uid = if unsafe { libc::geteuid() } == 10003 {
            10004
        } else {
            10003
        };
        let out = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "login::tests::login_and_verdict_commands_use_the_bot_identity_and_profile_home",
                "--nocapture",
            ])
            .env(CHILD, "1")
            .env(crate::isolation::UID_ENV, uid.to_string())
            .env(crate::isolation::GID_ENV, "10002")
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "{}{}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        );
        return;
    }
    let uid = std::env::var(crate::isolation::UID_ENV).unwrap();
    let env: Env = [("HOME".into(), "/subscriptions/acme/claude".into())]
        .into_iter()
        .collect();
    for args in [
        &["auth", "login", "--claudeai"][..],
        &["auth", "status", "--json"][..],
    ] {
        let command = model_command("claude", args, &env);
        assert_eq!(command.get_program(), crate::isolation::SETPRIV);
        let mut expected = vec![
            format!("--reuid={uid}"),
            "--regid=10002".into(),
            "--clear-groups".into(),
            "--inh-caps=-all".into(),
            "--ambient-caps=-all".into(),
            "claude".into(),
        ];
        expected.extend(args.iter().map(|s| s.to_string()));
        assert_eq!(
            command
                .get_args()
                .map(|a| a.to_string_lossy().into_owned())
                .collect::<Vec<_>>(),
            expected
        );
        assert!(command.get_envs().any(|(key, value)| key == "HOME"
            && value == Some(std::ffi::OsStr::new("/subscriptions/acme/claude"))));
    }
}

#[test]
fn a_profiles_claude_sign_in_drops_an_inherited_claude_config_dir() {
    const CHILD: &str = "TICO_TEST_LOGIN_CONFIG_DIR";
    let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
        // A process of its own: the runner's environment points Claude at a folder outside every profile.
        let root = std::env::temp_dir().join(format!(
            "tico-login-config-dir-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(root.join("bin")).unwrap();
        crate::util::write_script(
            &root.join("bin/claude"),
            crate::profiles::tests::FAKE_CLAUDE_LOGIN,
        );
        let out = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "login::tests::a_profiles_claude_sign_in_drops_an_inherited_claude_config_dir",
                "--nocapture",
            ])
            .env(CHILD, &root)
            .env(
                "PATH",
                format!("{}:/usr/bin:/bin", root.join("bin").display()),
            )
            .env("HOME", &root)
            .env("CLAUDE_CONFIG_DIR", root.join("outside"))
            .env_remove(crate::isolation::UID_ENV)
            .env_remove(crate::isolation::GID_ENV)
            .output()
            .unwrap();
        std::fs::remove_dir_all(&root).ok();
        let said = String::from_utf8_lossy(&out.stdout);
        assert!(
            out.status.success() && said.contains("1 passed"),
            "{said}{}",
            String::from_utf8_lossy(&out.stderr)
        );
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
        assert!(model_command(&exe, &["auth", "login", "--claudeai"], &env)
            .status()
            .unwrap()
            .success());
        assert!(root.join("acme/claude/.claude/.credentials.json").is_file());
        std::fs::remove_dir_all(root.join("acme")).unwrap();
    }
    let left: Vec<_> = std::fs::read_dir(&outside)
        .unwrap()
        .flatten()
        .map(|e| e.file_name())
        .collect();
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
      *) if [ -f "$HOME/refuse" ]; then echo "Not logged in"; exit 1; fi ;;
    esac
    if grep -q '^apikey' "$home/auth.json" 2>/dev/null; then echo "Logged in using an API key - sk-***"; exit 0; fi
    if [ -f "$home/auth.json" ]; then echo "Logged in using ChatGPT"; exit 0; fi; echo "Not logged in"; exit 1 ;;
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
    Session::new(
        &json!({"id": format!("l{}", uuid::Uuid::new_v4().simple()), "runtime": "codex", "kind": kind}),
    )
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
/// runner says it is finishing (true: confirmed; false: it had been canceled).
fn switch_with(
    runner: &Arc<Runner>,
    config: &Config,
    server: Option<bool>,
) -> (Arc<Session>, std::thread::JoinHandle<()>) {
    switch_kind(runner, config, server, "switch")
}

fn switch_kind(
    runner: &Arc<Runner>,
    config: &Config,
    server: Option<bool>,
    kind: &str,
) -> (Arc<Session>, std::thread::JoinHandle<()>) {
    let s = session(kind);
    let (s2, r2, c2) = (s.clone(), runner.clone(), config.clone());
    let running = std::thread::spawn(move || run_session(s2, r2, c2, Duration::from_secs(20)));
    if let Some(confirm) = server {
        answer_finishing(&s, confirm);
    }
    (s, running)
}

/// Stand in for Tico's answer once the runner says `s` is finishing (true: confirmed; false: it had been canceled).
fn answer_finishing(s: &Arc<Session>, confirm: bool) {
    let s3 = s.clone();
    std::thread::spawn(move || {
        let until = Instant::now() + Duration::from_secs(15);
        while state(&s3).0 != "finishing" && s3.live() && Instant::now() < until {
            std::thread::sleep(Duration::from_millis(20));
        }
        if state(&s3).0 == "finishing" {
            if confirm {
                s3.confirmed.store(true, Ordering::SeqCst)
            } else {
                s3.canceled.store(true, Ordering::SeqCst)
            }
        }
    });
}

#[test]
fn a_switch_to_a_subscription_waits_for_a_key_login_already_running_and_a_fetch_already_out() {
    const CHILD: &str = "TICO_TEST_SWITCH_DRAIN";
    let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
        // A process of its own: PATH, HOME and CODEX_HOME point at a stand-in CLI and a scratch home.
        let root = std::env::temp_dir().join(format!(
            "tico-switch-drain-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(root.join("bin")).unwrap();
        std::fs::write(root.join("bin/codex"), FAKE_CODEX).unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(
            root.join("bin/codex"),
            std::fs::Permissions::from_mode(0o755),
        )
        .unwrap();
        let path = format!("{}:/usr/bin:/bin", root.join("bin").display());
        let out = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "login::tests::a_switch_to_a_subscription_waits_for_a_key_login_already_running_and_a_fetch_already_out", "--nocapture"])
            .env(CHILD, &root).env("PATH", path).env("HOME", &root).env("CODEX_HOME", root.join("codex"))
            .env_remove(crate::isolation::UID_ENV).env_remove(crate::isolation::GID_ENV)
            .env_remove("OPENAI_API_KEY").output().unwrap();
        std::fs::remove_dir_all(&root).ok();
        let said = String::from_utf8_lossy(&out.stdout);
        assert!(
            out.status.success() && said.contains("1 passed"),
            "{said}{}",
            String::from_utf8_lossy(&out.stderr)
        );
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
    let runner = Arc::new(Runner::new(
        config.clone(),
        client,
        crate::state::State::open(&root.join("state")).unwrap(),
    ));
    let auth = root.join("codex/auth.json");
    let team = root
        .join("projects/secrets")
        .join(crate::signin::TEAM_KEYS_FILE);
    std::fs::write(&auth, "apikey sk-team-0123456789\n").unwrap();
    std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
        .unwrap();
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
    assert_eq!(
        std::fs::read_to_string(&auth).unwrap(),
        "apikey sk-team-0123456789\n",
        "the subscription went in while a key login was running"
    );
    go_tx.send(()).unwrap();
    assert!(
        !rt.block_on(fetch).unwrap(),
        "a fetch older than the switch changed a key"
    );
    assert_eq!(
        crate::signin::read_env(&team)["OPENAI_API_KEY"],
        "sk-team-0123456789"
    );

    // The key login ends, writing the key's login; only then does the subscription go in, and it stays.
    std::fs::write(root.join("key-release"), "").unwrap();
    assert!(rt.block_on(login).unwrap(), "the key login finished");
    running.join().unwrap();
    assert_eq!(state(&switch).0, "signed_in", "{:?}", state(&switch));
    assert_eq!(
        std::fs::read_to_string(&auth).unwrap(),
        "new\n",
        "the key login wrote over the subscription"
    );
    assert!(runner.sign_in.key_off("codex") && runner.sign_in.headless_secret("codex").is_none());
    assert!(!crate::signin::read_env(&team).contains_key("OPENAI_API_KEY"));
}

#[test]
fn sign_out_and_switch_account_on_the_computers_own_codex_login() {
    const CHILD: &str = "TICO_TEST_SIGN_OUT";
    let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
        // A process of its own: PATH, HOME and CODEX_HOME point at a stand-in CLI and a scratch home.
        let root =
            std::env::temp_dir().join(format!("tico-sign-out-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join("bin")).unwrap();
        std::fs::write(root.join("bin/codex"), FAKE_CODEX).unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(
            root.join("bin/codex"),
            std::fs::Permissions::from_mode(0o755),
        )
        .unwrap();
        let path = format!("{}:/usr/bin:/bin", root.join("bin").display());
        let out = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "login::tests::sign_out_and_switch_account_on_the_computers_own_codex_login",
                "--nocapture",
            ])
            .env(CHILD, &root)
            .env("PATH", path)
            .env("HOME", &root)
            .env("CODEX_HOME", root.join("codex"))
            .env_remove(crate::isolation::UID_ENV)
            .env_remove(crate::isolation::GID_ENV)
            .env_remove("OPENAI_API_KEY")
            .output()
            .unwrap();
        std::fs::remove_dir_all(&root).ok();
        let said = String::from_utf8_lossy(&out.stdout);
        assert!(
            out.status.success() && said.contains("1 passed"),
            "{said}{}",
            String::from_utf8_lossy(&out.stderr)
        );
        return;
    };
    std::fs::create_dir_all(root.join("projects/secrets")).unwrap();
    std::fs::create_dir_all(root.join("codex")).unwrap();
    let path = root.join("runner.json");
    std::fs::write(&path, json!({"url": "http://127.0.0.1:9", "token": "t", "runner_id": "r1", "projects_dir": root.join("projects")}).to_string()).unwrap();
    let config = Config::load(&path).unwrap();
    let client = crate::client::Client::new(&config.url(), &config.token(), 1, 0).unwrap();
    let runner = Arc::new(Runner::new(
        config.clone(),
        client,
        crate::state::State::open(&root.join("state")).unwrap(),
    ));
    let auth = root.join("codex/auth.json");
    let team = root
        .join("projects/secrets")
        .join(crate::signin::TEAM_KEYS_FILE);
    let sides = || {
        std::fs::read_dir(root.join("codex"))
            .unwrap()
            .flatten()
            .filter(|e| e.file_name().to_string_lossy().starts_with(".tico-switch-"))
            .count()
    };

    // Sign out: the login and the company's key this computer held are gone, and it is marked signed out.
    std::fs::write(&auth, "old\n").unwrap();
    std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
    let out = session("sign_out");
    run_sign_out(out.clone(), runner.clone(), config.clone());
    assert_eq!(state(&out).0, "signed_out", "{:?}", state(&out));
    assert!(
        !auth.exists()
            && runner.sign_in.signed_out("codex")
            && runner.sign_in.headless_secret("codex").is_none()
    );
    assert!(!std::fs::read_to_string(&team)
        .unwrap()
        .contains("OPENAI_API_KEY"));

    // A browser sign-in is a person signing in again: the mark goes.
    let back = session("sign_in");
    run_session(
        back.clone(),
        runner.clone(),
        config.clone(),
        Duration::from_secs(20),
    );
    assert_eq!(state(&back).0, "signed_in", "{:?}", state(&back));
    assert!(!runner.sign_in.signed_out("codex"));

    // A logout that did not take: failed, and not left marked signed out.
    std::fs::write(root.join("stuck"), "").unwrap();
    let stuck = session("sign_out");
    run_sign_out(stuck.clone(), runner.clone(), config.clone());
    assert_eq!(
        state(&stuck),
        (
            "failed".into(),
            "The logout finished but codex is still signed in".into()
        )
    );
    assert!(auth.exists() && !runner.sign_in.signed_out("codex"));
    std::fs::remove_file(root.join("stuck")).unwrap();

    // Switch account: signed in on the side, then, once Tico confirms, put in place; the side home is gone.
    std::fs::write(&auth, "old\n").unwrap();
    let (switch, running) = switch_with(&runner, &config, Some(true));
    running.join().unwrap();
    assert_eq!(state(&switch).0, "signed_in", "{:?}", state(&switch));
    assert_eq!(std::fs::read_to_string(&auth).unwrap(), "new\n");
    assert_eq!(sides(), 0);

    // Nothing moves before Tico confirms: a switch Tico says was canceled meanwhile keeps the old login.
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
    late.canceled.store(true, Ordering::SeqCst);
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
    assert!(
        state(&broken).1.contains("the old one is back"),
        "{:?}",
        state(&broken)
    );
    assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");
    assert_eq!(sides(), 0);
    std::fs::remove_file(root.join("refuse")).unwrap();

    // Switch to subscription (Computers part 2): the login runs on the company's key until the subscription is in
    // place. Canceled before the commit point, or not working once in place, the key stays in use.
    let key_in_use = || {
        runner.sign_in.headless_secret("codex").map(|(_, v)| v) == Some("sk-team-0123456789".into())
    };
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
    assert!(
        !runner.sign_in.key_off("codex"),
        "the key is the login again"
    );
    assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");
    // Nothing was taken off before the subscription worked: the company's copy is still here, so the next turn
    // has the key at once, with no fetch in between.
    assert!(
        std::fs::read_to_string(&team)
            .unwrap()
            .contains("OPENAI_API_KEY=sk-team-0123456789"),
        "the company key was deleted by a switch that failed"
    );
    assert!(key_in_use(), "the next turn would start with no key");
    std::fs::remove_file(root.join("refuse")).unwrap();
    // In place: the subscription is the login, the company's copy of the key is deleted and no key reaches it.
    std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
    let (moved, running) = switch_kind(&runner, &config, Some(true), "to_subscription");
    running.join().unwrap();
    assert_eq!(state(&moved).0, "signed_in", "{:?}", state(&moved));
    assert_eq!(std::fs::read_to_string(&auth).unwrap(), "new\n");
    assert!(runner.sign_in.key_off("codex") && runner.sign_in.headless_secret("codex").is_none());
    assert!(!std::fs::read_to_string(&team)
        .unwrap()
        .contains("OPENAI_API_KEY"));
    assert_eq!(sides(), 0);
    // Use the shared key lifts it.
    let shared = session("use_shared_key");
    Logins::default().begin(
        &runner,
        &json!({"id": shared.id, "runtime": "codex", "kind": "use_shared_key"}),
    );
    wait_for(|| !runner.sign_in.key_off("codex"));
    // In place and working, but a key file cannot be changed (the terminal's, after the company's and the
    // computer's own were rewritten): every key file goes back as it was, and the old login with it, so the next
    // turn has the computer's own key at once, with nothing to fetch.
    let secrets = root.join("projects/secrets");
    let (computer, hand) = (
        secrets.join(crate::signin::COMPUTER_KEYS_FILE),
        secrets.join(crate::signin::HAND_KEYS_FILE),
    );
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
    assert!(
        state(&late).1.ends_with("so it stays in use"),
        "{:?}",
        state(&late)
    );
    assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");
    assert_eq!(
        key_files(),
        before,
        "a key file was left changed by a switch that failed"
    );
    assert!(!runner.sign_in.key_off("codex"));
    assert_eq!(
        runner.sign_in.headless_secret("codex").map(|(_, v)| v),
        Some("sk-mine-0123456789".into()),
        "the next turn's key"
    );
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
    let err = adopt_files(&files, &home, &side, &env)
        .err()
        .expect("the second move fails");
    assert!(err.contains("so the old one stays"), "{err}");
    assert_eq!(
        std::fs::read_to_string(home.join("a.json")).unwrap(),
        "old-a"
    );
    // And a whole move undone: the old file back, the new one that had no old one gone.
    std::fs::remove_file(home.join("sub")).unwrap();
    let moved = adopt_files(&files, &home, &side, &env).unwrap();
    assert_eq!(
        std::fs::read_to_string(home.join("sub/b.json")).unwrap(),
        "new-b"
    );
    restore(&moved, &env).unwrap();
    assert_eq!(
        std::fs::read_to_string(home.join("a.json")).unwrap(),
        "old-a"
    );
    assert!(!home.join("sub/b.json").exists());

    // A kind this runner does not know is refused, and nothing runs or changes.
    let runs = std::fs::read_to_string(root.join("runs")).unwrap_or_default();
    let mut logins = Logins::default();
    logins.begin(
        &runner,
        &json!({"id": "lnew", "runtime": "codex", "kind": "rename_account"}),
    );
    let unknown = logins.sessions["lnew"].clone();
    assert_eq!(
        state(&unknown),
        (
            "failed".into(),
            "Update this computer's runner to rename account".into()
        )
    );
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(
        std::fs::read_to_string(root.join("runs")).unwrap_or_default(),
        runs,
        "a CLI ran for an unknown kind"
    );
    assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");

    // A switch canceled partway leaves the old login exactly as it was.
    std::fs::write(&auth, "old\n").unwrap();
    std::fs::write(root.join("slow"), "").unwrap();
    let canceled = session("switch");
    let (s2, r2, c2) = (canceled.clone(), runner.clone(), config.clone());
    let running = std::thread::spawn(move || run_session(s2, r2, c2, Duration::from_secs(20)));
    let until = Instant::now() + Duration::from_secs(10);
    while state(&canceled).0 != "waiting" && Instant::now() < until {
        std::thread::sleep(Duration::from_millis(50));
    }
    assert_eq!(sides(), 1, "the switch signs in on the side");
    canceled.canceled.store(true, Ordering::SeqCst);
    running.join().unwrap();
    assert_eq!(state(&canceled).0, "cancelled");
    assert_eq!(std::fs::read_to_string(&auth).unwrap(), "old\n");
    assert_eq!(sides(), 0);
}

/// A Tico that answers the model key fetch: `replies.0` to readiness's fetch, `replies.1` to a use_key job's (the
/// request names the job). Each request line is kept.
fn key_server(
    rt: &tokio::runtime::Runtime,
) -> (
    String,
    Arc<Mutex<(String, String)>>,
    Arc<Mutex<Vec<String>>>,
) {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let listener = rt
        .block_on(tokio::net::TcpListener::bind("127.0.0.1:0"))
        .unwrap();
    let url = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
    let (replies, seen) = (
        Arc::new(Mutex::new((String::new(), String::new()))),
        Arc::new(Mutex::new(vec![])),
    );
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
    let _guard = crate::readiness::probe_tests::PATH_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    const CHILD: &str = "TICO_TEST_MODEL_KEYS";
    let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
        // A process of its own: PATH, HOME and CODEX_HOME point at a stand-in CLI and a scratch home.
        let root =
            std::env::temp_dir().join(format!("tico-model-keys-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join("bin")).unwrap();
        std::fs::write(root.join("bin/codex"), FAKE_CODEX).unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(
            root.join("bin/codex"),
            std::fs::Permissions::from_mode(0o755),
        )
        .unwrap();
        let path = format!("{}:/usr/bin:/bin", root.join("bin").display());
        let out = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "login::tests::clear_and_replace_the_computers_own_codex_key",
                "--nocapture",
            ])
            .env(CHILD, &root)
            .env("PATH", path)
            .env("HOME", &root)
            .env("CODEX_HOME", root.join("codex"))
            .env_remove(crate::isolation::UID_ENV)
            .env_remove(crate::isolation::GID_ENV)
            .env_remove("OPENAI_API_KEY")
            .output()
            .unwrap();
        std::fs::remove_dir_all(&root).ok();
        let said = String::from_utf8_lossy(&out.stdout);
        assert!(
            out.status.success() && said.contains("1 passed"),
            "{said}{}",
            String::from_utf8_lossy(&out.stderr)
        );
        return;
    };
    use crate::signin::{read_env, COMPUTER_KEYS_FILE, HAND_KEYS_FILE, TEAM_KEYS_FILE};
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
        .unwrap();
    let (url, replies, seen) = key_server(&rt);
    let fetches = || {
        seen.lock()
            .unwrap()
            .iter()
            .filter(|l| !l.contains("login="))
            .count()
    };
    let secrets = root.join("projects/secrets");
    std::fs::create_dir_all(&secrets).unwrap();
    std::fs::create_dir_all(root.join("codex")).unwrap();
    let path = root.join("runner.json");
    std::fs::write(
        &path,
        json!({"url": url, "token": "t", "runner_id": "r1", "projects_dir": root.join("projects")})
            .to_string(),
    )
    .unwrap();
    let config = Config::load(&path).unwrap();
    let client = crate::client::Client::new(&config.url(), &config.token(), 5, 0).unwrap();
    let runner = Arc::new(Runner::new(
        config.clone(),
        client,
        crate::state::State::open(&root.join("state")).unwrap(),
    ));
    let sign = &runner.sign_in;
    let (auth, runs) = (root.join("codex/auth.json"), root.join("runs"));
    let (computer, hand, team) = (
        secrets.join(COMPUTER_KEYS_FILE),
        secrets.join(HAND_KEYS_FILE),
        secrets.join(TEAM_KEYS_FILE),
    );
    let key_logins = || {
        std::fs::read_to_string(&runs)
            .unwrap_or_default()
            .lines()
            .filter(|l| *l == "login --with-api-key")
            .count()
    };
    // A use_key job on its own thread, with Tico's answer once it is finishing (None: no answer).
    let start_use_key = |reply: &str, server: Option<bool>| {
        replies.lock().unwrap().1 = reply.into();
        let s = session("use_key");
        let (s2, r2, c2, h) = (
            s.clone(),
            runner.clone(),
            config.clone(),
            rt.handle().clone(),
        );
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
    let (hold, probing, release) = (
        root.join("hold-probe"),
        root.join("probing"),
        root.join("release"),
    );
    let no_side = || {
        std::fs::read_dir(root.join("codex"))
            .unwrap()
            .flatten()
            .all(|e| !e.file_name().to_string_lossy().starts_with(".tico-switch-"))
    };

    // Clear: every copy of the key on this computer goes, and the login it made; the terminal's other lines stay.
    std::fs::write(
        &hand,
        "OPENAI_API_KEY=sk-hand-0123456789\nKEEP=me\n# a note\n",
    )
    .unwrap();
    std::fs::write(&computer, "OPENAI_API_KEY=sk-mine-0123456789\n").unwrap();
    std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
    std::fs::write(&auth, "apikey sk-mine-0123456789\n").unwrap();
    let clear = session("clear_key");
    run_clear_key(clear.clone(), runner.clone(), config.clone());
    assert_eq!(state(&clear), ("signed_out".into(), "Key cleared".into()));
    assert!(!auth.exists(), "the login the key made is logged out");
    assert_eq!(
        std::fs::read_to_string(&hand).unwrap(),
        "KEEP=me\n# a note\n"
    );
    assert!(
        !read_env(&computer).contains_key("OPENAI_API_KEY")
            && !read_env(&team).contains_key("OPENAI_API_KEY")
    );
    // Nothing signs it back in: not the next fetch, not a company key that reappears, not the key login, not a restart.
    replies.lock().unwrap().0 = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-team-0123456789","source":"shared"}]}"#.into();
    sign.refresh_now("codex");
    assert!(!rt.block_on(sign.team_model_keys(&runner.client, "codex")));
    assert_eq!(
        fetches(),
        0,
        "the shared key was fetched after the key was cleared"
    );
    std::fs::write(&team, "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
    assert!(sign.headless_secret("codex").is_none());
    let before = key_logins();
    assert!(!rt.block_on(sign.key_login("codex", &root.join("bin/codex"))));
    assert_eq!(
        key_logins(),
        before,
        "a key login ran after the key was cleared"
    );
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
    let done = use_key(
        r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-new-0123456789","source":"computer"}]}"#,
    );
    assert_eq!(done, ("done".into(), "The new key is in use".into()));
    assert!(
        seen.lock()
            .unwrap()
            .iter()
            .any(|l| l.contains("runtime=codex") && l.contains("login=l")),
        "{:?}",
        seen.lock().unwrap()
    );
    assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-new-0123456789");
    assert_eq!(
        std::fs::read_to_string(&auth).unwrap(),
        "apikey sk-new-0123456789\n"
    );
    assert_eq!(std::fs::read_to_string(&hand).unwrap(), "KEEP=me\n");
    assert!(!sign.signed_out("codex") && !sign.key_off("codex"));
    assert_eq!(
        sign.key_source("codex"),
        Some(("OPENAI_API_KEY".into(), "computer"))
    );
    assert_eq!(
        sign.headless_secret("codex").map(|(_, v)| v),
        Some("sk-new-0123456789".into()),
        "the next turn's key"
    );

    // While the next key is checked nothing here changes: the regular fetch and key login wait, runs and the CLI
    // keep the old key. A cancel that reaches Tico first leaves all of it, the terminal's key too.
    std::fs::write(&hand, "OPENAI_API_KEY=sk-hand-0123456789\n").unwrap();
    replies.lock().unwrap().0 = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-other-0123456789","source":"computer"}]}"#.into();
    std::fs::write(&hold, "").unwrap();
    let (canceled, running) = start_use_key(next, Some(false));
    wait_for(|| probing.exists());
    let (fetched, logged) = (fetches(), key_logins());
    sign.refresh_now("codex");
    assert!(!rt.block_on(sign.team_model_keys(&runner.client, "codex")));
    assert!(!rt.block_on(sign.key_login("codex", &root.join("bin/codex"))));
    assert_eq!(
        (fetches(), key_logins()),
        (fetched, logged),
        "the regular fetch or key login ran during a key change"
    );
    assert_eq!(
        sign.headless_secret("codex").unwrap().1,
        "sk-new-0123456789"
    );
    assert_eq!(
        std::fs::read_to_string(&auth).unwrap(),
        "apikey sk-new-0123456789\n"
    );
    std::fs::write(&release, "").unwrap();
    running.join().unwrap();
    assert_eq!(state(&canceled), ("cancelled".into(), "Canceled".into()));
    assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-new-0123456789");
    assert_eq!(
        std::fs::read_to_string(&auth).unwrap(),
        "apikey sk-new-0123456789\n"
    );
    assert_eq!(read_env(&hand)["OPENAI_API_KEY"], "sk-hand-0123456789");
    assert!(no_side());
    for f in [&probing, &release] {
        std::fs::remove_file(f).unwrap();
    }

    // Run out on Tico while it is checked (the next poll no longer lists it): the same.
    let (expired, running) = start_use_key(next, None);
    wait_for(|| probing.exists());
    expired.canceled.store(true, Ordering::SeqCst);
    std::fs::write(&release, "").unwrap();
    running.join().unwrap();
    assert_eq!(state(&expired).0, "cancelled");
    assert_eq!(
        std::fs::read_to_string(&auth).unwrap(),
        "apikey sk-new-0123456789\n"
    );
    assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-new-0123456789");
    assert!(no_side());
    for f in [&hold, &probing, &release] {
        std::fs::remove_file(f).unwrap();
    }

    // Tico agrees: the CLI login, the key file and what Tico now hands out are the same key, and the fetch after
    // it changes nothing.
    assert_eq!(
        use_key(next),
        ("done".into(), "The new key is in use".into())
    );
    assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-next-0123456789");
    assert_eq!(
        std::fs::read_to_string(&auth).unwrap(),
        "apikey sk-next-0123456789\n"
    );
    assert!(!read_env(&hand).contains_key("OPENAI_API_KEY") && no_side());
    replies.lock().unwrap().0 = next.into();
    sign.refresh_now("codex");
    assert!(!rt.block_on(sign.team_model_keys(&runner.client, "codex")));
    assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-next-0123456789");

    // A new key the provider refuses: the old key and its login are back.
    let refused = use_key(
        r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-bad-0123456789","source":"computer"}]}"#,
    );
    assert_eq!(
        refused,
        (
            "failed".into(),
            "The new key did not sign codex in; the old key is still in use".into()
        )
    );
    assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-next-0123456789");
    assert_eq!(
        std::fs::read_to_string(&auth).unwrap(),
        "apikey sk-next-0123456789\n"
    );
    assert!(!sign.signed_out("codex"));

    // From a cleared key, a refused one leaves it cleared and signed out.
    let clear = session("clear_key");
    run_clear_key(clear.clone(), runner.clone(), config.clone());
    assert_eq!(state(&clear).0, "signed_out");
    let refused = use_key(
        r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-bad-0123456789","source":"computer"}]}"#,
    );
    assert_eq!(
        refused,
        (
            "failed".into(),
            "The new key did not sign codex in; still no key".into()
        )
    );
    assert!(sign.signed_out("codex") && !read_env(&computer).contains_key("OPENAI_API_KEY"));
    assert!(sign.headless_secret("codex").is_none());
    // From a cleared key, a cancel leaves it cleared, signed out and with no login.
    let (canceled, running) = start_use_key(next, Some(false));
    running.join().unwrap();
    assert_eq!(state(&canceled).0, "cancelled");
    assert!(
        sign.signed_out("codex")
            && !read_env(&computer).contains_key("OPENAI_API_KEY")
            && !auth.exists()
            && no_side()
    );

    // No key staged for the job, or a shared one: nothing changes.
    let missing = use_key(
        r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-team-0123456789","source":"shared"}]}"#,
    );
    assert_eq!(
        missing,
        (
            "failed".into(),
            "The new key was not found; add it again".into()
        )
    );
    assert!(sign.signed_out("codex"));

    // Only the computer's own login holds a key, and only for a runtime that takes one; nothing runs.
    let mut logins = Logins::default();
    logins.begin(
        &runner,
        &json!({"id": "lprofile", "runtime": "codex", "kind": "clear_key", "profile": "lab"}),
    );
    logins.begin(
        &runner,
        &json!({"id": "lgemini", "runtime": "gemini", "kind": "use_key"}),
    );
    assert!(crate::signin::headless_names("gemini").is_empty());
    assert_eq!(state(&logins.sessions["lprofile"]).0, "failed");
    assert_eq!(
        state(&logins.sessions["lgemini"]),
        (
            "failed".into(),
            "This computer does not hold a key for gemini".into()
        )
    );

    // Its own key deleted in Credentials with no shared key behind it: Tico answers the next fetch as for Clear key,
    // and the login that key made stops working here.
    sign.unmark_signed_out("codex").unwrap();
    std::fs::write(&computer, "OPENAI_API_KEY=sk-mine-0123456789\n").unwrap();
    std::fs::write(&auth, "apikey sk-mine-0123456789\n").unwrap();
    assert_eq!(
        sign.key_source("codex"),
        Some(("OPENAI_API_KEY".into(), "computer"))
    );
    replies.lock().unwrap().0 = r#"{"credentials":[],"signed_out":true,"clear_key":true}"#.into();
    sign.refresh_now("codex");
    assert!(rt.block_on(sign.team_model_keys(&runner.client, "codex")));
    assert!(
        !auth.exists()
            && sign.headless_secret("codex").is_none()
            && !read_env(&computer).contains_key("OPENAI_API_KEY")
    );
    let codex = root.join("bin/codex").display().to_string();
    assert!(
        !signed_in("codex", &codex, &key_cli("codex").unwrap().1, false).0,
        "the deleted key's login still works"
    );

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
        let root =
            std::env::temp_dir().join(format!("tico-to-key-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join("bin")).unwrap();
        crate::util::write_script(&root.join("bin/codex"), FAKE_CODEX);
        crate::util::write_script(&root.join("bin/claude"), FAKE_CLAUDE);
        let path = format!("{}:/usr/bin:/bin", root.join("bin").display());
        let out = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "login::tests::switch_to_an_api_key_on_the_computers_own_codex_and_claude_logins",
                "--nocapture",
            ])
            .env(CHILD, &root)
            .env("PATH", path)
            .env("HOME", &root)
            .env("CODEX_HOME", root.join("codex"))
            .env_remove(crate::isolation::UID_ENV)
            .env_remove(crate::isolation::GID_ENV)
            .env_remove("CLAUDE_CONFIG_DIR")
            .env_remove("OPENAI_API_KEY")
            .env_remove("ANTHROPIC_API_KEY")
            .env_remove("CLAUDE_CODE_OAUTH_TOKEN")
            .output()
            .unwrap();
        std::fs::remove_dir_all(&root).ok();
        let said = String::from_utf8_lossy(&out.stdout);
        assert!(
            out.status.success() && said.contains("1 passed"),
            "{said}{}",
            String::from_utf8_lossy(&out.stderr)
        );
        return;
    };
    use crate::signin::{read_env, COMPUTER_KEYS_FILE, HAND_KEYS_FILE};
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
        .unwrap();
    let (url, replies, _) = key_server(&rt);
    let secrets = root.join("projects/secrets");
    std::fs::create_dir_all(&secrets).unwrap();
    std::fs::create_dir_all(root.join("codex")).unwrap();
    std::fs::create_dir_all(root.join(".claude")).unwrap();
    let path = root.join("runner.json");
    std::fs::write(
        &path,
        json!({"url": url, "token": "t", "runner_id": "r1", "projects_dir": root.join("projects")})
            .to_string(),
    )
    .unwrap();
    let config = Config::load(&path).unwrap();
    let client = crate::client::Client::new(&config.url(), &config.token(), 5, 0).unwrap();
    let runner = Arc::new(Runner::new(
        config.clone(),
        client,
        crate::state::State::open(&root.join("state")).unwrap(),
    ));
    let sign = &runner.sign_in;
    let (auth, creds, runs) = (
        root.join("codex/auth.json"),
        root.join(".claude/.credentials.json"),
        root.join("runs"),
    );
    let (computer, hand) = (
        secrets.join(COMPUTER_KEYS_FILE),
        secrets.join(HAND_KEYS_FILE),
    );
    let logouts = || {
        std::fs::read_to_string(&runs)
            .unwrap_or_default()
            .lines()
            .filter(|l| *l == "logout" || *l == "auth logout")
            .count()
    };
    let no_side = || {
        std::fs::read_dir(root.join("codex"))
            .unwrap()
            .flatten()
            .all(|e| !e.file_name().to_string_lossy().starts_with(".tico-switch-"))
    };
    // A to_key job on its own thread, the key Tico staged for it in `reply`, with Tico's answer once it is finishing.
    let to_key = |runtime: &str, reply: &str, server: Option<bool>| {
        replies.lock().unwrap().1 = reply.into();
        let s = Session::new(
            &json!({"id": format!("l{}", uuid::Uuid::new_v4().simple()), "runtime": runtime, "kind": "to_key"}),
        );
        let (s2, r2, c2, h) = (
            s.clone(),
            runner.clone(),
            config.clone(),
            rt.handle().clone(),
        );
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
    let subscription_in_use = || {
        std::fs::read_to_string(&auth).unwrap() == "chatgpt\n"
            && sign.key_off("codex")
            && sign.headless_secret("codex").is_none()
            && !read_env(&computer).contains_key("OPENAI_API_KEY")
    };
    // Canceled before its commit point, or a key the provider refuses: the subscription stays signed in and in use.
    assert_eq!(
        to_key("codex", codex_key, Some(false)),
        ("cancelled".into(), "Canceled".into())
    );
    assert!(subscription_in_use() && no_side());
    let bad = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-bad-0123456789","source":"computer"}]}"#;
    assert_eq!(
        to_key("codex", bad, Some(true)),
        (
            "failed".into(),
            "The new key did not sign codex in; the subscription is still in use".into()
        )
    );
    assert!(subscription_in_use() && no_side());
    // Past the commit point a step fails (the terminal's key file cannot be rewritten, after this computer's key was
    // saved and the key-off mark lifted): everything it touched goes back, the subscription's login with it.
    std::fs::write(&hand, "OPENAI_API_KEY=sk-hand-0123456789\nKEEP=me\n").unwrap();
    let unwritable = secrets.join(format!("{HAND_KEYS_FILE}.tmp"));
    std::fs::create_dir(&unwritable).unwrap();
    let late = to_key("codex", codex_key, Some(true));
    assert_eq!(late.0, "failed", "{late:?}");
    assert!(
        late.1.ends_with("so the subscription is still in use"),
        "{late:?}"
    );
    assert!(
        subscription_in_use(),
        "a failed switch left something changed"
    );
    assert_eq!(
        std::fs::read_to_string(&hand).unwrap(),
        "OPENAI_API_KEY=sk-hand-0123456789\nKEEP=me\n"
    );
    assert!(no_side());
    std::fs::remove_dir(&unwritable).unwrap();
    std::fs::remove_file(&hand).unwrap();
    // Works: Codex keeps both logins in one auth.json, so the key's login has replaced the subscription's and is not
    // logged out (that would delete the new key's login). The key-off mark is lifted: the next turn has the key.
    let before = logouts();
    assert_eq!(
        to_key("codex", codex_key, Some(true)),
        (
            "done".into(),
            "Switched to the API key; the subscription is signed out".into()
        )
    );
    assert_eq!(
        std::fs::read_to_string(&auth).unwrap(),
        "apikey sk-key-0123456789\n"
    );
    assert_eq!(logouts(), before, "the key's login was logged out");
    assert!(!sign.key_off("codex") && !sign.signed_out("codex") && no_side());
    assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-key-0123456789");
    assert_eq!(
        sign.headless_secret("codex"),
        Some(("OPENAI_API_KEY".into(), "sk-key-0123456789".into())),
        "the next turn's key"
    );

    // Claude Code on a subscription: its login and the key are kept apart, so the subscription is logged out and the
    // key stays in this computer's key file. Before anything changes the key is checked with Anthropic, in a home
    // with no login (the stand-in refuses to answer for the subscription).
    let (subscription, settings) = (r#"{"sub":1}"#, r#"{"settings":1}"#);
    std::fs::write(&creds, subscription).unwrap();
    std::fs::write(root.join(".claude.json"), settings).unwrap();
    sign.mark_key_off("claude").unwrap();
    let claude_key = r#"{"credentials":[{"env":"ANTHROPIC_API_KEY","value":"sk-ant-key-0123456789","source":"computer"}]}"#;
    if crate::harness_tools::login_command("claude")
        .unwrap()
        .keychain
        .iter()
        .any(|p| p == crate::runner::platform())
    {
        // A file fixture cannot stand in for a reversible Keychain logout. This platform must refuse before
        // touching either account; the file-backed platforms exercise the transactional path below.
        let before = std::fs::read_to_string(&runs).unwrap_or_default();
        let result = to_key("claude", claude_key, None);
        assert_eq!(result, ("failed".into(), "Switching to an API key isn't available for claude on this computer: sign out, then add a key".into()));
        assert_eq!(std::fs::read_to_string(&creds).unwrap(), subscription);
        assert_eq!(
            std::fs::read_to_string(root.join(".claude.json")).unwrap(),
            settings
        );
        assert!(sign.key_off("claude") && sign.headless_secret("claude").is_none());
        assert_eq!(
            read_env(&computer),
            [("OPENAI_API_KEY".into(), "sk-key-0123456789".into())]
                .into_iter()
                .collect()
        );
        assert_eq!(
            std::fs::read_to_string(&runs).unwrap_or_default(),
            before,
            "no CLI runs at this boundary"
        );
        assert!(
            no_side()
                && std::fs::read_dir(&root)
                    .unwrap()
                    .flatten()
                    .all(|e| !e.file_name().to_string_lossy().starts_with(".tico-switch-"))
        );
        return;
    }
    let flag = |name: &str, on: bool| {
        if on {
            std::fs::write(root.join(name), "").unwrap()
        } else {
            std::fs::remove_file(root.join(name)).unwrap()
        }
    };
    let claude_side = || {
        std::fs::read_dir(&root)
            .unwrap()
            .flatten()
            .any(|e| e.file_name().to_string_lossy().starts_with(".tico-switch-"))
    };
    let still_subscription = |why: &str| {
        assert_eq!(
            std::fs::read_to_string(&creds).ok().as_deref(),
            Some(subscription),
            "{why}: the subscription's login"
        );
        assert_eq!(
            std::fs::read_to_string(root.join(".claude.json")).unwrap(),
            settings,
            "{why}: its settings file"
        );
        assert!(
            sign.key_off("claude") && sign.headless_secret("claude").is_none(),
            "{why}: the key-off mark"
        );
        assert!(
            !read_env(&computer).contains_key("ANTHROPIC_API_KEY"),
            "{why}: no key saved"
        );
        assert_eq!(
            read_env(&computer)["OPENAI_API_KEY"],
            "sk-key-0123456789",
            "{why}: another runtime's key was touched"
        );
        assert!(!claude_side(), "{why}: the side home was left");
    };
    assert_eq!(to_key("claude", claude_key, Some(false)).0, "cancelled");
    still_subscription("cancelled");
    // A key Anthropic refuses: failed before the commit point, nothing logged out.
    let before = logouts();
    let refused = r#"{"credentials":[{"env":"ANTHROPIC_API_KEY","value":"sk-ant-bad-0123456789","source":"computer"}]}"#;
    assert_eq!(
        to_key("claude", refused, None),
        (
            "failed".into(),
            "The new key did not work for claude; the subscription is still in use".into()
        )
    );
    still_subscription("refused key");
    // A status check that says nothing readable is not "signed out": before the logout nothing is logged out ...
    flag("broken", true);
    let unknown = to_key("claude", claude_key, Some(true));
    assert_eq!(unknown, ("failed".into(), "Could not check whether claude is signed in to its subscription, so the subscription is still in use".into()));
    assert_eq!(
        logouts(),
        before,
        "logged out on a status check that said nothing"
    );
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
    for said in [
        r#"{"type":"error","error":"Something went wrong"}"#,
        r#"{"loggedIn": null}"#,
        r#"{"loggedIn": "false"}"#,
    ] {
        std::fs::write(root.join("status"), said).unwrap();
        let unknown = to_key("claude", claude_key, Some(true));
        assert_eq!(unknown, ("failed".into(), "Could not check whether claude is signed in to its subscription, so the subscription is still in use".into()), "{said}");
        assert_eq!(
            logouts(),
            before,
            "logged out when the status check said {said}"
        );
        flag("status", false);
        still_subscription(said);
    }
    // ... and after it.
    std::fs::write(
        root.join("status-after"),
        r#"{"loggedIn": null, "authMethod": "claude.ai"}"#,
    )
    .unwrap();
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
    assert_eq!(
        logouts(),
        before,
        "logged out with a login file that could not be kept aside"
    );
    assert_eq!(
        std::fs::read_link(&link).unwrap(),
        target,
        "the link was changed"
    );
    assert_eq!(
        std::fs::read_to_string(&target).unwrap(),
        settings,
        "the file the link points at was changed"
    );
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
    assert_eq!(
        sign.headless_secret("claude"),
        Some(("ANTHROPIC_API_KEY".into(), "sk-ant-key-0123456789".into())),
        "the next turn's key"
    );
    assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-key-0123456789");
    // An uncertain logout keeps the explicit block on saved keys; sign-out retains a computer-owned key on disk.
    std::fs::write(&creds, subscription).unwrap();
    flag("break-after", true);
    let out = Session::new(&json!({"id": "lunknown", "runtime": "claude", "kind": "sign_out"}));
    run_sign_out(out.clone(), runner.clone(), config.clone());
    assert_eq!(
        state(&out),
        (
            "failed".into(),
            "The logout finished but could not confirm whether claude signed out".into()
        )
    );
    assert!(sign.signed_out("claude") && sign.headless_secret("claude").is_none());
    assert!(!creds.exists());
    assert_eq!(
        read_env(&computer)["ANTHROPIC_API_KEY"],
        "sk-ant-key-0123456789",
        "sign-out blocks, but does not clear, a computer-owned key"
    );
    assert_eq!(read_env(&computer)["OPENAI_API_KEY"], "sk-key-0123456789");
}

#[test]
fn remove_waits_for_tico_runs_alone_on_the_subscription_and_keeps_a_login_it_cannot_check() {
    const CHILD: &str = "TICO_TEST_REMOVE";
    let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
        // A process of its own: the stand-in codex is the only CLI on PATH, so Claude Code cannot be checked.
        let root =
            std::env::temp_dir().join(format!("tico-remove-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join("bin")).unwrap();
        std::fs::write(root.join("bin/codex"), FAKE_CODEX).unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(
            root.join("bin/codex"),
            std::fs::Permissions::from_mode(0o755),
        )
        .unwrap();
        let path = format!("{}:/usr/bin:/bin", root.join("bin").display());
        let out = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "login::tests::remove_waits_for_tico_runs_alone_on_the_subscription_and_keeps_a_login_it_cannot_check", "--nocapture"])
            .env(CHILD, &root).env("PATH", path).env("HOME", &root).env_remove("CODEX_HOME")
            .env_remove(crate::isolation::UID_ENV).env_remove(crate::isolation::GID_ENV)
            .env_remove("OPENAI_API_KEY").output().unwrap();
        std::fs::remove_dir_all(&root).ok();
        let said = String::from_utf8_lossy(&out.stdout);
        assert!(
            out.status.success() && said.contains("1 passed"),
            "{said}{}",
            String::from_utf8_lossy(&out.stderr)
        );
        return;
    };
    let path = root.join("runner.json");
    std::fs::write(&path, json!({"url": "http://127.0.0.1:9", "token": "t", "runner_id": "r1", "projects_dir": root.join("projects")}).to_string()).unwrap();
    let config = Config::load(&path).unwrap();
    let client = crate::client::Client::new(&config.url(), &config.token(), 1, 0).unwrap();
    let runner = Arc::new(Runner::new(
        config.clone(),
        client,
        crate::state::State::open(&root.join("state")).unwrap(),
    ));
    crate::profiles::add_profile(&config, "work").unwrap();
    let folder = root.join("profiles/work");
    let (codex, claude) = (
        folder.join("codex/auth.json"),
        folder.join("claude/.claude.json"),
    );
    std::fs::write(&codex, "work\n").unwrap();
    std::fs::write(&claude, "{}").unwrap();
    let listed = || {
        Config::load(&path).unwrap().data["profiles"]
            .get("work")
            .is_some()
    };
    let logouts = || {
        std::fs::read_to_string(root.join("runs"))
            .unwrap_or_default()
            .matches("logout")
            .count()
    };
    let remove = |server: bool| {
        let s = Session::new(
            &json!({"id": format!("l{}", uuid::Uuid::new_v4().simple()), "runtime": "codex", "kind": "remove", "profile": "work"}),
        );
        // As the relay hands it over: runner.json read again, with the profile `add_profile` wrote.
        let (s2, r2, c2) = (s.clone(), runner.clone(), Config::load(&path).unwrap());
        let running = std::thread::spawn(move || run_remove(s2, r2, c2));
        wait_for(|| state(&s).0 == "finishing" || !s.live());
        if server {
            s.confirmed.store(true, Ordering::SeqCst)
        } else {
            s.canceled.store(true, Ordering::SeqCst)
        }
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
    assert_eq!(
        kept,
        (
            "done".into(),
            "codex signed out of work; it stays on this computer for Claude Code".into()
        )
    );
    assert!(!codex.exists() && claude.exists() && listed());

    // While a removal runs nothing else starts on that subscription, and it does not start while something does.
    let mut logins = Logins::default();
    let held = Session::new(
        &json!({"id": "lheld", "runtime": "claude", "kind": "sign_in", "profile": "work"}),
    );
    logins.sessions.insert(held.id.clone(), held);
    logins.begin(
        &runner,
        &json!({"id": "lrm", "runtime": "codex", "kind": "remove", "profile": "work"}),
    );
    assert_eq!(
        state(&logins.sessions["lrm"]),
        (
            "failed".into(),
            "Something else is running on work; try again when it ends".into()
        )
    );
    let mut logins = Logins::default();
    let held = Session::new(
        &json!({"id": "lheld", "runtime": "codex", "kind": "remove", "profile": "work"}),
    );
    logins.sessions.insert(held.id.clone(), held);
    // A named subscription never signs in any more: one login per tool per computer.
    logins.begin(
        &runner,
        &json!({"id": "lin", "runtime": "claude", "kind": "sign_in", "profile": "work"}),
    );
    assert!(state(&logins.sessions["lin"])
        .1
        .starts_with("Each computer has one login per AI tool"));
    assert!(claude.exists() && listed());

    // Every other tool surely signed out: the subscription leaves runner.json and its folder goes.
    std::fs::remove_file(&claude).unwrap();
    std::fs::write(&codex, "work\n").unwrap();
    assert_eq!(remove(true), ("done".into(), "Removed work".into()));
    assert!(!folder.exists() && !listed());
}

#[test]
fn muse_sign_out_and_switch_preserve_each_logins_files() {
    const CHILD: &str = "TICO_TEST_MUSE_SWITCH";
    let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
        // A separate process keeps every home, credential and stand-in CLI isolated from the runner and other tests.
        let root = std::env::temp_dir().join(format!(
            "tico-muse-switch-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(root.join("bin")).unwrap();
        std::fs::write(root.join("bin/muse"), r#"#!/bin/sh
home="$XDG_CONFIG_HOME/muse"
if [ "$TBH_CREDENTIAL_BACKEND" != file ]; then
  # As on macOS by default: the token goes to a Keychain item outside the config folder (here one item, the worst case).
  case "$1" in
    login) mkdir -p "$home"; echo "$XDG_CONFIG_HOME" > "$HOME/keychain"
      printf '%s\n' '{"schema_version":2,"providers":{"meta":{"storage":"keychain"}}}' > "$home/auth.json" ;;
    logout) rm -f "$HOME/keychain" ;;
  esac
  exit 0
fi
case "$1" in
  logout)
    if [ -f "$HOME/broken" ]; then echo malformed > "$home/auth.json"
    else printf '%s\n' '{"schema_version":1,"providers":{}}' > "$home/auth.json"; fi ;;
  login)
    mkdir -p "$home"; printf '%s\n' '{"schema_version":1,"providers":{"meta":{"api_key":"new"}}}' > "$home/auth.json"
    if [ -f "$HOME/hold" ]; then
      echo ready > "$HOME/started"
      while [ -f "$HOME/hold" ]; do sleep 0.05; done
    fi ;;
  *) exit 2 ;;
esac
"#).unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(
            root.join("bin/muse"),
            std::fs::Permissions::from_mode(0o755),
        )
        .unwrap();
        let out = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "login::tests::muse_sign_out_and_switch_preserve_each_logins_files",
                "--nocapture",
            ])
            .env_clear()
            .env(CHILD, &root)
            .env("HOME", &root)
            .env(
                "PATH",
                format!("{}:/usr/bin:/bin", root.join("bin").display()),
            )
            .env("XDG_CONFIG_HOME", root.join("operator"))
            .env("MUSE_AUTH_PATH", root.join("operator/auth.json"))
            .output()
            .unwrap();
        std::fs::remove_dir_all(&root).ok();
        let said = String::from_utf8_lossy(&out.stdout);
        assert!(
            out.status.success() && said.contains("1 passed"),
            "{said}{}",
            String::from_utf8_lossy(&out.stderr)
        );
        return;
    };
    crate::hosts::musecode::set_state_dir(&root.join("host-state"));
    std::fs::create_dir_all(root.join("operator")).unwrap();
    let operator = root.join("operator/auth.json");
    std::fs::write(&operator, "operator").unwrap();
    let keychain = root.join("keychain");
    std::fs::write(&keychain, "operator").unwrap();
    let path = root.join("runner.json");
    std::fs::write(
        &path,
        json!({"url": "http://127.0.0.1:9", "token": "synthetic", "runner_id": "fixture",
        "projects_dir": root.join("projects")})
        .to_string(),
    )
    .unwrap();
    crate::profiles::add_profile(&Config::load(&path).unwrap(), "acme").unwrap();
    let config = Config::load(&path).unwrap();
    let client = crate::client::Client::new(&config.url(), &config.token(), 1, 0).unwrap();
    let runner = Arc::new(Runner::new(
        config.clone(),
        client,
        crate::state::State::open(&root.join("state")).unwrap(),
    ));
    let own = crate::hosts::musecode::config_home(None).join("muse/auth.json");
    let named = root.join("profiles/acme/musecode/muse/auth.json");
    let old = "{\"schema_version\":1,\"providers\":{\"meta\":{\"api_key\":\"old\"}}}\n";
    let new = "{\"schema_version\":1,\"providers\":{\"meta\":{\"api_key\":\"new\"}}}\n";
    for auth in [&own, &named] {
        std::fs::create_dir_all(auth.parent().unwrap()).unwrap();
        std::fs::write(auth, old).unwrap();
    }
    for (profile, auth, other) in [("", &own, &named), ("acme", &named, &own)] {
        let session = |kind: &str| {
            Session::new(&json!({"id": format!("l{}", uuid::Uuid::new_v4().simple()),
            "runtime": "musecode", "kind": kind, "profile": profile}))
        };
        let untouched = std::fs::read(other).unwrap();
        // An unreadable verdict is not confirmation of sign-out.
        std::fs::write(root.join("broken"), "").unwrap();
        let broken = session("sign_out");
        run_sign_out(broken.clone(), runner.clone(), config.clone());
        // The credential boundary first: a logout from Tico's folder never reaches the item outside it.
        assert_eq!(
            std::fs::read_to_string(&keychain).ok().as_deref(),
            Some("operator"),
            "a Tico logout reached the Keychain"
        );
        assert_eq!(state(&broken).0, "failed", "{:?}", state(&broken));
        assert!(state(&broken).1.contains("could not confirm"));
        assert_eq!(runner.sign_in.signed_out("musecode"), profile.is_empty());
        std::fs::remove_file(root.join("broken")).unwrap();
        std::fs::write(auth, old).unwrap();
        let out = session("sign_out");
        run_sign_out(out.clone(), runner.clone(), config.clone());
        assert_eq!(state(&out).0, "signed_out", "{:?}", state(&out));
        assert!(auth.exists(), "Muse logout keeps an empty credential store");
        let (exe, env, _) = login_env("musecode", &config, profile).unwrap();
        assert_eq!(sign_in_status("musecode", &exe, &env).unwrap().0, false);
        assert_eq!(std::fs::read(other).unwrap(), untouched);

        // A canceled sign-in has already written a new file on the side; the old file must stay in use.
        std::fs::write(auth, old).unwrap();
        std::fs::write(root.join("hold"), "").unwrap();
        let canceled = session("switch");
        let (s, r, c) = (canceled.clone(), runner.clone(), config.clone());
        let running = std::thread::spawn(move || run_session(s, r, c, Duration::from_secs(20)));
        wait_for(|| root.join("started").exists());
        assert_eq!(std::fs::read_to_string(auth).unwrap(), old);
        canceled.canceled.store(true, Ordering::SeqCst);
        running.join().unwrap();
        assert_eq!(state(&canceled).0, "cancelled", "{:?}", state(&canceled));
        assert_eq!(std::fs::read_to_string(auth).unwrap(), old);
        std::fs::remove_file(root.join("hold")).unwrap();
        std::fs::remove_file(root.join("started")).unwrap();

        // A completed login moves into place only after the server confirms the switch.
        let switched = session("switch");
        answer_finishing(&switched, true);
        run_session(
            switched.clone(),
            runner.clone(),
            config.clone(),
            Duration::from_secs(20),
        );
        assert_eq!(state(&switched).0, "signed_in", "{:?}", state(&switched));
        assert_eq!(std::fs::read_to_string(auth).unwrap(), new);
        assert_eq!(std::fs::read(other).unwrap(), untouched);
        assert_eq!(std::fs::read_to_string(&operator).unwrap(), "operator");
        let home = auth.parent().unwrap().parent().unwrap();
        assert!(!std::fs::read_dir(home)
            .unwrap()
            .flatten()
            .any(|e| e.file_name().to_string_lossy().starts_with(".tico-switch-")));
    }
    // Remove needs an explicit signed-out result before deleting a Tico-made subscription folder.
    let folder = root.join("profiles/acme");
    let listed = || {
        Config::load(&path).unwrap().data["profiles"]
            .get("acme")
            .is_some()
    };
    let untouched = std::fs::read(&own).unwrap();
    let remove = || {
        let s = Session::new(&json!({"id": format!("l{}", uuid::Uuid::new_v4().simple()),
            "runtime": "musecode", "kind": "remove", "profile": "acme"}));
        answer_finishing(&s, true);
        run_remove(s.clone(), runner.clone(), Config::load(&path).unwrap());
        state(&s)
    };
    std::fs::write(root.join("broken"), "").unwrap();
    let broken = remove();
    assert_eq!(
        broken.0, "failed",
        "malformed logout must keep the subscription: {broken:?}"
    );
    assert!(
        listed() && folder.is_dir() && named.is_file(),
        "unknown sign-out deleted the subscription"
    );
    assert_eq!(std::fs::read_to_string(&named).unwrap(), "malformed\n");
    std::fs::remove_file(root.join("broken")).unwrap();
    std::fs::write(&named, old).unwrap();
    assert_eq!(remove(), ("done".into(), "Removed acme".into()));
    assert!(
        !listed() && !folder.exists(),
        "confirmed sign-out must remove the subscription and folder"
    );
    assert_eq!(std::fs::read(&own).unwrap(), untouched);
    assert_eq!(std::fs::read_to_string(&operator).unwrap(), "operator");
    assert_eq!(
        std::fs::read_to_string(&keychain).unwrap(),
        "operator",
        "a Tico login reached the shared Keychain"
    );
}

/// Run only on a disposable Linux box: no provider connection or real credential is used.
#[cfg(target_os = "linux")]
#[test]
#[ignore = "requires a disposable Linux two-user environment"]
fn isolated_grok_computer_login_repairs_supervisor_owned_store() {
    use std::os::unix::fs::{chown, MetadataExt, PermissionsExt};
    const CHILD: &str = "TICO_TEST_GROK_PERMISSIONS";
    assert_eq!(
        unsafe { libc::geteuid() },
        0,
        "disposable supervisor fixture"
    );
    assert!(std::env::var_os("TICO_TEST_DISPOSABLE").is_some());
    assert_eq!(crate::isolation::identity(), Some((10003, 10002)));
    let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
        let root = std::env::temp_dir().join(format!(
            "tico-grok-permissions-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(root.join("bin")).unwrap();
        std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o755)).unwrap();
        crate::util::write_script(
            &root.join("bin/grok"),
            r#"#!/usr/bin/python3
import json, os, pathlib, sys
if sys.argv[1:] == ['--version']:
    print('1.0.70')
    sys.exit(0)
def session(name):
    store = pathlib.Path(os.environ['HOME']) / '.grok/sessions'
    store.mkdir(parents=True, exist_ok=True)
    (store / (name + '.json')).write_text('{}')
if sys.argv[1:] == ['session']:
    session('synthetic')
    sys.exit(0)
if sys.argv[1:2] == ['agent']:
    for line in sys.stdin:
        request = json.loads(line)
        try:
            if request['method'] == 'initialize':
                result = {'protocolVersion': 1}
            else:
                session(request['method'].split('/')[1])
                result = {'sessionId': str(os.getpid())}
            reply = {'result': result}
        except PermissionError:
            reply = {'error': {'message': 'Failed to save credentials: Permission denied (os error 13)'}}
        print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], **reply}), flush=True)
    sys.exit(0)
if sys.argv[1:] not in (['login', '--device-auth'], ['refresh']):
    sys.exit(2)
print('Open https://login.example/device\nEnter code ABCD-1234', flush=True)
try:
    store = pathlib.Path(os.environ['HOME']) / '.grok/auth.json'
    store.write_text(json.dumps({'https://accounts.x.ai/sign-in': {'auth_mode': 'oidc', 'key': 'synthetic'}}))
    store.chmod(0o600)
except PermissionError:
    print('Error: Failed to save credentials: Permission denied (os error 13)', file=sys.stderr)
    sys.exit(1)
"#,
        );
        let out = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "login::tests::isolated_grok_computer_login_repairs_supervisor_owned_store",
                "--ignored",
                "--nocapture",
            ])
            .env(CHILD, &root)
            .env("HOME", &root)
            .env(
                "PATH",
                format!("{}:/usr/bin:/bin", root.join("bin").display()),
            )
            .env_remove("GROK_HOME")
            .env_remove("GROK_AUTH_PATH")
            .env_remove("XAI_API_KEY")
            .output()
            .unwrap();
        std::fs::remove_dir_all(&root).unwrap();
        assert!(
            out.status.success(),
            "{}{}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        );
        return;
    };
    let path = root.join("runner.json");
    std::fs::write(&path, json!({"url": "http://127.0.0.1:9", "token": "synthetic", "runner_id": "r1", "projects_dir": root.join("projects")}).to_string()).unwrap();
    let config = Config::load(&path).unwrap();
    let client = crate::client::Client::new(&config.url(), &config.token(), 1, 0).unwrap();
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    let home = root.join(".grok");
    let auth = home.join("auth.json");
    // An existing 0755 folder (the incident), a private old store, and both entry points:
    // startup repairs an existing login; login_env repairs a folder created after startup.
    for (mode, existing, startup) in [
        (0o755, false, false),
        (0o700, true, false),
        (0o755, true, true),
    ] {
        std::fs::create_dir_all(&home).unwrap();
        std::fs::set_permissions(&home, std::fs::Permissions::from_mode(mode)).unwrap();
        chown(&home, Some(10002), Some(10002)).unwrap();
        let old = b"synthetic old store";
        let inode = if existing {
            std::fs::write(&auth, old).unwrap();
            std::fs::set_permissions(&auth, std::fs::Permissions::from_mode(0o600)).unwrap();
            chown(&auth, Some(10002), Some(10002)).unwrap();
            Some(auth.metadata().unwrap().ino())
        } else {
            None
        };
        let env: Env = std::env::vars().collect();
        let before = model_command(
            root.join("bin/grok").to_str().unwrap(),
            &["login", "--device-auth"],
            &env,
        )
        .output()
        .unwrap();
        assert!(!before.status.success());
        assert!(String::from_utf8_lossy(&before.stderr)
            .contains("Failed to save credentials: Permission denied (os error 13)"));
        if startup {
            crate::hosts::grok::hand_login_to_bot();
        }
        let (exe, env, profile) = login_env("grok", &config, "").unwrap();
        assert!(profile.is_none());
        assert_eq!(home.metadata().unwrap().uid(), 10003);
        assert_eq!(
            home.metadata().unwrap().mode() & 0o777,
            mode,
            "no permission broadening"
        );
        if let Some(inode) = inode {
            assert_eq!(
                auth.metadata().unwrap().ino(),
                inode,
                "handoff keeps the original file"
            );
            assert_eq!(std::fs::read(&auth).unwrap(), old);
            assert_eq!(
                (
                    auth.metadata().unwrap().uid(),
                    auth.metadata().unwrap().mode() & 0o777
                ),
                (10003, 0o600)
            );
        }
        let runner = Arc::new(Runner::new(
            config.clone(),
            client.clone(),
            crate::state::State::open(&root.join("state")).unwrap(),
        ));
        let login = Session::new(
            &json!({"id": uuid::Uuid::new_v4().simple().to_string(), "runtime": "grok"}),
        );
        run_session(
            login.clone(),
            runner.clone(),
            config.clone(),
            Duration::from_secs(5),
        );
        assert_eq!(state(&login).0, "signed_in", "{:?}", state(&login));
        assert_eq!(auth.metadata().unwrap().uid(), 10003);
        // The same account can refresh in place, and readiness reads that same store.
        assert!(model_command(&exe, &["refresh"], &env)
            .status()
            .unwrap()
            .success());
        assert_eq!(sign_in_status("grok", &exe, &env).unwrap().0, true);
        let ready = rt.block_on(crate::readiness::runtime_readiness(
            "grok",
            &runner.sign_in,
            &runner.client,
            &[],
            None,
        ));
        assert_eq!(ready["authenticated"], "ready");
        assert!(!root.join("projects/bots").exists(), "no copied bot login");
        std::fs::remove_dir_all(&home).unwrap();
    }
    // An API-key turn still writes sessions, without passing through the interactive login path.
    std::fs::create_dir(&home).unwrap();
    chown(&home, Some(10002), Some(10002)).unwrap();
    let mut env: Env = std::env::vars().collect();
    env.insert("XAI_API_KEY".into(), "synthetic".into());
    let exe = root.join("bin/grok").display().to_string();
    assert!(!model_command(&exe, &["session"], &env)
        .output()
        .unwrap()
        .status
        .success());
    rt.block_on(async {
        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        let host = crate::hosts::acp_host::AcpHost::new(
            "acme",
            "grok",
            &exe,
            crate::harness_tools::acp_spec("grok").unwrap(),
            tx,
        );
        let settings = crate::hosts::Settings {
            env: env.clone(),
            cwd: root.display().to_string(),
            ..Default::default()
        };
        // Launch the actual host path with an API key, without interactive login.
        let thread = host.start_thread("acme", &settings).await.unwrap();
        assert!(host.alive());
        assert_eq!(
            home.join("sessions/new.json").metadata().unwrap().uid(),
            10003
        );
        // Recreate the incident after the process is already running. Both new and resumed
        // threads must repair the store without restarting that process (session id = pid).
        for resume in [false, true] {
            std::fs::remove_dir_all(&home).unwrap();
            std::fs::create_dir(&home).unwrap();
            chown(&home, Some(10002), Some(10002)).unwrap();
            assert!(!model_command(&exe, &["session"], &env)
                .output()
                .unwrap()
                .status
                .success());
            let result = if resume {
                host.resume_thread(&thread, &settings).await
            } else {
                host.start_thread("acme", &settings).await
            };
            assert_eq!(result.unwrap(), thread, "the running agent must be reused");
            assert_eq!(home.metadata().unwrap().uid(), 10003);
            let file = if resume { "load.json" } else { "new.json" };
            assert_eq!(
                home.join("sessions").join(file).metadata().unwrap().uid(),
                10003
            );
        }
        host.stop().await;
    });
    std::fs::remove_dir_all(&home).unwrap();

    // A named subscription hands over only its profile home; a shared profile uses the operator home.
    std::fs::create_dir(&home).unwrap();
    chown(&home, Some(10002), Some(10002)).unwrap();
    let profiles = root.join("profiles");
    let named_home = profiles.join("acme/grok");
    std::fs::create_dir_all(named_home.join(".grok")).unwrap();
    std::fs::write(
        named_home.join(".grok/auth.json"),
        "synthetic old named store",
    )
    .unwrap();
    chown(named_home.join(".grok"), Some(10002), Some(10002)).unwrap();
    let mut named_config = config.clone();
    named_config.data.insert(
        "profiles".into(),
        json!({"acme": {"dir": profiles.join("acme")},
        "shared": {"dir": profiles.join("shared"), "share_operator": true}}),
    );
    let (_, named_env, _) = login_env("grok", &named_config, "acme").unwrap();
    assert_eq!(named_env["HOME"], named_home.display().to_string());
    assert_eq!(
        home.metadata().unwrap().uid(),
        10002,
        "named login must not hand over the operator store"
    );
    assert_eq!(named_home.join(".grok").metadata().unwrap().uid(), 10003);
    assert!(model_command(&exe, &["login", "--device-auth"], &named_env)
        .status()
        .unwrap()
        .success());
    assert!(std::fs::read_to_string(named_home.join(".grok/auth.json"))
        .unwrap()
        .contains("synthetic"));
    named_config
        .data
        .insert("default_profile".into(), json!("acme"));
    let (_, default_env, _) = login_env("grok", &named_config, "").unwrap();
    assert_eq!(default_env["HOME"], named_env["HOME"]);
    crate::hosts::grok::prepare_home(&named_env);
    assert_eq!(
        home.metadata().unwrap().uid(),
        10002,
        "profile turns must leave the operator store alone"
    );
    let (_, shared_env, _) = login_env("grok", &named_config, "shared").unwrap();
    assert_eq!(shared_env["HOME"], root.display().to_string());
    assert_eq!(home.metadata().unwrap().uid(), 10003);
    std::fs::remove_dir_all(&home).unwrap();

    // Ownership repair never traverses a link to unrelated supervisor state or a foreign hardlink.
    let outside = root.join("private");
    std::fs::create_dir(&outside).unwrap();
    let control = outside.join("control.json");
    std::fs::write(&control, "synthetic supervisor state").unwrap();
    std::fs::set_permissions(&control, std::fs::Permissions::from_mode(0o600)).unwrap();
    let mut custom_env: Env = std::env::vars().collect();
    custom_env.insert("GROK_HOME".into(), outside.display().to_string());
    custom_env.insert("GROK_AUTH_PATH".into(), control.display().to_string());
    crate::hosts::prepare_login_home("grok", &custom_env);
    assert_eq!(
        outside.metadata().unwrap().uid(),
        0,
        "overrides never redirect the privileged handoff"
    );
    assert_eq!(control.metadata().unwrap().uid(), 0);
    std::os::unix::fs::symlink(&outside, &home).unwrap();
    crate::hosts::grok::hand_login_to_bot();
    assert_eq!(outside.metadata().unwrap().uid(), 0);
    assert_eq!(control.metadata().unwrap().uid(), 0);
    std::fs::remove_file(&home).unwrap();
    std::fs::create_dir(&home).unwrap();
    std::os::unix::fs::symlink(&outside, home.join("linked-directory")).unwrap();
    std::os::unix::fs::symlink(&control, home.join("linked-file")).unwrap();
    crate::hosts::grok::hand_login_to_bot();
    assert_eq!(
        outside.metadata().unwrap().uid(),
        0,
        "links inside the store must not be traversed"
    );
    assert_eq!(control.metadata().unwrap().uid(), 0);
    std::fs::hard_link(&control, &auth).unwrap();
    crate::hosts::grok::hand_login_to_bot();
    assert_eq!(control.metadata().unwrap().uid(), 0);
    assert_eq!(control.metadata().unwrap().mode() & 0o777, 0o600);
}

#[test]
fn grok_computer_login_controls_stay_in_default_home() {
    subscription_account_controls(
        "grok",
        ".grok/auth.json",
        "login::tests::grok_computer_login_controls_stay_in_default_home",
    );
}

#[test]
fn cursor_computer_login_controls_stay_in_default_home() {
    let file = if cfg!(target_os = "macos") {
        ".cursor/auth.json"
    } else {
        ".config/cursor/auth.json"
    };
    subscription_account_controls(
        "cursor",
        file,
        "login::tests::cursor_computer_login_controls_stay_in_default_home",
    );
}

fn subscription_account_controls(runtime: &str, file: &str, test: &str) {
    const CHILD: &str = "TICO_TEST_SUBSCRIPTION_CONTROLS";
    let Some(root) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
        let root = std::env::temp_dir().join(format!(
            "tico-subscription-controls-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(root.join("bin")).unwrap();
        let exe = crate::harness_tools::executable_for(runtime);
        crate::util::write_script(
            &root.join("bin").join(exe),
            r#"#!/bin/sh
set -eu
case "$(basename "$0")" in
  grok) auth="${GROK_AUTH_PATH:-${GROK_HOME:-$HOME/.grok}/auth.json}"; login='login --device-auth' ;;
  cursor-agent)
    test "$AGENT_CLI_CREDENTIAL_STORE" = file
    test "$NO_OPEN_BROWSER" = 1
    case "$(uname)" in Darwin) auth="$HOME/.cursor/auth.json" ;; *) auth="${XDG_CONFIG_HOME:-$HOME/.config}/cursor/auth.json" ;; esac
    login=login ;;
esac
case "$*" in
  "$login")
    echo 'Open https://login.example/device'; echo 'Enter code ABCD-1234'
    while test ! -f "$TICO_TEST_SUBSCRIPTION_CONTROLS/continue"; do sleep 0.02; done
    mkdir -p "$(dirname "$auth")"
    echo '{"https://accounts.x.ai/sign-in":{"auth_mode":"oidc","key":"new"}}' > "$auth" ;;
  logout)
    if test -f "$TICO_TEST_SUBSCRIPTION_CONTROLS/logout-error"; then exit 2; fi
    if test -f "$TICO_TEST_SUBSCRIPTION_CONTROLS/logout-corrupt"; then echo '{broken' > "$auth"; else echo '{}' > "$auth"; fi ;;
  status)
    if test -f "$TICO_TEST_SUBSCRIPTION_CONTROLS/status-error"; then echo 'Internal service failure' >&2; exit 2; fi
    if test -f "$auth" && grep -q '"key"' "$auth"; then echo 'Logged in'; else echo 'Not logged in'; exit 1; fi ;;
  --version) echo 1.0.0 ;;
  *) exit 2 ;;
esac
"#,
        );
        let mut cmd = Command::new(std::env::current_exe().unwrap());
        cmd.args(["--exact", test, "--nocapture"])
            .env(CHILD, &root)
            .env(
                "PATH",
                format!("{}:/usr/bin:/bin", root.join("bin").display()),
            )
            .env("HOME", &root)
            .env_remove(crate::isolation::UID_ENV)
            .env_remove(crate::isolation::GID_ENV);
        for key in [
            "GROK_HOME",
            "GROK_AUTH_PATH",
            "XDG_CONFIG_HOME",
            "CURSOR_CONFIG_DIR",
            "CURSOR_DATA_DIR",
            "XAI_API_KEY",
            "CURSOR_API_KEY",
        ] {
            cmd.env_remove(key);
        }
        let out = cmd.output().unwrap();
        std::fs::remove_dir_all(&root).ok();
        assert!(
            out.status.success(),
            "{}{}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        );
        return;
    };
    std::fs::create_dir_all(root.join("projects")).unwrap();
    let path = root.join("runner.json");
    std::fs::write(&path, json!({"url": "http://127.0.0.1:9", "token": "synthetic", "runner_id": "r1", "projects_dir": root.join("projects")}).to_string()).unwrap();
    let config = Config::load(&path).unwrap();
    let client = crate::client::Client::new(&config.url(), &config.token(), 1, 0).unwrap();
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    let old = r#"{"https://accounts.x.ai/sign-in":{"auth_mode":"oidc","key":"old"}}"#;
    let legacy_auth = root.join("legacy-home").join(runtime).join(file);
    std::fs::create_dir_all(legacy_auth.parent().unwrap()).unwrap();
    std::fs::write(&legacy_auth, old).unwrap();
    for use_default in [false, true] {
        let mut config = Config::load(&path).unwrap();
        if use_default {
            config.data.insert("profiles".into(), json!({"computer": {"dir": root.join("default-home")}, "other": {"dir": root.join("per-profile-home")}, "legacy": {"dir": root.join("legacy-home")}}));
            config
                .data
                .insert("default_profile".into(), json!("computer"));
            config.save().unwrap();
            // Inherited operator paths and keys cannot answer for the computer's isolated default login.
            for key in [
                "GROK_HOME",
                "GROK_AUTH_PATH",
                "XDG_CONFIG_HOME",
                "CURSOR_CONFIG_DIR",
                "CURSOR_DATA_DIR",
                "XAI_API_KEY",
                "CURSOR_API_KEY",
            ] {
                std::env::set_var(key, root.join("outside"));
            }
        }
        let runner = Arc::new(Runner::new(
            config.clone(),
            client.clone(),
            crate::state::State::open(&root.join("state")).unwrap(),
        ));
        let home = if use_default {
            root.join("default-home").join(runtime)
        } else {
            root.clone()
        };
        let auth = home.join(file);
        let operator_before = std::fs::read(root.join(file)).ok();
        let make = |kind: &str| {
            Session::new(
                &json!({"id": uuid::Uuid::new_v4().simple().to_string(), "runtime": runtime, "kind": kind}),
            )
        };
        let (exe, env, p) = login_env(runtime, &config, "").unwrap();
        assert_eq!(
            sign_in_status(runtime, &exe, &env),
            Some((false, String::new()))
        );
        if use_default {
            let ready = rt.block_on(crate::readiness::runtime_readiness(
                runtime,
                &runner.sign_in,
                &runner.client,
                &[],
                p.as_ref(),
            ));
            assert_eq!(
                ready["authenticated"], "missing",
                "an inherited operator key is not the default home's login"
            );
        }
        std::fs::remove_file(root.join("continue")).ok();
        let login = make("sign_in");
        let (s2, r2, c2) = (login.clone(), runner.clone(), config.clone());
        let running = std::thread::spawn(move || run_session(s2, r2, c2, Duration::from_secs(5)));
        wait_for(|| {
            let i = login.inner.lock().unwrap();
            i.state == "waiting" && !i.url.is_empty() && !i.code.is_empty()
        });
        let report = login.report().unwrap();
        assert_eq!(report["url"], "https://login.example/device");
        assert_eq!(report["code"], "ABCD-1234");
        std::fs::write(root.join("continue"), "").unwrap();
        running.join().unwrap();
        assert_eq!(state(&login).0, "signed_in", "{:?}", state(&login));
        assert_eq!(sign_in_status(runtime, &exe, &env).unwrap().0, true);
        let ready = rt.block_on(crate::readiness::runtime_readiness(
            runtime,
            &runner.sign_in,
            &runner.client,
            &[],
            p.as_ref(),
        ));
        assert_eq!(ready["authenticated"], "ready");
        if use_default {
            assert_eq!(p.as_ref().unwrap().name, "computer");
            assert_eq!(std::fs::read(root.join(file)).ok(), operator_before);
            assert!(!root.join("outside").exists());
        }
        std::fs::write(&auth, old).unwrap();
        std::fs::write(root.join("logout-error"), "").unwrap();
        std::fs::write(root.join("status-error"), "").unwrap();
        let failed = make("sign_out");
        run_sign_out(failed.clone(), runner.clone(), config.clone());
        assert_eq!(state(&failed).0, "failed");
        assert_eq!(runner.sign_in.signed_out(runtime), crate::harness_tools::caps(runtime).sign_in_status.check == "command", "an unknown verdict must keep automatic key login blocked; a confirmed existing login clears the mark");
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), old);
        std::fs::remove_file(root.join("logout-error")).unwrap();
        std::fs::remove_file(root.join("status-error")).unwrap();
        if crate::harness_tools::caps(runtime).sign_in_status.check == "file" {
            std::fs::write(root.join("logout-corrupt"), "").unwrap();
            let corrupt = make("sign_out");
            run_sign_out(corrupt.clone(), runner.clone(), config.clone());
            assert_eq!(state(&corrupt).0, "failed");
            assert!(state(&corrupt).1.contains("could not confirm"));
            assert!(runner.sign_in.signed_out(runtime));
            assert_eq!(sign_in_status(runtime, &exe, &env), None);
            std::fs::remove_file(root.join("logout-corrupt")).unwrap();
            std::fs::write(
                &auth,
                r#"{"xai::api_key":{"auth_mode":"api_key","key":"stored-synthetic-key"}}"#,
            )
            .unwrap();
            assert!(sign_in_status(runtime, &exe, &env).unwrap().0);
            let ready = rt.block_on(crate::readiness::runtime_readiness(
                runtime,
                &runner.sign_in,
                &runner.client,
                &[],
                p.as_ref(),
            ));
            assert_eq!(ready["authenticated"], "ready");
            assert_eq!(ready["detail"], "Signed in with an API key");
            std::fs::write(&auth, old).unwrap();
        }
        if !use_default {
            let mut custom = env.clone();
            for key in crate::harness_tools::caps(runtime).profile_home_overrides {
                custom.insert(key, root.join("outside").display().to_string());
            }
            assert!(begin_switch(runtime, "custom", &custom, None)
                .unwrap_err()
                .contains("custom login folder"));
            assert_eq!(std::fs::read_to_string(&auth).unwrap(), old);
            assert!(!root.join("outside").exists());
        }
        let canceled = make("switch");
        answer_finishing(&canceled, false);
        run_session(
            canceled.clone(),
            runner.clone(),
            config.clone(),
            Duration::from_secs(5),
        );
        assert_eq!(state(&canceled).0, "cancelled", "{:?}", state(&canceled));
        assert_eq!(std::fs::read_to_string(&auth).unwrap(), old);
        let switch = make("switch");
        answer_finishing(&switch, true);
        run_session(
            switch.clone(),
            runner.clone(),
            config.clone(),
            Duration::from_secs(5),
        );
        assert_eq!(state(&switch).0, "signed_in", "{:?}", state(&switch));
        assert!(std::fs::read_to_string(&auth).unwrap().contains("\"new\""));
        assert!(!std::fs::read_dir(&home)
            .unwrap()
            .flatten()
            .any(|e| e.file_name().to_string_lossy().starts_with(".tico-switch-")));
        let logout = make("sign_out");
        run_sign_out(logout.clone(), runner.clone(), config.clone());
        assert_eq!(state(&logout).0, "signed_out", "{:?}", state(&logout));
        assert_eq!(
            sign_in_status(runtime, &exe, &env),
            Some((false, String::new()))
        );
        let ready = rt.block_on(crate::readiness::runtime_readiness(
            runtime,
            &runner.sign_in,
            &runner.client,
            &[],
            p.as_ref(),
        ));
        assert_eq!(ready["authenticated"], "missing");
        if use_default {
            assert_eq!(std::fs::read(root.join(file)).ok(), operator_before);
        }
        let mut sessions = Logins::default();
        sessions.begin(
            &runner,
            &json!({"id": "named", "runtime": runtime, "profile": "other", "kind": "sign_in"}),
        );
        assert!(state(&sessions.sessions["named"])
            .1
            .starts_with("Each computer has one login"));
        assert_eq!(std::fs::read_to_string(&legacy_auth).unwrap(), old);
        assert!(!root.join("per-profile-home").exists());
        assert!(!root.join("profiles").exists());
    }
}

#[test]
fn a_muse_sign_in_is_its_login_file_not_a_status_command() {
    let dir = std::env::temp_dir().join(format!("muse-signin-{}", uuid::Uuid::new_v4().simple()));
    let env: Env = [("XDG_CONFIG_HOME".to_string(), dir.display().to_string())]
        .into_iter()
        .collect();
    // `muse` has no `auth status`; the executable is never run for it.
    assert!(!signed_in("musecode", "/nonexistent/muse", &env, false).0);
    std::fs::create_dir_all(dir.join("muse")).unwrap();
    std::fs::write(
        dir.join("muse").join("auth.json"),
        r#"{"schema_version":1,"providers":{"meta":{"api_key":"fixture"}}}"#,
    )
    .unwrap();
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

#[test]
fn an_echoed_typed_code_is_never_reported_as_the_device_code() {
    // A CLI that draws the pasted code on its prompt line: the typed value holds a device-code-shaped piece.
    let typed = "WXYZ-9876abcdefgh#state";
    let raw = format!("Paste code here if prompted > {typed}\r\nSigning in...\r\n");
    let (_, code, lines, _) = parse(&raw, typed);
    assert_eq!(code, "", "{lines:?}");
    assert!(!lines.iter().any(|l| l.contains("WXYZ")), "{lines:?}");
    // The device code the CLI itself shows is still read.
    let raw = format!("Enter this code: ABCD-1234\r\nPaste code here if prompted > {typed}\r\n");
    assert_eq!(parse(&raw, typed).1, "ABCD-1234");
}

/// A finished login asks for a probe, whatever its verdict, once its action has run (finding 1): a sign-out's logout
/// and a named profile's removal are read again after they finish, not before.
#[tokio::test]
async fn a_finished_login_asks_for_a_probe_whatever_the_verdict() {
    let root = std::env::temp_dir().join(format!("tico-finish-{}", uuid::Uuid::new_v4().simple()));
    std::fs::create_dir_all(root.join("projects")).unwrap();
    let data = json!({"projects_dir": root.join("projects").display().to_string()})
        .as_object()
        .unwrap()
        .clone();
    let config = Config {
        path: std::path::PathBuf::new(),
        data,
    };
    let client = crate::client::Client::new("http://127.0.0.1:9", "t", 1, 0).unwrap();
    let runner = Runner::new(
        config,
        client,
        crate::state::State::open(&root.join("state")).unwrap(),
    );
    for (state, message) in [
        ("signed_out", "Signed out"),
        ("failed", "The logout did not finish"),
    ] {
        let before = crate::readiness::requests();
        session("sign_out").finish(state, message, &runner, "");
        assert!(
            crate::readiness::requests() > before,
            "{state} asks for a probe"
        );
    }
    std::fs::remove_dir_all(root).unwrap();
}
