//! A bot's own Chrome on a Linux computer (capability `browser_v1`): a bot opts in with a bot.yaml `tools:` entry
//! `service: chrome`. Chrome installs on first need (crate::browser::install), runs headful on the screen's display
//! (crate::screen keeps one up without VNC while a browser wants it), one per bot, as the bot user, with its profile
//! in the runner-home volume so sign-ins survive restarts and updates. All of that bot's runs share it and it stays up
//! between runs; it restarts when it crashes and stops when the bot leaves this computer or drops the tool.
//!
//! Chrome has no debugging port: the runner holds its CDP pipe (crate::browser::cdp) and serves one loopback
//! listener (crate::browser::endpoint). A run gets `TICO_BROWSER_URL=http://127.0.0.1:<port>/<secret>`, the secret
//! random per run (it stops working when the run ends, and it names the run a fill speaks for), for `json/version` +
//! the CDP websocket, `/fill` (types a login granted to that run's bot, username, password or current authenticator
//! code, into the page, never returning it) and `/status`. Login secrets come from the hub with the runner's own
//! token for that run and stay in this process.

mod cdp;
mod endpoint;
mod fill;
mod install;
pub mod totp;

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};
use tokio::sync::{watch, Notify};

use crate::client::Client;
use crate::config::Config;
use crate::util::{diagnostic, log, text_of};

pub const CAPABILITY: &str = "browser_v1";
pub const SERVICE: &str = "chrome";
pub const URL_ENV: &str = "TICO_BROWSER_URL";
/// How often assignments are read for bots that declare Chrome (installs ahead of the first run, stops).
const SCAN_EVERY: Duration = Duration::from_secs(60);
const INSTALL_RETRY: Duration = Duration::from_secs(30 * 60);
const BACKOFF_MAX_S: u64 = 60;
const WIDTH: u32 = crate::screen::WIDTH;
const HEIGHT: u32 = crate::screen::HEIGHT;

/// Whether bot.yaml's tools ask for Chrome.
pub fn declared(manifest: &Value) -> bool {
    let tools = crate::manifest::tools_of(manifest);
    crate::declared_access::reported_entries(&tools).iter().any(|e| text_of(&e["service"]).trim().eq_ignore_ascii_case(SERVICE))
}

/// The values a turn's redactor hides for these logins: each password and authenticator secret.
pub fn login_secrets(logins: &[Value]) -> Vec<String> {
    let mut out = vec![];
    for l in logins {
        if let Some(p) = l["password"].as_str().filter(|p| !p.is_empty()) {
            out.push(p.to_string());
        }
        if let Some(s) = l["totp"]["secret"].as_str().filter(|s| !s.is_empty()) {
            out.push(s.to_string());
            out.push(totp::normalised(s));
        }
    }
    out
}

/// The logins granted to the bot of run `attempt`, read with the runner's own token: the one place the path is spelled.
/// The hub answers 403 for a run of another computer or an inactive bot, 409 `stale_lease` once the run has ended.
pub async fn fetch_logins(runner: &Client, attempt: &str) -> Result<Vec<Value>, crate::client::ApiError> {
    let reply = runner.get_query("runner-login-credentials", &[("attempt", attempt.to_string())]).await?;
    Ok(reply["logins"].as_array().cloned().unwrap_or_default())
}

// ------------------------------------------------------------------------------------------ state

struct Turn {
    id: u64,
    aid: String,
    secret: String,
}

pub(crate) struct Bot {
    name: String,
    dir: PathBuf,
    turns: Mutex<Vec<Turn>>,
    browser: watch::Sender<Option<Arc<cdp::Browser>>>,
    version: Mutex<Option<String>>,
    display: Mutex<Option<String>>,
    problem: Mutex<Option<String>>,
    stopping: AtomicBool,
    stop: Notify,
    /// True once its supervisor has ended (its Chrome closed).
    done: watch::Sender<bool>,
}

impl Bot {
    fn profile(&self) -> PathBuf {
        self.dir.join("profile")
    }

    fn current(&self) -> Option<Arc<cdp::Browser>> {
        self.browser.borrow().clone().filter(|b| b.alive())
    }

