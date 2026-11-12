//! The command loop: `GET runner-browser-commands?wait=25` held open on a task (and a client) of its own, so a
//! browser command never waits behind a claim or a heartbeat, and the reverse. Each account has a worker that runs
//! its commands one at a time and owns its Chrome; different accounts run side by side.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tokio::sync::{mpsc, oneshot, Mutex as AsyncMutex, Notify};

use super::verbs::{failed, Answer, Done, Session};
use super::{fill, launch};
use crate::client::Client;
use crate::config::Config;
use crate::util::{log, text_of};

/// A Chrome idle this long is closed (its profile keeps the sign-in).
const IDLE: Duration = Duration::from_secs(15 * 60);
/// The most Chromes open at once; the least recently used closes first.
pub const MOST_OPEN: usize = 8;

/// A command from the hub. Not `Debug`: it may carry a login value.
pub struct Command {
    pub id: String,
    pub verb: String,
    pub args: Value,
    pub sites: Vec<String>,
    pub profile: String,
    pub value: Option<String>,
    /// For a session on a bot's own computer: the bot, whose Browse-through proxy it uses.
    pub bot: String,
    pub place: String,
    deadline: Instant,
}

impl Command {
    pub fn from(v: &Value) -> Option<Command> {
        let id = text_of(&v["id"]);
        if id.is_empty() || id.len() > 200 {
            return None;
        }
        Some(Command {
            id,
            verb: text_of(&v["verb"]),
            args: v["args"].clone(),
            sites: v["sites"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|s| s.as_str().map(String::from))
                .collect(),
            profile: text_of(&v["profile"]),
            value: v["value"].as_str().map(String::from),
            bot: text_of(&v["bot"]),
            place: text_of(&v["where"]),
            // Leave room for transport after the hub measures the remaining budget.
            deadline: Instant::now()
                + Duration::from_secs_f64(
                    v["expires_in"]
                        .as_f64()
                        .filter(|s| s.is_finite())
                        .unwrap_or(30.0)
                        .clamp(0.0, 30.0),
                )
                .saturating_sub(Duration::from_millis(250)),
        })
    }

    fn arg(&self, name: &str) -> String {
        text_of(&self.args[name])
    }
}

enum Msg {
    Run(Box<Command>),
    Close(oneshot::Sender<()>),
}

struct Slot {
    tx: mpsc::UnboundedSender<Msg>,
    used: Instant,
    open: bool,
}

fn slots() -> &'static Mutex<HashMap<String, Slot>> {
    static SLOTS: OnceLock<Mutex<HashMap<String, Slot>>> = OnceLock::new();
    SLOTS.get_or_init(Default::default)
}

static STOPPING: AtomicBool = AtomicBool::new(false);

fn stop() -> &'static Notify {
    static STOP: OnceLock<Notify> = OnceLock::new();
    STOP.get_or_init(Notify::new)
}

/// How many Chromes are open now.
pub fn open_count() -> usize {
    slots().lock().unwrap().values().filter(|s| s.open).count()
}

fn mark(profile: &str, open: bool) {
    if let Some(s) = slots().lock().unwrap().get_mut(profile) {
        s.open = open;
        s.used = Instant::now();
    }
}

struct Host {
    poll: Client,
    report: Client,
    state: PathBuf,
}

pub fn start(config: Config) {
    let clients = Client::new(&config.url(), &config.token(), 40, 0).and_then(|poll| {
        Client::new(&config.url(), &config.token(), 30, 2).map(|report| (poll, report))
    });
    let (poll, report) = match clients {
        Ok(c) => c,
        Err(e) => {
            log(&format!("Tico runner: browser sessions are off: {e}"));
            return;
        }
    };
    let host = Arc::new(Host {
        poll,
        report,
        state: config.state_dir().join("browsers"),
    });
    tokio::spawn(poll_loop(host));
}

pub async fn shutdown() {
    STOPPING.store(true, Ordering::SeqCst);
    stop().notify_waiters();
    let waiting: Vec<oneshot::Receiver<()>> = slots()
        .lock()
        .unwrap()
        .values()
        .filter(|s| s.open)
        .filter_map(|s| {
            let (tx, rx) = oneshot::channel();
            s.tx.send(Msg::Close(tx)).ok().map(|_| rx)
        })
        .collect();
    let _ = tokio::time::timeout(Duration::from_secs(20), async {
        for rx in waiting {
            let _ = rx.await;
        }
    })
    .await;
}

