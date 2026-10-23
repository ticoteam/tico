//! Replay tests: real provider sessions recorded at the process boundary (runner/tests/fixtures/REPLAY.md), played
//! back through a fake CLI that writes what the provider wrote and keeps what the runner wrote back. A replay passes
//! when the host makes the recorded events of it and sends the recorded frames: prompts, steers, interrupts and
//! answers to the provider's requests.

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::sync::mpsc;

use super::claude::ClaudeHost;
use super::codex::CodexHost;
use super::Settings;
use crate::env::Env;

pub struct Scenario {
    pub dir: PathBuf,
    pub input: Value,
    pub transcript: Vec<Value>,
    pub output: Value,
}

/// Every recorded scenario of `harness`: runner/tests/fixtures/<harness>/replay/<name>/.
pub fn scenarios(harness: &str) -> Vec<Scenario> {
    let root = super::fixtures::dir(harness).join("replay");
    let mut dirs: Vec<PathBuf> = std::fs::read_dir(&root).unwrap().map(|e| e.unwrap().path()).filter(|p| p.is_dir()).collect();
    dirs.sort();
    dirs.into_iter().map(load).collect()
}

fn load(dir: PathBuf) -> Scenario {
    let read = |name: &str| std::fs::read_to_string(dir.join(name)).unwrap_or_else(|e| panic!("{}: {e}", dir.join(name).display()));
    let transcript = read("transcript.ndjson").lines().filter(|l| !l.trim().is_empty()).map(|l| serde_json::from_str(l).unwrap()).collect();
    Scenario { input: serde_json::from_str(&read("input.json")).unwrap(), transcript, output: serde_json::from_str(&read("output.json")).unwrap(), dir }
}

fn quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

/// The fake CLI for `transcript` at `<dir>/bin/<name>`. Process n (the n-th `spawn`) runs `<dir>/proc-<n>.sh`: it
/// writes each run of `in` lines, reads one line from stdin for each `out` frame and keeps it in `<dir>/sent`, waits
/// for stdin to close at `close` and for SIGTERM at `signal`, and exits as recorded. A uuid the runner chose (a
/// Claude user message's) is read from the line it wrote and put in place of the recorded one from then on.
pub fn write_fake(dir: &Path, name: &str, version: &str, transcript: &[Value]) -> PathBuf {
    let bin = dir.join("bin");
    std::fs::create_dir_all(&bin).unwrap();
    let d = quote(&dir.display().to_string());
    let mut procs: Vec<Vec<&Value>> = vec![];
    for entry in transcript {
        if entry["dir"] == "spawn" {
            procs.push(vec![]);
        } else {
            procs.last_mut().expect("a transcript starts with a spawn").push(entry);
        }
    }
    for (i, entries) in procs.iter().enumerate() {
        let n = i + 1;
        let mut sh = format!("D={d}\nS='-e s/^//'\n");
        if entries.iter().any(|e| e["dir"] == "signal") {
            sh.push_str("trap 'got=1' TERM\n");
        }
        let (mut run, mut k) = (String::new(), 0);
        let flush = |run: &mut String, k: &mut usize, sh: &mut String| {
            if !run.is_empty() {
                *k += 1;
                let file = format!("p{n}-{k}.in");
                std::fs::write(dir.join(&file), std::mem::take(run)).unwrap();
                sh.push_str(&format!("sed $S \"$D/{file}\"\n"));
            }
        };
        for (j, e) in entries.iter().enumerate() {
            match e["dir"].as_str().unwrap_or_default() {
                "in" => {
                    match e.get("frame") {
                        Some(f) => run.push_str(&serde_json::to_string(f).unwrap()),
                        None => run.push_str(e["line"].as_str().unwrap_or_default()),
                    }
                    run.push('\n');
                }
                "out" => {
                    flush(&mut run, &mut k, &mut sh);
                    sh.push_str("if IFS= read -r line; then printf '%s\\n' \"$line\" >> \"$D/sent\"; else echo '\"<stdin closed early>\"' >> \"$D/sent\"; exit 98; fi\n");
                    if let Some(old) = e["frame"]["uuid"].as_str() {
                        sh.push_str(&format!(
                            "u{j}=$(printf '%s\\n' \"$line\" | sed -n 's/.*\"uuid\": *\"\\([^\"]*\\)\".*/\\1/p'); S=\"$S -e s/{old}/$u{j}/g\"\n"
                        ));
                    }
                }
                "close" => {
                    flush(&mut run, &mut k, &mut sh);
                    sh.push_str("cat >> \"$D/sent\"\n");
                }
                "signal" => {
                    flush(&mut run, &mut k, &mut sh);
                    sh.push_str("while [ -z \"$got\" ]; do sleep 0.05; done\n");
                }
                "exit" => {
                    flush(&mut run, &mut k, &mut sh);
                    std::fs::write(dir.join(format!("p{n}.err")), e["stderr"].as_str().unwrap_or_default()).unwrap();
                    sh.push_str(&format!("cat \"$D/p{n}.err\" >&2\n"));
                    let code = e["code"].as_i64().unwrap_or(0);
                    sh.push_str(&if code < 0 { format!("trap - TERM\nkill -{} $$\n", -code) } else { format!("exit {code}\n") });
                }
                _ => {}
            }
        }
        flush(&mut run, &mut k, &mut sh);
        std::fs::write(dir.join(format!("proc-{n}.sh")), sh).unwrap();
    }
    let path = bin.join(name);
    crate::util::write_script(
        &path,
        &format!(
            "#!/bin/sh\nD={d}\ncase \"$1\" in --version) echo {}; exit 0;; --help) echo '  --autocompact <tokens>'; exit 0;; esac\n\
             n=$(( $(cat \"$D/spawns\" 2>/dev/null || echo 0) + 1 )); echo $n > \"$D/spawns\"\n\
             [ -f \"$D/proc-$n.sh\" ] || {{ echo \"replay: no process $n was recorded\" >&2; exit 97; }}\nexec /bin/sh \"$D/proc-$n.sh\"\n",
            quote(version)
        ),
    );
    path
}