    /// The running browser, waiting up to `wait` for one starting.
    pub(crate) async fn ready(&self, wait: Duration) -> Option<Arc<cdp::Browser>> {
        let mut rx = self.browser.subscribe();
        let found = tokio::time::timeout(wait, rx.wait_for(|b| b.as_ref().is_some_and(|b| b.alive()))).await;
        found.ok().and_then(|r| r.ok()).and_then(|b| b.clone())
    }

    fn set_problem(&self, problem: Option<String>) {
        let mut p = self.problem.lock().unwrap();
        if *p != problem {
            if let Some(text) = &problem {
                log(&format!("Tico runner: {}: browser: {text}", self.name));
            }
            *p = problem;
        }
    }

    pub(crate) fn turn_ids(&self) -> Vec<String> {
        self.turns.lock().unwrap().iter().map(|t| t.aid.clone()).collect()
    }

    pub(crate) fn status(&self) -> Value {
        let running = self.current().is_some();
        let mut s = json!({
            "running": running,
            "version": if running { json!(self.version.lock().unwrap().clone()) } else { Value::Null },
            "display": if running { json!(self.display.lock().unwrap().clone()) } else { Value::Null },
            "profile": self.profile().display().to_string(),
            "downloads": self.dir.join("Downloads").display().to_string(),
        });
        if let Some(p) = self.problem.lock().unwrap().clone().or_else(global_problem) {
            s["problem"] = json!(p);
        }
        s
    }
}

#[derive(Clone)]
struct Paths {
    tools: PathBuf,
    home: PathBuf,
}

#[derive(Clone, Default)]
enum Install {
    #[default]
    Unknown,
    Installing,
    Ready(install::Chrome),
    Failed(String, Instant),
}

#[derive(Clone, Default)]
enum Mcp {
    #[default]
    Unknown,
    Ready(PathBuf, PathBuf),
    Skipped(String),
}

struct Shared {
    bots: Mutex<HashMap<String, Arc<Bot>>>,
    chrome: Mutex<Install>,
    mcp: Mutex<Mcp>,
    installed: Notify,
    runner: Mutex<Option<Client>>,
    paths: Mutex<Option<Paths>>,
    scanned: Mutex<Option<Instant>>,
    next_turn: AtomicU64,
    sandbox: tokio::sync::OnceCell<bool>,
}

fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| Shared {
        bots: Mutex::new(HashMap::new()),
        chrome: Mutex::new(Install::Unknown),
        mcp: Mutex::new(Mcp::Unknown),
        installed: Notify::new(),
        runner: Mutex::new(None),
        paths: Mutex::new(None),
        scanned: Mutex::new(None),
        next_turn: AtomicU64::new(1),
        sandbox: tokio::sync::OnceCell::new(),
    })
}

/// The bot and live run a URL secret belongs to.
pub(crate) fn by_secret(secret: &str) -> Option<(Arc<Bot>, String)> {
    let bots = shared().bots.lock().unwrap();
    let mut found = None;
    for b in bots.values() {
        for t in b.turns.lock().unwrap().iter() {
            // Every secret is compared in full, so timing says nothing about which one nearly matched.
            if endpoint::same(t.secret.as_bytes(), secret.as_bytes()) {
                found = Some((b.clone(), t.aid.clone()));
            }
        }
    }
    found
}

pub(crate) fn runner_client() -> Option<Client> {
    shared().runner.lock().unwrap().clone()
}

fn linux() -> bool {
    std::env::consts::OS == "linux"
}

fn paths_for(config: &Config) -> Paths {
    let tools = crate::harness_tools::tools_dir(config);
    // Under isolation HOME is the runner-home volume; elsewhere the profiles sit beside runner.json.
    let home = if crate::isolation::enabled() {
        PathBuf::from(std::env::var("HOME").ok().filter(|h| !h.is_empty()).unwrap_or_else(|| "/home/runner".into())).join("browsers")
    } else {
        config.path.parent().map(Path::to_path_buf).unwrap_or_else(|| PathBuf::from(".")).join("browsers")
    };
    Paths { tools, home }
}

fn valid_bot(bot: &str) -> bool {
    !bot.is_empty() && bot.len() <= 100 && !bot.starts_with('.') && bot.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
}