async fn poll_loop(host: Arc<Host>) {
    let mut backoff = 1u64;
    while !STOPPING.load(Ordering::SeqCst) {
        if launch::chrome().is_none() {
            pause(Duration::from_secs(60)).await;
            continue;
        }
        let began = Instant::now();
        let query = [("wait", "25".to_string())];

        let asked = host.poll.get_query("runner-browser-commands", &query);
        let answer = tokio::select! {
            a = asked => a,
            _ = stop().notified() => break,
        };
        let wait = match answer {
            Ok(v) => {
                backoff = 1;
                let commands = v["commands"].as_array().cloned().unwrap_or_default();
                let empty = commands.is_empty();
                for c in commands {
                    dispatch(&host, &c);
                }
                // A hub that answers an empty list at once (an older one, or one holding nothing for this computer)
                // is not asked in a tight loop.
                if empty && began.elapsed() < Duration::from_secs(2) {
                    Duration::from_secs(5)
                } else {
                    Duration::ZERO
                }
            }
            // A hub from before website tools.
            Err(e) if e.status == 404 => Duration::from_secs(300),
            Err(e) => {
                if backoff == 1 {
                    log(&format!(
                        "Tico runner: browser command poll failed (HTTP {}); will retry",
                        e.status
                    ));
                }
                let w = Duration::from_secs(backoff);
                backoff = (backoff * 2).min(60);
                w
            }
        };
        if !wait.is_zero() {
            pause(wait).await;
        }
    }
}

async fn pause(d: Duration) {
    tokio::select! {
        _ = tokio::time::sleep(d) => {}
        _ = stop().notified() => {}
    }
}

fn dispatch(host: &Arc<Host>, v: &Value) {
    let Some(cmd) = Command::from(v) else { return };
    if !super::valid_profile(&cmd.profile) {
        let host = host.clone();
        tokio::spawn(async move {
            let refused: Done<Answer> =
                Err(failed("bad_request", "That account is not a profile name."));
            report(&host, &cmd, refused, None).await;
        });
        return;
    }
    let mut all = slots().lock().unwrap();
    let slot = all.entry(cmd.profile.clone()).or_insert_with(|| {
        let (tx, rx) = mpsc::unbounded_channel();
        tokio::spawn(worker(host.clone(), cmd.profile.clone(), rx));
        Slot {
            tx,
            used: Instant::now(),
            open: false,
        }
    });
    slot.used = Instant::now();
    let _ = slot.tx.send(Msg::Run(Box::new(cmd)));
}

/// One account's commands, in order, and its Chrome.
async fn worker(host: Arc<Host>, profile: String, mut rx: mpsc::UnboundedReceiver<Msg>) {
    let mut session: Option<Session> = None;
    loop {
        let msg = tokio::select! {
            m = rx.recv() => m,
            _ = tokio::time::sleep(IDLE), if session.is_some() => {
                close(&mut session, &profile).await;
                continue;
            }
        };
        match msg {
            Some(Msg::Run(cmd)) => {
                let left = cmd.deadline.saturating_duration_since(Instant::now());
                if left.is_zero() {
                    report(
                        &host,
                        &cmd,
                        Err(failed(
                            "browser_timeout",
                            "This browser command expired; read the page before trying again.",
                        )),
                        None,
                    )
                    .await;
                    continue;
                }

                let done = match tokio::time::timeout(left, run(&host, &mut session, &cmd)).await {
                    Ok(done) => done,
                    Err(_) => {
                        close(&mut session, &profile).await;
                        Err(failed(
                            "browser_timeout",
                            "This browser command expired; read the page before trying again.",
                        ))
                    }
                };

                if let Some(s) = &session {
                    s.release().await;
                }

                report(&host, &cmd, done, session.as_mut()).await;

                mark(&profile, session.is_some());
            }
            Some(Msg::Close(done)) => {
                close(&mut session, &profile).await;
                let _ = done.send(());
            }
            None => {
                close(&mut session, &profile).await;
                return;
            }
        }
    }
}

async fn close(session: &mut Option<Session>, profile: &str) {
    if let Some(s) = session.take() {
        s.close().await;
    }
    mark(profile, false);
}