/// Whether `got` is `want`, where the string "<any>" in `want` stands for any value.
pub fn matches(want: &Value, got: &Value) -> bool {
    match (want, got) {
        (Value::String(w), _) if w == "<any>" => true,
        (Value::Object(w), Value::Object(g)) => w.len() == g.len() && w.iter().all(|(k, v)| g.get(k).is_some_and(|x| matches(v, x))),
        (Value::Array(w), Value::Array(g)) => w.len() == g.len() && w.iter().zip(g).all(|(a, b)| matches(a, b)),
        _ => want == got,
    }
}

/// The frames the runner chose and a replay cannot repeat, by harness: its own message ids, the turn's folder and
/// the per-thread config (checked by the hosts' own tests).
pub fn unrepeatable(harness: &str, frame: &Value) -> Value {
    let mut f = frame.clone();
    match harness {
        "claude" if f.get("uuid").is_some() => f["uuid"] = json!("<any>"),
        "codex" if matches!(f["method"].as_str(), Some("thread/start" | "thread/resume")) => {
            f["params"]["cwd"] = json!("<any>");
            f["params"]["config"] = json!("<any>");
        }
        _ => {}
    }
    f
}

/// The host under a replay or a recording.
pub enum Under {
    Claude(ClaudeHost),
    Codex(CodexHost),
}

impl Under {
    async fn start_thread(&self, settings: &Settings) -> String {
        match self {
            Under::Claude(h) => h.start_thread(settings),
            Under::Codex(h) => h.start_thread("acme", settings).await.unwrap(),
        }
    }

    async fn start_turn(&self, thread: &str, prompt: &str) -> Result<String, String> {
        match self {
            Under::Claude(h) => h.start_turn(thread, prompt, None).map_err(|e| format!("{e:?}")),
            Under::Codex(h) => h.start_command(thread, prompt, None).await.map_err(|e| format!("{e:?}")),
        }
    }

    async fn act(&self, thread: &str, turn: &str, action: &Value) -> Value {
        let turn = match self {
            Under::Codex(h) => h.active_turn(thread).unwrap_or_else(|| turn.to_string()),
            Under::Claude(_) => turn.to_string(),
        };
        if let Some(text) = action["steer"].as_str() {
            let ok = match self {
                Under::Claude(h) => h.steer(thread, &turn, text).is_ok(),
                Under::Codex(h) => h.steer(thread, &turn, text).await.is_ok(),
            };
            return json!({"act": "steer", "ok": ok});
        }
        let ok = match self {
            Under::Claude(h) => {
                h.interrupt(thread, &turn);
                true
            }
            Under::Codex(h) => h.interrupt(thread, &turn).await.is_ok(),
        };
        json!({"act": "interrupt", "ok": ok})
    }