/// Why no bot's Chrome can run here, if nothing per bot is needed to say so.
fn global_problem() -> Option<String> {
    if !linux() {
        return Some("Chrome for bots runs on Linux computers only for now; on a Mac, use Aside".into());
    }
    let missing = crate::screen::missing_display_tools();
    if !missing.is_empty() {
        return Some(format!("Missing {} for the browser's display", missing.join(", ")));
    }
    match shared().chrome.lock().unwrap().clone() {
        Install::Failed(e, _) => Some(format!("Chrome did not install: {e}")),
        Install::Installing => Some("Chrome is installing".into()),
        _ => None,
    }
}

/// The heartbeat offers `browser_v1` while Chrome can start here: Linux, a display, and no failed install.
pub fn offers() -> bool {
    linux() && crate::screen::missing_display_tools().is_empty() && !matches!(*shared().chrome.lock().unwrap(), Install::Failed(..))
}

/// The health row's `problem` for a bot's `chrome` tool entry, or None when it is fine.
pub fn problem(bot: &str) -> Option<String> {
    let mut parts: Vec<String> = vec![];
    if let Some(p) = global_problem() {
        parts.push(p);
    } else if let Some(p) = shared().bots.lock().unwrap().get(bot).and_then(|b| b.problem.lock().unwrap().clone()) {
        parts.push(p);
    }
    if linux() {
        if let Mcp::Skipped(why) = shared().mcp.lock().unwrap().clone() {
            parts.push(format!("chrome-devtools-mcp is skipped: {why}"));
        }
    }
    (!parts.is_empty()).then(|| parts.join("; ").chars().take(300).collect())
}

// ------------------------------------------------------------------------------------------ runs

/// One run's hold on its bot's browser: the URL its environment carries. The browser stays up after it drops.
pub struct Lease {
    bot: Arc<Bot>,
    id: u64,
    pub url: String,
    pub secret: String,
}

impl Drop for Lease {
    fn drop(&mut self) {
        self.bot.turns.lock().unwrap().retain(|t| t.id != self.id);
    }
}

/// The run `aid` of `bot` uses its Chrome: started if it is not running, and the endpoint up. `runner` is the
/// runner's own client (login secrets are read with it, never with the run's token).
pub async fn lease(config: &Config, runner: &Client, bot: &str, aid: &str) -> Result<Lease, String> {
    if !linux() {
        return Err("Chrome for bots runs on Linux computers only for now".into());
    }
    if !valid_bot(bot) {
        return Err("this bot's name cannot name a browser profile".into());
    }
    let s = shared();
    *s.runner.lock().unwrap() = Some(runner.clone());
    let paths = paths_for(config);
    *s.paths.lock().unwrap() = Some(paths.clone());
    let port = endpoint::start().await?;
    let bot = running(bot, &paths).await;
    let id = s.next_turn.fetch_add(1, Ordering::SeqCst);
    let secret = endpoint::random_secret();
    bot.turns.lock().unwrap().push(Turn { id, aid: aid.to_string(), secret: secret.clone() });
    Ok(Lease { url: format!("http://127.0.0.1:{port}/{secret}"), bot, id, secret })
}

/// The bot's entry, its supervisor started when new. A browser still closing is waited for: two Chromes never
/// share a profile.
async fn running(name: &str, paths: &Paths) -> Arc<Bot> {
    let s = shared();
    let closing = s.bots.lock().unwrap().get(name).filter(|b| b.stopping.load(Ordering::SeqCst)).cloned();
    if let Some(old) = closing {
        let _ = tokio::time::timeout(Duration::from_secs(30), finished(&old)).await;
        let mut bots = s.bots.lock().unwrap();
        if bots.get(name).is_some_and(|b| Arc::ptr_eq(b, &old)) {
            bots.remove(name);
        }
    }
    let mut bots = s.bots.lock().unwrap();
    if let Some(b) = bots.get(name) {
        return b.clone();
    }
    let bot = Arc::new(Bot {
        name: name.to_string(),
        dir: paths.home.join(name),
        turns: Mutex::new(vec![]),
        browser: watch::channel(None).0,
        version: Mutex::new(None),
        display: Mutex::new(None),
        problem: Mutex::new(None),
        stopping: AtomicBool::new(false),
        stop: Notify::new(),
        done: watch::channel(false).0,
    });
    bots.insert(name.to_string(), bot.clone());
    drop(bots);
    want_display();
    tokio::spawn(supervise(bot.clone(), paths.clone()));
    bot
}

fn want_display() {
    let any = shared().bots.lock().unwrap().values().any(|b| !b.stopping.load(Ordering::SeqCst));
    crate::screen::want_display(any);
}