fn starts() -> &'static AsyncMutex<()> {
    static STARTS: OnceLock<AsyncMutex<()>> = OnceLock::new();
    STARTS.get_or_init(|| AsyncMutex::new(()))
}

/// Reserve a browser slot before launching: two accounts cannot evict the same oldest browser and both take its
/// one place. A reservation counts as open until either launch fails or that account closes.
async fn reserve(
    all: &Mutex<HashMap<String, Slot>>,
    gate: &AsyncMutex<()>,
    profile: &str,
) -> Done<()> {
    let _guard = gate.lock().await;
    let oldest = {
        let slots = all.lock().unwrap();
        if slots.values().filter(|s| s.open).count() < MOST_OPEN {
            None
        } else {
            slots
                .iter()
                .filter(|(p, s)| s.open && p.as_str() != profile)
                .min_by_key(|(_, s)| s.used)
                .map(|(_, s)| s.tx.clone())
        }
    };
    if let Some(tx) = oldest {
        let (done, wait) = oneshot::channel();
        let sent = tx.send(Msg::Close(done)).is_ok();
        if !sent
            || !matches!(
                tokio::time::timeout(Duration::from_secs(20), wait).await,
                Ok(Ok(()))
            )
        {
            return Err(failed(
                "browser_timeout",
                "The oldest browser did not close; open this website again.",
            ));
        }
    }
    let mut slots = all.lock().unwrap();
    if slots.values().filter(|s| s.open).count() >= MOST_OPEN {
        return Err(failed(
            "browser_timeout",
            "No browser slot is available; open this website again.",
        ));
    }
    let slot = slots.get_mut(profile).ok_or_else(|| {
        failed(
            "browser_failed",
            "This browser worker stopped; open the website again.",
        )
    })?;
    slot.open = true;
    slot.used = Instant::now();
    Ok(())
}

/// The session for `cmd`, started (or restarted after a crash, or for another Browse-through proxy) as needed.
async fn ready<'a>(
    host: &Host,
    session: &'a mut Option<Session>,
    cmd: &Command,
) -> Done<&'a mut Session> {
    let proxy = if cmd.place == "own" && !cmd.bot.is_empty() {
        crate::browser_exit::runner_proxy(&cmd.bot)
    } else {
        None
    };
    let stale = session
        .as_ref()
        .is_some_and(|s| !s.alive() || s.proxy != proxy);
    // The login values typed before a restart stay hidden in what the new Chrome reports.
    let mut typed = None;
    if stale {
        if let Some(s) = session.take() {
            typed = Some(s.typed.clone());
            if !s.alive() {
                log(&format!(
                    "Tico runner: a browser session stopped ({}); it starts again",
                    s.launched.tail()
                ));
            }
            s.close().await;
        }
    }
    if session.is_none() {
        let exe = launch::chrome()
            .ok_or_else(|| failed("no_chrome", "Google Chrome is not on this computer."))?;

        reserve(slots(), starts(), &cmd.profile).await?;

        let dir = host.state.join(&cmd.profile);
        let launched = match launch::launch(&exe, &dir.join("profile"), proxy.as_deref()).await {
            Ok(launched) => launched,
            Err(e) => {
                mark(&cmd.profile, false);
                return Err(failed("browser_failed", e));
            }
        };

        log(&format!(
            "Tico runner: Chrome {} started for a website session",
            launched.version
        ));
        let started = Session::start(launched, proxy, dir.join("files")).await;
        match started {
            Ok(mut s) => {
                if let Some(t) = typed {
                    s.typed = t;
                }

                *session = Some(s)
            }
            Err(e) => {
                mark(&cmd.profile, false);
                return Err(e);
            }
        }
    }
    let s = session.as_mut().expect("a session was just started");
    s.follow().await?;
    Ok(s)
}