    async fn stop(&self) {
        match self {
            Under::Claude(h) => h.stop().await,
            Under::Codex(h) => h.stop().await,
        }
    }
}

/// What a turn waits for before its next action or its end.
pub enum Pace<'a> {
    /// A replay: the recorded count of events.
    Replay(&'a Value),
    /// A recording: each turn's actions, each fired `delay` after a line from the CLI holds all of `when`; a turn
    /// ends at its last event, once the log is quiet and holds `until`.
    Record { log: &'a Path, actions: &'a [Vec<Trigger>], until: &'a [&'a str] },
}

const RECORD_TURN_LIMIT: Duration = Duration::from_secs(240);

/// (when, delay, action)
pub type Trigger = (Vec<&'static str>, Duration, Value);

fn log_has(log: &Path, needles: &[&str]) -> bool {
    std::fs::read_to_string(log).unwrap_or_default().lines().any(|l| l.contains("\"dir\": \"in\"") && needles.iter().all(|n| l.contains(n)))
}

fn turn_over(harness: &str, ev: &Value) -> bool {
    match harness {
        "claude" => ev["kind"] == "status" && ev["state"] == "idle",
        _ => ev["kind"] == "turn_completed" || ev["kind"] == "turn_failed",
    }
}

fn normalize(harness: &str, transcript: &str, mut ev: Value) -> Value {
    let Some(o) = ev.as_object_mut() else { return ev };
    for key in ["thread_id", "turn_id"] {
        let ours = o.get(key).and_then(|v| v.as_str()).is_some_and(|v| !transcript.contains(&format!("\"{v}\"")));
        if harness == "claude" {
            o.remove(key); // the runner's own uuids
        } else if ours {
            o.insert(key.into(), json!("<runner>")); // an id the host made up, as for a control call's turn
        }
    }
    ev
}

/// Runs the scenario's turns on `host` and returns its output: each turn's events (with each action's result in
/// place) and what came after the host stopped, plus each action's `after_events` as fired.
pub async fn drive(harness: &str, host: &Under, mut rx: mpsc::UnboundedReceiver<Value>, settings: &Settings, input: &Value, pace: Pace<'_>, transcript: impl Fn() -> String) -> (Value, Vec<Vec<usize>>) {
    let thread = host.start_thread(settings).await;
    let (mut turns, mut fired) = (vec![], vec![]);
    for (t, turn) in input["turns"].as_array().unwrap().iter().enumerate() {
        let id = host.start_turn(&thread, turn["prompt"].as_str().unwrap()).await;
        let (mut got, mut at) = (vec![], vec![]);
        if let Err(e) = &id {
            got.push(json!({"start_failed": e}));
        }
        let id = id.unwrap_or_default();
        let actions = turn["actions"].as_array().cloned().unwrap_or_default();
        let mut next = 0;
        let mut quiet_since: Option<(tokio::time::Instant, u64)> = None;
        let started = tokio::time::Instant::now();
        loop {
            let due = match &pace {
                Pace::Replay(_) => next < actions.len() && got.len() >= actions[next]["after_events"].as_u64().unwrap() as usize,
                Pace::Record { log, actions: a, .. } => next < a[t].len() && log_has(log, &a[t][next].0),
            };
            if due {
                if let Pace::Record { actions: a, .. } = &pace {
                    tokio::time::sleep(a[t][next].1).await;
                }
                at.push(got.len());
                let action = match &pace {
                    Pace::Replay(_) => actions[next].clone(),
                    Pace::Record { actions: a, .. } => a[t][next].2.clone(),
                };
                got.push(host.act(&thread, &id, &action).await);
                next += 1;
                continue;
            }
            match &pace {
                Pace::Replay(out) => {
                    let want = out["turns"][t].as_array().map_or(0, |w| w.len());
                    if got.len() >= want {
                        break;
                    }
                    match tokio::time::timeout(Duration::from_secs(10), rx.recv()).await {
                        Ok(Some(ev)) if ev.get("stderr_tail").is_some() || ev["process_exit"] == true => continue,
                        Ok(Some(ev)) => got.push(normalize(harness, &transcript(), ev)),
                        _ => break,
                    }
                }
                Pace::Record { log, until, .. } => match tokio::time::timeout(Duration::from_millis(250), rx.recv()).await {
                    Ok(Some(ev)) => {
                        got.push(ev);
                        quiet_since = None;
                    }
                    Ok(None) => break,
                    Err(_) => {
                        let ended = got.iter().any(|e| turn_over(harness, e)) && (until.is_empty() || log_has(log, until));
                        assert!(started.elapsed() < RECORD_TURN_LIMIT, "turn {t} did not end within {RECORD_TURN_LIMIT:?}");
                        let size = std::fs::metadata(log).map_or(0, |m| m.len());
                        match quiet_since {
                            Some((since, s)) if s == size => {
                                if ended && since.elapsed() >= Duration::from_secs(3) {
                                    break;
                                }
                            }
                            _ => quiet_since = Some((tokio::time::Instant::now(), size)),
                        }
                    }
                },
            }
        }
        turns.push(Value::Array(got));
        fired.push(at);
    }
    host.stop().await;
    let mut after = vec![];
    while let Ok(Some(ev)) = tokio::time::timeout(Duration::from_millis(500), rx.recv()).await {
        if ev.get("stderr_tail").is_none() && ev["process_exit"] != true { after.push(ev); }
    }
    let text = transcript();
    let norm = |v: Vec<Value>| Value::Array(v.into_iter().map(|e| normalize(harness, &text, e)).collect());
    let turns = turns.into_iter().map(|t| norm(t.as_array().unwrap().clone())).collect::<Vec<_>>();
    (json!({"turns": turns, "after": norm(after)}), fired)
}

/// A host of `harness` whose CLI is the fake in `bin` (or, recording, the proxy in front of the real one).
pub fn host(harness: &str, cmd: &str, path: &str, home: &Path) -> (Under, mpsc::UnboundedReceiver<Value>) {
    let (tx, rx) = mpsc::unbounded_channel();
    let host = match harness {
        "claude" => Under::Claude(ClaudeHost::new("acme", vec![cmd.into()], tx)),
        _ => {
            let env: Env = [("PATH", path), ("HOME", &home.display().to_string()), ("CODEX_HOME", &home.join(".codex").display().to_string())]
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect();
            Under::Codex(CodexHost::on_home(env, tx, Some(&home.join(".codex").display().to_string())))
        }
    };
    (host, rx)
}

pub async fn start(host: &Under) {
    match host {
        Under::Claude(h) => h.start().await.unwrap(),
        Under::Codex(h) => h.start().await.unwrap(),
    }
}

pub fn settings(cwd: &Path, path: &str, home: &Path) -> Settings {
    let mut s = Settings { cwd: cwd.display().to_string(), ..Default::default() };
    s.env.insert("PATH".into(), path.into());
    s.env.insert("HOME".into(), home.display().to_string());
    s
}

/// Plays one scenario back; panics with the scenario's name on any difference.
pub async fn replay(harness: &str, s: &Scenario) {
    let dir = std::env::temp_dir().join(format!("replay-{harness}-{}", uuid::Uuid::new_v4().simple()));
    let (home, cwd) = (dir.join("home"), dir.join("work"));
    std::fs::create_dir_all(home.join(".codex")).unwrap();
    std::fs::create_dir_all(&cwd).unwrap();
    let cli = write_fake(&dir, harness, s.input["cli"].as_str().unwrap_or("0"), &s.transcript);
    let path = format!("{}:/usr/bin:/bin", dir.join("bin").display());
    let (host, rx) = host(harness, &cli.display().to_string(), &path, &home);
    start(&host).await;
    let text = std::fs::read_to_string(s.dir.join("transcript.ndjson")).unwrap();
    let (got, _) = drive(harness, &host, rx, &settings(&cwd, &path, &home), &s.input, Pace::Replay(&s.output), || text.clone()).await;
    let name = s.dir.display();
    assert_eq!(got, s.output, "{name}: the host's events differ from the recording");
    if let Under::Claude(h) = &host {
        // Claude echoes each message it read with the uuid written, so a broken uuid swap shows here; it also echoes
        // a local command's output (`/compact`) under a uuid of its own, which the host matches by order.
        let written: Vec<&Value> = s.transcript.iter().filter(|e| e["dir"] == "out").map(|e| &e["frame"]["uuid"]).collect();
        let own = s.transcript.iter().filter(|e| e["dir"] == "in").map(|e| &e["frame"]).any(|f| {
            f["type"] == "user" && crate::util::truthy(f.get("isReplay")) && !crate::util::truthy(f.get("parent_tool_use_id")) && !written.contains(&&f["uuid"])
        });
        assert_eq!(h.replay_fallback_used(), own, "{name}: the host matched Claude's echoes by order where the recording matched by uuid, or the reverse");
    }
    let sent: Vec<Value> = std::fs::read_to_string(dir.join("sent"))
        .unwrap_or_default()
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).unwrap_or_else(|_| json!(l)))
        .collect();
    let want: Vec<Value> = s.transcript.iter().filter(|e| e["dir"] == "out").map(|e| unrepeatable(harness, &e["frame"])).collect();
    assert_eq!(sent.len(), want.len(), "{name}: the runner sent {} frames, the recording has {}:\n{sent:#?}", sent.len(), want.len());
    for (i, (w, g)) in want.iter().zip(&sent).enumerate() {
        assert!(matches(w, g), "{name}: frame {i} the runner sent differs from the recording\nwant {w}\ngot  {g}");
    }
    std::fs::remove_dir_all(dir).unwrap();
}