/// Called from the run loop: installs ahead of a chrome bot's first run, and stops the browsers of bots that left
/// this computer or dropped the tool. Returns at once.
pub fn tick(config: &Config, assignments: &[Value]) {
    if !linux() || assignments.is_empty() {
        return; // before the first assignments (or a failed read) nothing is known to have gone
    }
    let s = shared();
    {
        let mut at = s.scanned.lock().unwrap();
        if at.is_some_and(|t| t.elapsed() < SCAN_EVERY) {
            return;
        }
        *at = Some(Instant::now());
    }
    let mine = config.runner_id();
    let mut wanted = HashSet::new();
    for entry in assignments {
        let bot = text_of(&entry["bot"]);
        if entry["state"] != "active" || mine.is_empty() || text_of(&entry["runner_id"]) != mine {
            continue;
        }
        let path = crate::readiness::local_path(config, &bot);
        if crate::manifest::read(&path).is_some_and(|m| declared(&m)) {
            wanted.insert(bot);
        }
    }
    let paths = paths_for(config);
    *s.paths.lock().unwrap() = Some(paths.clone());
    if !wanted.is_empty() {
        tokio::spawn(prepare(paths));
    }
    let gone: Vec<Arc<Bot>> = {
        let bots = s.bots.lock().unwrap();
        bots.iter()
            .filter(|(name, b)| !wanted.contains(*name) && b.turns.lock().unwrap().is_empty() && !b.stopping.load(Ordering::SeqCst))
            .map(|(_, b)| b.clone())
            .collect()
    };
    for bot in gone {
        log(&format!("Tico runner: {}: stopping its browser (the bot left this computer or no longer has the chrome tool)", bot.name));
        bot.stopping.store(true, Ordering::SeqCst);
        bot.stop.notify_one();
    }
    want_display();
}

/// Chrome, then chrome-devtools-mcp, installed once; a failed Chrome install is tried again after INSTALL_RETRY.
async fn prepare(paths: Paths) -> Option<install::Chrome> {
    let s = shared();
    loop {
        let state = s.chrome.lock().unwrap().clone();
        match state {
            Install::Ready(c) if c.exe.is_file() => return Some(c),
            Install::Failed(_, at) if at.elapsed() < INSTALL_RETRY => return None,
            Install::Installing => {
                let wait = s.installed.notified();
                if matches!(*s.chrome.lock().unwrap(), Install::Installing) {
                    wait.await;
                }
                continue;
            }
            _ => {}
        }
        *s.chrome.lock().unwrap() = Install::Installing;
        let result = install::chrome(&paths.tools).await;
        let ready = match result {
            Ok(c) => {
                *s.chrome.lock().unwrap() = Install::Ready(c.clone());
                Some(c)
            }
            Err(e) => {
                diagnostic(&format!("Tico runner: Chrome did not install: {e}"));
                *s.chrome.lock().unwrap() = Install::Failed(e, Instant::now());
                None
            }
        };
        s.installed.notify_waiters();
        if ready.is_some() && matches!(*s.mcp.lock().unwrap(), Mcp::Unknown | Mcp::Skipped(_)) {
            let mcp = match install::node().await {
                Err(why) => Mcp::Skipped(why),
                Ok(node) => match install::devtools_mcp(&paths.tools).await {
                    Ok(entry) => Mcp::Ready(node, entry),
                    Err(e) => Mcp::Skipped(format!("it did not install ({e})")),
                },
            };
            if let Mcp::Skipped(why) = &mcp {
                log(&format!("Tico runner: chrome-devtools-mcp is skipped for bots' browsers: {why}"));
            }
            *s.mcp.lock().unwrap() = mcp;
        }
        return ready;
    }
}