async fn run(host: &Host, session: &mut Option<Session>, cmd: &Command) -> Done<Answer> {
    let verb = cmd.verb.as_str();
    if matches!(verb, "fill" | "copy") {
        let value = cmd.value.as_deref().unwrap_or("");
        // Hidden before it is used: a page read back, or anything a turn here prints, never shows it.
        let field = cmd.arg("field");
        let code = field == "code";
        if !value.is_empty()
            && (field == "password"
                || code
                || value.chars().count() >= crate::redact::LOGIN_MIN_LENGTH)
        {
            crate::redact::hide_everywhere(value, code);
        }
        if verb == "copy" {
            return fill::copy(value).await.map(|_| Answer::default());
        }
    }
    if verb == "close" {
        close(session, &cmd.profile).await;
        return Ok(Answer::default());
    }
    let s = ready(host, session, cmd).await?;

    if verb != "open" && !s.opened {
        return Err(failed(
            "session_lost",
            "This browser session was lost; open the website again.",
        ));
    }
    match verb {
        "open" => {
            s.open(&cmd.arg("url"), &cmd.sites, cmd.args["keep"] == true)
                .await
        }
        "read" => s.read().await,
        "click" => s.click(&cmd.arg("ref")).await,
        "type" => {
            s.type_text(
                &cmd.arg("ref"),
                &cmd.arg("text"),
                cmd.args["submit"] == true,
            )
            .await
        }
        "press" | "input_key" => s.press(&cmd.arg("key")).await,
        "scroll" | "input_scroll" => s.scroll(cmd.args["dy"].as_f64().unwrap_or(600.0)).await,
        "back" => s.back().await,
        "screenshot" => s.screenshot().await,
        "download" => s.download().await,
        "upload" => {
            s.upload(&cmd.arg("ref"), &cmd.arg("name"), &cmd.arg("data"))
                .await
        }
        "input_click" => {
            let at = |k: &str| cmd.args[k].as_f64().unwrap_or(-1.0);
            s.input_click(at("x"), at("y")).await
        }
        "input_type" => s.input_type(&cmd.arg("text")).await,
        "fill" => {
            let value = cmd.value.as_deref().unwrap_or("");
            let field = cmd.arg("field");
            let r = cmd.args["ref"].as_str().filter(|r| !r.trim().is_empty());
            // Hidden in this session's reports before it can reach the page, so a fill that stops halfway is
            // covered too.
            if field == "code" {
                s.typed.add_codes(&[value.to_string()]);
            } else if field == "password"
                || value.chars().count() >= crate::redact::LOGIN_MIN_LENGTH
            {
                s.typed.add_login(&[value.to_string()]);
            }
            fill::fill(s, &cmd.sites, &field, r, value)
                .await
                .map(|_| Answer::default())
        }
        _ => Err(failed(
            "bad_request",
            format!(
                "Unknown verb {:?}.",
                verb.chars().take(40).collect::<String>()
            ),
        )),
    }
}

/// The report body for a command's result: no login value in any of it.
fn body(
    done: &Done<Answer>,
    url: Option<String>,
    title: Option<String>,
    scrub: impl Fn(&str) -> String,
) -> Value {
    let mut b = match done {
        Ok(a) => {
            let mut b = json!({"ok": true});
            if let Some(t) = &a.text {
                b["text"] = json!(scrub(t));
            }
            if let Some(i) = &a.image {
                b["image"] = json!(i);
            }
            b
        }
        Err(f) => json!({"ok": false, "error": f.code, "text": scrub(&f.text)}),
    };
    if let Some(u) = url {
        b["url"] = json!(scrub(&u));
    }
    if let Some(t) = title {
        b["title"] = json!(scrub(&t));
    }
    b["signed_in"] = Value::Null;
    b
}