#[tokio::test]
async fn every_claude_replay_matches_its_recording() {
    for s in scenarios("claude") {
        replay("claude", &s).await;
    }
}

#[tokio::test]
async fn every_codex_replay_matches_its_recording() {
    for s in scenarios("codex") {
        replay("codex", &s).await;
    }
}

#[test]
fn the_replays_are_real_captures() {
    let all: Vec<Scenario> = ["claude", "codex"].iter().flat_map(|h| scenarios(h)).collect();
    let real = all.iter().filter(|s| s.input["source"]["real"] == true).count();
    assert!(real >= 8, "{real} real-capture replays; the suite keeps at least 8");
    for s in &all {
        assert!(s.transcript.first().is_some_and(|e| e["dir"] == "spawn"), "{}", s.dir.display());
        assert!(s.transcript.iter().any(|e| e["dir"] == "out"), "{}: no frame from the runner", s.dir.display());
    }
}

#[test]
fn a_frame_matches_with_wildcards_only() {
    assert!(matches(&json!({"a": "<any>", "b": [1, {"c": 2}]}), &json!({"a": {"x": 1}, "b": [1, {"c": 2}]})));
    assert!(!matches(&json!({"a": 1}), &json!({"a": 1, "b": 2})));
    assert!(!matches(&json!([1, 2]), &json!([1])));
    assert!(!matches(&json!({"a": "x"}), &json!({"a": "y"})));
}