/// The chrome-devtools-mcp server a chrome bot's run gets, when node and the package are there.
pub fn devtools_mcp_server(env: &crate::env::Env) -> Option<Value> {
    let url = env.get(URL_ENV).filter(|u| !u.is_empty())?;
    let Mcp::Ready(node, entry) = shared().mcp.lock().unwrap().clone() else { return None };
    // The URL (and its secret) stays in the environment, off the command line other processes can read.
    let script = "import { pathToFileURL } from 'node:url'; const main = process.argv[1]; \
        process.argv = [process.argv[0], main, '--browserUrl', process.env.TICO_BROWSER_URL, '--no-usage-statistics', '--no-performance-crux']; \
        await import(pathToFileURL(main).href);";
    let mut vars = Map::new();
    vars.insert(URL_ENV.into(), json!(url));
    for k in ["CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS", "CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS", "CHROME_DEVTOOLS_MCP_NO_CONFIG_DISCOVERY"] {
        vars.insert(k.into(), json!("1"));
    }
    Some(json!({
        "command": node.display().to_string(),
        "args": ["--input-type=module", "-e", script, entry.display().to_string()],
        "env": vars,
    }))
}

// ------------------------------------------------------------------------------------------ the supervisor

async fn supervise(bot: Arc<Bot>, paths: Paths) {
    let mut failures: u32 = 0;
    while !bot.stopping.load(Ordering::SeqCst) {
        let wait = match run_once(&bot, &paths).await {
            Ok(lived) => {
                failures = if lived > Duration::from_secs(60) { 1 } else { failures + 1 };
                Duration::from_secs((1u64 << failures.min(6)).min(BACKOFF_MAX_S))
            }
            Err(why) => {
                bot.set_problem(Some(why));
                failures += 1;
                Duration::from_secs((5u64 << failures.min(4)).min(BACKOFF_MAX_S))
            }
        };
        if bot.stopping.load(Ordering::SeqCst) {
            break;
        }
        tokio::select! {
            _ = tokio::time::sleep(wait) => {}
            _ = bot.stop.notified() => {}
        }
    }
    bot.browser.send_replace(None);
    {
        let mut bots = shared().bots.lock().unwrap();
        if bots.get(&bot.name).is_some_and(|b| Arc::ptr_eq(b, &bot)) {
            bots.remove(&bot.name);
        }
    }
    bot.done.send_replace(true);
    want_display();
}

async fn finished(bot: &Bot) {
    let mut rx = bot.done.subscribe();
    let _ = rx.wait_for(|d| *d).await;
}

/// Start the bot's Chrome and keep it until it exits or is stopped: how long it lived, or why it could not start.
async fn run_once(bot: &Arc<Bot>, paths: &Paths) -> Result<Duration, String> {
    if let Some(p) = global_problem().filter(|_| !matches!(*shared().chrome.lock().unwrap(), Install::Installing | Install::Unknown)) {
        return Err(p);
    }
    let chrome = match prepare(paths.clone()).await {
        Some(c) => c,
        None => return Err(global_problem().unwrap_or_else(|| "Chrome is not installed".into())),
    };
    let display = wait_display(Duration::from_secs(30)).await.ok_or("no display for the browser (Xvfb did not start)")?;
    let mut launched = launch(bot, &chrome, &display).await?;
    let started = Instant::now();
    bot.set_problem(None);
    *bot.version.lock().unwrap() = Some(launched.version.clone());
    *bot.display.lock().unwrap() = Some(display.clone());
    bot.browser.send_replace(Some(launched.browser.clone()));
    log(&format!("Tico runner: {}: Chrome {} is up on display {display}", bot.name, launched.version));
    let stopped = tokio::select! {
        _ = launched.child.wait() => false,
        _ = launched.browser.closed() => false,
        _ = bot.stop.notified() => true,
        _ = shutdown_signal() => true,
    };
    bot.browser.send_replace(None);
    if stopped {
        close(&mut launched).await;
    } else {
        let _ = launched.child.start_kill();
        let status = launched.child.wait().await.map(|s| s.to_string()).unwrap_or_default();
        let tail = launched.stderr.lock().unwrap().iter().rev().take(3).rev().cloned().collect::<Vec<_>>().join(" | ");
        log(&format!("Tico runner: {}: Chrome exited ({status}); starting it again. {tail}", bot.name));
    }
    Ok(started.elapsed())
}

async fn close(launched: &mut Launched) {
    // A clean close writes the profile (cookies, sessions) the next start signs in with.
    let _ = launched.browser.call_within("Browser.close", json!({}), None, Duration::from_secs(5)).await;
    if tokio::time::timeout(Duration::from_secs(10), launched.child.wait()).await.is_err() {
        let _ = launched.child.start_kill();
        let _ = launched.child.wait().await;
    }
}