async fn report(host: &Host, cmd: &Command, done: Done<Answer>, session: Option<&mut Session>) {
    let (url, title, session) = match session {
        Some(s) if s.alive() => {
            let (u, t) = s.info().await;
            (Some(u), Some(t), Some(&*s))
        }
        other => (None, None, other.map(|s| &*s)),
    };
    let mut done = done;
    if let Ok(Answer {
        file: Some((name, bytes)),
        ..
    }) = &done
    {
        let path = format!(
            "/api/v2/runner-browser-commands/{}/file?name={}",
            crate::client::quote_plus(&cmd.id),
            crate::client::quote_plus(name)
        );
        let key = format!("browser-file-{}", cmd.id);
        if let Err(e) = host.report.post_raw(&path, bytes.clone(), &key).await {
            log(&format!(
                "Tico runner: a browser download did not reach the hub ({})",
                e.status
            ));
            done = Err(failed("upload_failed", "The file did not reach Tico."));
        }
    }
    let scrub = |text: &str| -> String {
        let own = session.map_or(std::borrow::Cow::Borrowed(text), |s| {
            s.typed.scrub_text(text)
        });
        crate::redact::scrub_log(&own)
    };
    let b = body(&done, url, title, scrub);
    let path = format!(
        "runner-browser-commands/{}/report",
        crate::client::quote_plus(&cmd.id)
    );
    let key = format!("browser-report-{}", cmd.id);
    if let Err(e) = host.report.post(&path, &b, Some(&key)).await {
        log(&format!(
            "Tico runner: a browser command's result did not reach the hub ({} {})",
            e.status, e.code
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn two_starts_at_the_limit_each_reserve_a_different_place() {
        let pool = Arc::new(Mutex::new(HashMap::<String, Slot>::new()));
        let closed = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let mut workers = vec![];
        for n in 0..MOST_OPEN {
            let name = format!("old-{n}");
            let (tx, mut rx) = mpsc::unbounded_channel();
            pool.lock().unwrap().insert(
                name.clone(),
                Slot {
                    tx,
                    used: Instant::now() - Duration::from_secs(10),
                    open: true,
                },
            );
            let pool = pool.clone();
            let closed = closed.clone();
            workers.push(tokio::spawn(async move {
                while let Some(Msg::Close(done)) = rx.recv().await {
                    // Both starts reach the limit before the first close can finish without the reservation gate.
                    tokio::task::yield_now().await;
                    pool.lock().unwrap().get_mut(&name).unwrap().open = false;
                    closed.fetch_add(1, Ordering::SeqCst);
                    let _ = done.send(());
                }
            }));
        }
        for name in ["new-a", "new-b"] {
            let (tx, _rx) = mpsc::unbounded_channel();
            pool.lock().unwrap().insert(
                name.into(),
                Slot {
                    tx,
                    used: Instant::now(),
                    open: false,
                },
            );
        }
        let gate = AsyncMutex::new(());
        let (a, b) = tokio::time::timeout(Duration::from_secs(3), async {
            tokio::join!(
                reserve(&pool, &gate, "new-a"),
                reserve(&pool, &gate, "new-b")
            )
        })
        .await
        .unwrap();
        a.unwrap();
        b.unwrap();
        {
            let slots = pool.lock().unwrap();
            assert_eq!(slots.values().filter(|s| s.open).count(), MOST_OPEN);
            assert!(slots["new-a"].open && slots["new-b"].open);
        }
        assert_eq!(closed.load(Ordering::SeqCst), 2);
        for worker in workers {
            worker.abort();
            let _ = worker.await;
        }
    }

    #[test]
    fn a_report_carries_the_result_and_never_the_value() {
        let cmd = Command::from(&json!({"id": "bc_1", "account": "c1", "tool": "t1", "verb": "fill",
            "args": {"field": "password"}, "sites": ["acme.example"], "profile": "c1", "value": "pw-canary-9"}))
        .unwrap();
        assert_eq!(
            (cmd.verb.as_str(), cmd.sites.len(), cmd.value.as_deref()),
            ("fill", 1, Some("pw-canary-9"))
        );
        assert!(Command::from(&json!({"verb": "read"})).is_none(), "no id");

        let typed = crate::redact::Redactor::new(&[]);
        typed.add_login(&["pw-canary-9".into()]);
        let scrub = |t: &str| typed.scrub_text(t).into_owned();
        let read: Done<Answer> = Ok(Answer {
            text: Some("[e2] textbox \"Password\" = \"pw-canary-9\"".into()),
            ..Answer::default()
        });
        let b = body(
            &read,
            Some("https://acme.example/?u=pw-canary-9".into()),
            Some("Acme pw-canary-9".into()),
            scrub,
        );
        assert_eq!(b["ok"], true);
        assert_eq!(b["text"], "[e2] textbox \"Password\" = \"••••\"");
        assert_eq!(
            (b["url"].as_str(), b["title"].as_str()),
            (Some("https://acme.example/?u=••••"), Some("Acme ••••"))
        );
        assert!(b["signed_in"].is_null());

        let refused: Done<Answer> = Err(failed(
            "not_on_site",
            "This page is not one of this tool's sites.",
        ));
        let b = body(&refused, None, None, |t: &str| t.to_string());
        assert_eq!(
            (b["ok"].as_bool(), b["error"].as_str()),
            (Some(false), Some("not_on_site"))
        );
        assert!(b.get("url").is_none());
        assert!(b["signed_in"].is_null() && b.get("signed_in").is_some());
        assert!(!b.to_string().contains("pw-canary-9"));
    }
}