/// Recording (not part of `cargo test`): `TICO_REPLAY_RECORD=<name,...|all> cargo test record_replays -- --ignored
/// --nocapture` runs each scenario below on the installed CLI, signed in as this computer's operator, through
/// runner/tests/fixtures/replay_proxy.py, and writes runner/tests/fixtures/<harness>/replay/<name>/. Read the files
/// before committing: the recorder masks the login's tokens, paths and email addresses, nothing else.
mod record {
    use super::*;

    struct Plan {
        harness: &'static str,
        name: &'static str,
        about: &'static str,
        turns: Vec<(&'static str, Vec<Trigger>)>,
        until: Vec<&'static str>,
        /// Sign in with a login that no longer works (an expired Claude token, a revoked API key).
        bad_login: bool,
    }

    fn plans() -> Vec<Plan> {
        let s = Duration::from_secs;
        let tool = |cmd: &'static str| vec!["\"tool_use\"", cmd];
        let codex_tool = |cmd: &'static str| vec!["item/started", "commandExecution", cmd];
        vec![
            Plan { harness: "claude", name: "turn", about: "a plain turn", turns: vec![("Reply with exactly: hello from the replay", vec![])], until: vec![], bad_login: false },
            Plan {
                harness: "claude",
                name: "subagent",
                about: "a subagent's stream next to the bot's own",
                turns: vec![("Use the Task tool to start one general-purpose subagent with the prompt 'Reply with exactly: pong'. Then tell me in one line what it replied.", vec![])],
                until: vec![],
                bad_login: false,
            },
            Plan {
                harness: "claude",
                name: "steer-mid-turn",
                about: "a steer written while a tool runs, read after it",
                turns: vec![(
                    "Run the shell command `sleep 6` with the Bash tool, then reply with one short line.",
                    vec![(tool("sleep 6"), s(1), json!({"steer": "Also end your reply with the word banana."}))],
                )],
                until: vec![],
                bad_login: false,
            },
            // A follow-up after the turn's answer while its background work still runs: read and answered then, not
            // once that work ends.
            Plan {
                harness: "claude",
                name: "steer-during-background",
                about: "a steer after the result while a background command runs, answered before it ends",
                turns: vec![(
                    "Start the shell command `timeout 30 tail -f /dev/null` with the Bash tool and run_in_background set to true. Do not wait for it: reply with exactly 'started' and end your turn.",
                    vec![(vec!["\"type\": \"result\""], s(1), json!({"steer": "While that runs: what is 2+2? Answer in one word."}))],
                )],
                until: vec![],
                bad_login: false,
            },
            Plan {
                harness: "claude",
                name: "interrupt-mid-tool",
                about: "an interrupt while a shell command runs",
                turns: vec![("Run the shell command `timeout 45 tail -f /dev/null` with the Bash tool, then reply done.", vec![(tool("timeout 45 tail -f /dev/null"), s(2), json!({"interrupt": true}))])],
                until: vec![],
                bad_login: false,
            },
            Plan {
                harness: "claude",
                name: "compaction",
                about: "a turn, then /compact on the same session",
                turns: vec![("Reply with exactly: first turn", vec![]), ("/compact", vec![])],
                until: vec![],
                bad_login: false,
            },
            // A fallback turn's fresh thread, given only the replayed conversation (crate::fallback::replay): the answer
            // is in the history and nowhere else.
            Plan {
                harness: "claude",
                name: "fallback-replay",
                about: "a fallback turn that answers from the replayed conversation",
                turns: vec![(fallback_prompt(), vec![])],
                until: vec![],
                bad_login: false,
            },
            Plan { harness: "claude", name: "auth-expired", about: "an expired login whose refresh fails", turns: vec![("Reply with exactly: hello", vec![])], until: vec![], bad_login: true },
            // Record on a login that is out of quota: the provider's own refusal, as the turn sees it.
            Plan {
                harness: "codex",
                name: "limit-notice",
                about: "a turn on a login out of quota",
                turns: vec![("Reply with exactly: hello from the replay", vec![])],
                until: vec![],
                bad_login: false,
            },
            Plan {
                harness: "codex",
                name: "steer-mid-turn",
                about: "a steer while a command runs",
                turns: vec![(
                    "Run the shell command `sleep 6`, then reply with one short line.",
                    vec![(codex_tool("sleep 6"), s(1), json!({"steer": "Also end your reply with the word banana."}))],
                )],
                until: vec![],
                bad_login: false,
            },
            Plan {
                harness: "codex",
                name: "interrupt-mid-tool",
                about: "an interrupt while a command runs",
                turns: vec![("Run the shell command `sleep 45`, then reply done.", vec![(codex_tool("sleep 45"), s(2), json!({"interrupt": true}))])],
                until: vec![],
                bad_login: false,
            },
            // No Codex compaction plan: the host emits a control call's turn_completed from the caller's task, after
            // notifications read in the same burst, so a replay of it would not keep one order.
            Plan { harness: "codex", name: "auth-rejected", about: "an API key the provider rejects", turns: vec![("Reply with exactly: hello", vec![])], until: vec![], bad_login: true },
        ]
    }