async fn wait_display(wait: Duration) -> Option<String> {
    let deadline = Instant::now() + wait;
    loop {
        if let Some(d) = crate::screen::browser_display() {
            return Some(d);
        }
        if Instant::now() >= deadline {
            return None;
        }
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
}

static SHUTDOWN: OnceLock<watch::Sender<bool>> = OnceLock::new();

fn shutdown_tx() -> &'static watch::Sender<bool> {
    SHUTDOWN.get_or_init(|| watch::channel(false).0)
}

async fn shutdown_signal() {
    let mut rx = shutdown_tx().subscribe();
    let _ = rx.wait_for(|s| *s).await;
}

/// Close every bot's Chrome (cleanly, so profiles are saved) before the runner exits.
pub async fn shutdown() {
    let bots: Vec<Arc<Bot>> = shared().bots.lock().unwrap().values().cloned().collect();
    if bots.is_empty() {
        return;
    }
    for b in &bots {
        b.stopping.store(true, Ordering::SeqCst);
        b.stop.notify_one();
    }
    shutdown_tx().send_replace(true);
    let _ = tokio::time::timeout(Duration::from_secs(20), futures_util::future::join_all(bots.iter().map(|b| finished(b)))).await;
}

// ------------------------------------------------------------------------------------------ launching Chrome

struct Launched {
    child: tokio::process::Child,
    browser: Arc<cdp::Browser>,
    version: String,
    stderr: Arc<Mutex<std::collections::VecDeque<String>>>,
}

/// Run as the bot: clear the lock a Chrome of another container left (it names that host) and the crash flag that
/// would offer to restore pages, set "continue where you left off" (restore_on_startup 1: Chrome then keeps session
/// cookies, so a sign-in without "remember me" survives a runner restart), then exec Chrome.
const START: &str = r#"p="$1"; shift
rm -f "$p/SingletonLock" "$p/SingletonSocket" "$p/SingletonCookie"
f="$p/Default/Preferences"
if [ ! -e "$f" ] && [ ! -L "$f" ]; then mkdir -p "$p/Default" && printf '{"session":{"restore_on_startup":1}}' > "$f"
elif [ -f "$f" ] && [ ! -L "$f" ]; then
  sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/; s/"exited_cleanly":false/"exited_cleanly":true/; s/"restore_on_startup":[0-9]*/"restore_on_startup":1/; s/"session":{}/"session":{"restore_on_startup":1}/' "$f"
  if ! grep -q '"restore_on_startup"' "$f"; then
    if grep -q '"session":{' "$f"; then sed -i 's/"session":{/"session":{"restore_on_startup":1,/' "$f"; else sed -i 's/^{/{"session":{"restore_on_startup":1},/' "$f"; fi
  fi
fi
exec "$@""#;

fn lang() -> String {
    let raw = std::env::var("TICO_BROWSER_LANG").unwrap_or_default();
    let ok = !raw.is_empty()
        && raw.len() <= 35
        && raw.split('-').enumerate().all(|(i, p)| (if i == 0 { 2..=3 } else { 2..=8 }).contains(&p.len()) && p.bytes().all(|b| b.is_ascii_alphanumeric()));
    if ok { raw } else { "en-US".into() }
}

fn plain(name: &str) -> Option<String> {
    std::env::var(name).ok().map(|v| v.trim().to_string()).filter(|v| !v.is_empty() && v.len() < 500 && !v.chars().any(|c| c.is_whitespace() || c.is_control()))
}

/// The Chrome command line after the executable.
fn flags(profile: &Path, sandbox: bool) -> Vec<String> {
    let lang = lang();
    let mut f = vec![
        format!("--user-data-dir={}", profile.display()),
        "--remote-debugging-pipe".into(),
        "--no-first-run".into(),
        "--no-default-browser-check".into(),
        // Pipe mode marks the page as automated (`navigator.webdriver`); sites refuse such sign-ins.
        "--disable-blink-features=AutomationControlled".into(),
        format!("--lang={lang}"),
        format!("--window-size={WIDTH},{HEIGHT}"),
        "--window-position=0,0".into(),
        "--password-store=basic".into(),
        "--disable-dev-shm-usage".into(),
        "--disable-search-engine-choice-screen".into(),
        // This Chrome is updated with the runner, so its own "out of date" prompt only gets in the way.
        "--simulate-outdated-no-au=Tue, 31 Dec 2099 23:59:59 GMT".into(),
    ];
    if !sandbox {
        f.push("--no-sandbox".into());
    }
    if let Some(proxy) = plain("TICO_BROWSER_PROXY") {
        f.push(format!("--proxy-server={proxy}"));
    }
    f
}

/// Whether the bot user can make the namespaces Chrome's sandbox needs (a Docker runner's seccomp says no).
async fn sandbox_works() -> bool {
    *shared()
        .sandbox
        .get_or_init(|| async {
            // SAFETY: geteuid has no preconditions.
            if !crate::isolation::enabled() && unsafe { libc::geteuid() } == 0 {
                return false; // Chrome refuses to sandbox as root
            }
            let mut cmd = crate::isolation::command("unshare");
            cmd.args(["--user", "--map-root-user", "--pid", "--net", "--fork", "true"])
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null());
            let ok = matches!(tokio::time::timeout(Duration::from_secs(10), cmd.status()).await, Ok(Ok(s)) if s.success());
            if !ok {
                log("Tico runner: bots' Chrome runs without its sandbox here (no user namespaces for the bot user)");
            }
            ok
        })
        .await
}

#[cfg(not(target_os = "linux"))]
fn pipe() -> std::io::Result<(std::os::fd::OwnedFd, std::os::fd::OwnedFd)> {
    Err(std::io::Error::new(std::io::ErrorKind::Unsupported, "Linux only"))
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
    Ok(unsafe { (std::os::fd::OwnedFd::from_raw_fd(fds[0]), std::os::fd::OwnedFd::from_raw_fd(fds[1])) })
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

async fn launch(bot: &Bot, chrome: &install::Chrome, display: &str) -> Result<Launched, String> {
    use std::os::fd::AsRawFd;
    let parent = bot.dir.parent().ok_or("no browsers directory")?;
    if !parent.exists() {
        use std::os::unix::fs::DirBuilderExt;
        std::fs::DirBuilder::new().recursive(true).mode(0o711).create(parent).map_err(|e| format!("cannot make {} ({e})", parent.display()))?;
    }
    crate::isolation::mkdir(&bot.dir, 0o700).map_err(|e| format!("cannot make the profile directory ({e})"))?;
    let sandbox = sandbox_works().await;
    let (chrome_reads, we_write) = pipe().map_err(|e| e.to_string())?;
    let (we_read, chrome_writes) = pipe().map_err(|e| e.to_string())?;
    let (child_in, child_out) = (high(&chrome_reads).map_err(|e| e.to_string())?, high(&chrome_writes).map_err(|e| e.to_string())?);
    drop((chrome_reads, chrome_writes));
    let (fd_in, fd_out) = (child_in.as_raw_fd(), child_out.as_raw_fd());

    let mut cmd = match crate::isolation::prefix() {
        Some(p) => {
            let mut c = tokio::process::Command::new(&p[0]);
            c.args(&p[1..]).args(["--pdeathsig", "TERM", "/bin/sh"]);
            c
        }
        None => tokio::process::Command::new("/bin/sh"),
    };
    cmd.args(["-c", START, "tico-chrome"]).arg(bot.profile()).arg(&chrome.exe).args(flags(&bot.profile(), sandbox));
    let lang = lang();
    let posix = lang.replace('-', "_");
    cmd.env_clear()
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .env("HOME", &bot.dir)
        .env("XDG_CONFIG_HOME", bot.dir.join(".config"))
        .env("XDG_CACHE_HOME", bot.dir.join(".cache"))
        .env("DISPLAY", display)
        .env("LANG", format!("{posix}.UTF-8"))
        .env("LANGUAGE", format!("{posix}:{}", lang.split('-').next().unwrap_or("en")))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true);
    if let Some(tz) = plain("TICO_BROWSER_TZ") {
        cmd.env("TZ", tz);
    }
    let isolated = crate::isolation::enabled();
    // SAFETY: only async-signal-safe calls (umask, dup2, prctl) between fork and exec.
    unsafe {
        cmd.pre_exec(move || {
            libc::umask(0o077);
            if libc::dup2(fd_in, 3) < 0 || libc::dup2(fd_out, 4) < 0 {
                return Err(std::io::Error::last_os_error());
            }
            #[cfg(target_os = "linux")]
            if !isolated {
                // A runner that dies takes its bots' browsers with it (setpriv does this under isolation).
                libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
            }
            Ok(())
        });
    }
    let _ = isolated;
    let mut child = cmd.spawn().map_err(|e| format!("cannot start Chrome ({e})"))?;
    drop((child_in, child_out));
    let stderr = Arc::new(Mutex::new(std::collections::VecDeque::new()));
    if let Some(err) = child.stderr.take() {
        let keep = stderr.clone();
        tokio::spawn(async move {
            use tokio::io::AsyncBufReadExt;
            let mut lines = tokio::io::BufReader::new(err).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let mut k = keep.lock().unwrap();
                if k.len() == 20 {
                    k.pop_front();
                }
                k.push_back(line.chars().take(300).collect::<String>());
            }
        });
    }
    let to_chrome = tokio::net::unix::pipe::Sender::from_owned_fd(we_write).map_err(|e| e.to_string())?;
    let from_chrome = tokio::net::unix::pipe::Receiver::from_owned_fd(we_read).map_err(|e| e.to_string())?;
    let browser = cdp::Browser::start(to_chrome, from_chrome);
    let version = match browser.call_within("Browser.getVersion", json!({}), None, Duration::from_secs(45)).await {
        Ok(v) => text_of(&v["product"]).trim_start_matches("Chrome/").to_string(),
        Err(e) => {
            let _ = child.start_kill();
            let _ = child.wait().await;
            let tail = stderr.lock().unwrap().iter().rev().take(3).rev().cloned().collect::<Vec<_>>().join(" | ");
            return Err(format!("Chrome did not start ({e}) {tail}").trim().chars().take(280).collect());
        }
    };
    Ok(Launched { child, browser, version, stderr })
}