    fn fallback_prompt() -> &'static str {
        let attempt = json!({"bot": "ops", "conversation": {"scope": "shared"}, "message": {"id": "m3"}, "history": [
            {"id": "m1", "from_actor": "human:ana", "body": "Note this for later: the Acme code word is PELICAN.", "refs": {}, "created": "2026-10-16T05:00:00Z"},
            {"id": "m2", "from_actor": "bot:ops", "body": "Noted.", "refs": {}, "created": "2026-10-16T05:00:05Z"},
        ]});
        let text = format!(
            "{}\n\nCurrent message from human:ana:\nWhat is the Acme code word? Reply with only the word.",
            crate::fallback::replay(&attempt, crate::fallback::REPLAY_TOKENS)
        );
        Box::leak(text.into_boxed_str())
    }

    /// Removes a recording's folder, which holds a copy of the login, even when the recording panics.
    struct Gone(PathBuf);

    impl Drop for Gone {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    /// A copy of the login is readable by its owner only, also when a killed recording leaves it behind.
    fn write_private(path: &Path, text: &str) {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(path).unwrap();
        f.write_all(text.as_bytes()).unwrap();
    }

    fn which(name: &str) -> String {
        let out = std::process::Command::new("sh").arg("-c").arg(format!("command -v {name}")).output().unwrap();
        String::from_utf8_lossy(&out.stdout).trim().to_string()
    }