#[cfg(test)]
mod live;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_chrome_tools_entry_opts_in() {
        assert!(declared(&json!({"tools": [{"service": "github"}, {"service": "Chrome"}]})));
        assert!(declared(&json!({"access": [{"service": "chrome"}]})));
        assert!(!declared(&json!({"tools": [{"service": "aside"}]})));
        assert!(!declared(&json!({"tools": "chrome"})));
    }

    #[test]
    fn every_password_and_authenticator_secret_is_redacted() {
        let logins = vec![
            json!({"id": "l1", "name": "Acme", "username": "ops@acme.example", "password": "correct horse", "totp": {"secret": "jbsw y3dp ehpk 3pxp"}}),
            json!({"id": "l2", "name": "Other", "username": "u", "password": "battery staple", "totp": null}),
        ];
        let secrets = login_secrets(&logins);
        for v in ["correct horse", "jbsw y3dp ehpk 3pxp", "JBSWY3DPEHPK3PXP", "battery staple"] {
            assert!(secrets.iter().any(|s| s == v), "{v}");
        }
        assert!(!secrets.iter().any(|s| s == "ops@acme.example"));
    }

    #[test]
    fn chrome_never_runs_automated_or_headless_and_takes_only_plain_settings() {
        std::env::set_var("TICO_BROWSER_LANG", "de-DE");
        std::env::set_var("TICO_BROWSER_PROXY", "http://proxy.acme.example:3128");
        let f = flags(Path::new("/home/runner/browsers/ops/profile"), false);
        assert!(f.iter().any(|a| a == "--lang=de-DE") && f.iter().any(|a| a == "--no-sandbox"));
        assert!(f.iter().any(|a| a == "--proxy-server=http://proxy.acme.example:3128"));
        assert!(f.iter().any(|a| a == "--disable-blink-features=AutomationControlled"));
        assert!(!f.iter().any(|a| a.contains("enable-automation") || a.contains("headless") || a.contains("remote-debugging-port")));
        std::env::set_var("TICO_BROWSER_LANG", "en-US; rm -rf");
        std::env::set_var("TICO_BROWSER_PROXY", "http://a b");
        let f = flags(Path::new("/p"), true);
        assert!(f.iter().any(|a| a == "--lang=en-US"));
        assert!(!f.iter().any(|a| a.starts_with("--proxy-server") || a == "--no-sandbox"));
        std::env::remove_var("TICO_BROWSER_LANG");
        std::env::remove_var("TICO_BROWSER_PROXY");
        assert!(!valid_bot("../ops") && !valid_bot(".ops") && valid_bot("ops-bot_2"));
    }
}