    /// The login's secret values, and a home holding a copy of it (or a broken one).
    fn login(harness: &str, home: &Path, bad: bool) -> Vec<String> {
        let real = PathBuf::from(std::env::var("HOME").unwrap());
        match harness {
            "claude" => {
                std::fs::create_dir_all(home.join(".claude")).unwrap();
                let creds: Value = serde_json::from_str(&std::fs::read_to_string(real.join(".claude/.credentials.json")).unwrap()).unwrap();
                let o = &creds["claudeAiOauth"];
                let secrets = vec![o["accessToken"].as_str().unwrap().to_string(), o["refreshToken"].as_str().unwrap().to_string()];
                let mut copy = creds.clone();
                if bad {
                    copy["claudeAiOauth"]["accessToken"] = json!(format!("sk-ant-oat01-expired{}", "x".repeat(60)));
                    copy["claudeAiOauth"]["refreshToken"] = json!(format!("sk-ant-ort01-revoked{}", "x".repeat(60)));
                    copy["claudeAiOauth"]["expiresAt"] = json!(1_700_000_000_000_i64);
                }
                write_private(&home.join(".claude/.credentials.json"), &copy.to_string());
                secrets
            }
            _ => {
                std::fs::create_dir_all(home.join(".codex")).unwrap();
                let auth: Value = serde_json::from_str(&std::fs::read_to_string(real.join(".codex/auth.json")).unwrap()).unwrap();
                let key = auth["OPENAI_API_KEY"].as_str().unwrap_or_default().to_string();
                let mut copy = auth.clone();
                if bad {
                    copy["OPENAI_API_KEY"] = json!(format!("sk-proj-revoked{}", "x".repeat(40)));
                }
                write_private(&home.join(".codex/auth.json"), &copy.to_string());
                vec![key].into_iter().filter(|k| !k.is_empty()).collect()
            }
        }
    }

    fn scrub(v: &Value, secrets: &crate::redact::Redactor, paths: &[(String, &str)]) -> Value {
        match v {
            Value::String(s) => {
                let mut s = secrets.scrub_text(s);
                for (from, to) in paths {
                    s = s.replace(from.as_str(), to);
                }
                let email = regex::Regex::new(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}").unwrap();
                json!(email.replace_all(&s, "user@example.com").to_string())
            }
            Value::Array(a) => Value::Array(a.iter().map(|x| scrub(x, secrets, paths)).collect()),
            Value::Object(o) => Value::Object(o.iter().map(|(k, x)| (k.clone(), scrub(x, secrets, paths))).collect()),
            _ => v.clone(),
        }
    }

    #[tokio::test]
    #[ignore]
    async fn record_replays() {
        let wanted = std::env::var("TICO_REPLAY_RECORD").unwrap_or_default();
        let proxy = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../runner/tests/fixtures/replay_proxy.py");
        let python = which("python3");
        for plan in plans().into_iter().filter(|p| wanted == "all" || wanted.split(',').any(|w| w == format!("{}/{}", p.harness, p.name))) {
            let real_cli = which(plan.harness);
            let version = String::from_utf8_lossy(&std::process::Command::new(&real_cli).arg("--version").output().unwrap().stdout).trim().to_string();
            let dir = std::env::temp_dir().join(format!("record-{}-{}", plan.harness, uuid::Uuid::new_v4().simple()));
            {
                use std::os::unix::fs::DirBuilderExt;
                std::fs::DirBuilder::new().mode(0o700).create(&dir).unwrap();
            }
            let _gone = Gone(dir.clone());
            let (home, cwd, bin, log) = (dir.join("home"), dir.join("work"), dir.join("bin"), dir.join("log.ndjson"));
            std::fs::create_dir_all(&cwd).unwrap();
            std::fs::create_dir_all(&bin).unwrap();
            let secrets = login(plan.harness, &home, plan.bad_login);
            let wrapper = bin.join(plan.harness);
            crate::util::write_script(&wrapper, &format!("#!/bin/sh\nexec {} {} {} {} \"$@\"\n", quote(&python), quote(&proxy.display().to_string()), quote(&log.display().to_string()), quote(&real_cli)));
            // The CLI's own folder and node's: an npm-installed CLI runs under node.
            let dirs: Vec<String> = [real_cli.clone(), which("node")].iter().filter(|p| !p.is_empty()).map(|p| Path::new(p).parent().unwrap().display().to_string()).collect();
            let path = format!("{}:{}:/usr/bin:/bin", bin.display(), dirs.join(":"));
            let (host, rx) = host(plan.harness, &wrapper.display().to_string(), &path, &home);
            start(&host).await;
            let actions: Vec<Vec<Trigger>> = plan.turns.iter().map(|t| t.1.clone()).collect();
            let today = crate::util::iso(&json!(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs()));
            let mut input = json!({
                "harness": plan.harness, "cli": version, "about": plan.about,
                "source": {"real": true, "recorded": today.as_str().unwrap_or_default().get(..10), "how": "record_replays: the installed CLI through replay_proxy.py"},
                "turns": plan.turns.iter().map(|t| json!({"prompt": t.0, "actions": []})).collect::<Vec<_>>(),
            });
            let lg = log.clone();
            let pace = Pace::Record { log: &log, actions: &actions, until: &plan.until };
            let (output, fired) = drive(plan.harness, &host, rx, &settings(&cwd, &path, &home), &input, pace, move || std::fs::read_to_string(&lg).unwrap_or_default()).await;
            for (t, at) in fired.iter().enumerate() {
                let acts: Vec<Value> = at.iter().zip(&actions[t]).map(|(n, a)| {
                    let mut a = a.2.clone();
                    a["after_events"] = json!(n);
                    a
                }).collect();
                input["turns"][t]["actions"] = json!(acts);
            }
            let limited = output["turns"].as_array().unwrap().iter().flat_map(|t| t.as_array().unwrap()).any(|e| e["limit"] == true);
            assert!(!limited || plan.bad_login || plan.name == "limit-notice", "{}/{}: the login is out of quota; record it on one that is not", plan.harness, plan.name);
            let redactor = crate::redact::Redactor::new(&secrets);
            let host_name = std::fs::read_to_string("/etc/hostname").map(|h| h.trim().to_string()).unwrap_or_default();
            let mut paths = vec![(dir.display().to_string(), "/tmp/replay"), (std::env::var("HOME").unwrap(), "/home/user")];
            if host_name.len() >= 4 {
                paths.push((host_name, "replay-host"));
            }
            let transcript: Vec<Value> = std::fs::read_to_string(&log).unwrap().lines().map(|l| scrub(&serde_json::from_str(l).unwrap(), &redactor, &paths)).collect();
            let output = scrub(&output, &redactor, &paths);
            let out_dir = super::super::fixtures::dir(plan.harness).join("replay").join(plan.name);
            std::fs::create_dir_all(&out_dir).unwrap();
            let lines: Vec<String> = transcript.iter().map(|e| serde_json::to_string(e).unwrap()).collect();
            let text = lines.join("\n") + "\n";
            for secret in &secrets {
                assert!(!text.contains(secret.as_str()) && !output.to_string().contains(secret.as_str()), "a login secret survived scrubbing");
            }
            std::fs::write(out_dir.join("transcript.ndjson"), &text).unwrap();
            std::fs::write(out_dir.join("input.json"), serde_json::to_string_pretty(&input).unwrap() + "\n").unwrap();
            std::fs::write(out_dir.join("output.json"), serde_json::to_string_pretty(&output).unwrap() + "\n").unwrap();
            println!("recorded {}/{}: {} transcript lines", plan.harness, plan.name, lines.len());
        }
    }
}
