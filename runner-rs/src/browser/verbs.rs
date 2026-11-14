//! One account's browser session and the verbs a bot or a person sends it. The session drives one page (the tab the
//! profile reopened, or a page it opened since) over the pipe. Page script never sees the runner: no domain is
//! enabled, and every script the runner runs is in an isolated world of its own (`Page.createIsolatedWorld`), where
//! the elements a `read` numbered are kept until the next read or navigation.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

use base64::Engine;
use serde_json::{json, Value};

use super::cdp::Browser;
use super::launch::{self, Launched};
use crate::util::text_of;

const WORLD: &str = "tico";
/// Objects the runner's scripts hand back are kept in this group and released after each command.
const GROUP: &str = "tico";
const LOAD_WAIT: Duration = Duration::from_secs(15);
const SHOT_LIMIT: usize = 1_000_000;
const DOWNLOAD_LIMIT: u64 = 20 * 1024 * 1024;
const UPLOAD_LIMIT: usize = 10 * 1024 * 1024;
const TEXT_LIMIT: usize = 10_000;

/// Why a command failed: a code the hub passes on, and a sentence for whoever sent it.
#[derive(Debug, Clone, PartialEq)]
pub struct Failed {
    pub code: &'static str,
    pub text: String,
}

pub fn failed(code: &'static str, text: impl Into<String>) -> Failed {
    Failed {
        code,
        text: text.into(),
    }
}

pub type Done<T> = Result<T, Failed>;

/// What a command answers besides the page's url and title.
#[derive(Default)]
pub struct Answer {
    pub text: Option<String>,
    pub image: Option<String>,
    pub file: Option<(String, Vec<u8>)>,
}

impl Answer {
    fn text(text: impl Into<String>) -> Answer {
        Answer {
            text: Some(text.into()),
            ..Answer::default()
        }
    }
}

fn stale() -> Failed {
    failed(
        "stale_ref",
        "That element is gone or the page changed: read the page again.",
    )
}

fn page_error(e: String) -> Failed {
    failed("page_error", e.chars().take(200).collect::<String>())
}

struct World {
    frame: String,
    loader: String,
    context: i64,
}

/// A script's run in an execution context: its value, or (`by_value` false) the result object itself.
pub async fn eval_in(
    browser: &Browser,
    session: &str,
    context: i64,
    expression: &str,
    by_value: bool,
) -> Result<Value, String> {
    let r = browser
        .call_within(
            "Runtime.evaluate",
            json!({
                "expression": expression,
                "contextId": context,
                "returnByValue": by_value,
                "awaitPromise": true,
                "objectGroup": GROUP,
            }),
            Some(session),
            Duration::from_secs(20),
        )
        .await?;
    if let Some(ex) = r.get("exceptionDetails") {
        let why = text_of(&ex["exception"]["description"]);
        let why = why.lines().next().unwrap_or("").to_string();
        return Err(format!("script error: {why}").chars().take(200).collect());
    }
    Ok(r["result"].clone())
}

/// `function` called on the object `object`, its answer by value.
pub async fn call_on(
    browser: &Browser,
    session: &str,
    object: &str,
    function: &str,
) -> Done<Value> {
    let r = browser
        .call_within(
            "Runtime.callFunctionOn",
            json!({"objectId": object, "functionDeclaration": function, "returnByValue": true}),
            Some(session),
            Duration::from_secs(10),
        )
        .await
        .map_err(|e| {
            if gone_context(&e) {
                stale()
            } else {
                page_error(e)
            }
        })?;
    if r.get("exceptionDetails").is_some() {
        return Err(failed("no_field", "That element cannot be used that way."));
    }
    Ok(r["result"]["value"].clone())
}

/// A context, or an object in one, that navigation took away.
fn gone_context(e: &str) -> bool {
    let e = e.to_lowercase();
    e.contains("cannot find context")
        || e.contains("context was destroyed")
        || e.contains("could not find object")
        || e.contains("no node with given id")
        || e.contains("node is detached")
}

/// The object id of a script result that is an element.
pub fn node_of(result: &Value) -> Option<String> {
    (result["subtype"] == "node")
        .then(|| result["objectId"].as_str().map(String::from))
        .flatten()
}

/// A new isolated world in `frame` (or the one this name already has there): its execution context.
pub async fn create_world(browser: &Browser, session: &str, frame: &str) -> Done<i64> {
    let r = browser
        .call(
            "Page.createIsolatedWorld",
            json!({"frameId": frame, "worldName": WORLD, "grantUniveralAccess": false}),
            Some(session),
        )
        .await
        .map_err(page_error)?;
    r["executionContextId"]
        .as_i64()
        .ok_or_else(|| failed("page_error", "The page has no place to run in."))
}

/// The page targets Chrome has open: (id, url, opener).
async fn pages(browser: &Browser) -> Done<Vec<(String, String, String)>> {
    let r = browser
        .call("Target.getTargets", json!({}), None)
        .await
        .map_err(page_error)?;
    Ok(r["targetInfos"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|t| t["type"] == "page")
        .filter(|t| {
            let url = text_of(&t["url"]);
            !url.starts_with("devtools://") && !url.starts_with("chrome-extension://")
        })
        .map(|t| {
            (
                text_of(&t["targetId"]),
                text_of(&t["url"]),
                text_of(&t["openerId"]),
            )
        })
        .collect())
}

/// The page a session starts on: the first page Chrome has, else a new tab.
async fn first_page(browser: &Browser) -> Done<String> {
    if let Some((id, _, _)) = pages(browser).await?.into_iter().next() {
        return Ok(id);
    }
    let r = browser
        .call("Target.createTarget", json!({"url": "about:blank"}), None)
        .await
        .map_err(page_error)?;
    Ok(text_of(&r["targetId"]))
}

pub struct Session {
    pub launched: Launched,
    /// The Browse-through proxy this Chrome was started with.
    pub proxy: Option<String>,
    target: String,
    session: String,
    world: Option<World>,
    dir: PathBuf,
    /// The login values typed in this session: what it reports back (text, url, title) is scrubbed of them. Kept
    /// when Chrome restarts for the same account.
    pub typed: Arc<crate::redact::Redactor>,
    /// A browser restarted or evicted must be opened before it accepts another bot action.
    pub opened: bool,
}

impl Session {
    /// Drive the Chrome just started: its first page, downloads into `dir`/downloads, uploads from `dir`/uploads.
    pub async fn start(launched: Launched, proxy: Option<String>, dir: PathBuf) -> Done<Session> {
        // A file from an earlier session is not this one's newest download, and an upload is not kept past it.
        for sub in ["downloads", "uploads"] {
            let _ = std::fs::remove_dir_all(dir.join(sub));
            launch::private_dir(&dir.join(sub)).map_err(|e| {
                failed(
                    "browser_failed",
                    format!("cannot make the session folder ({e})"),
                )
            })?;
        }
        let browser = launched.browser.clone();
        browser
            .call(
                "Browser.setDownloadBehavior",
                json!({"behavior": "allow", "downloadPath": dir.join("downloads").display().to_string()}),
                None,
            )
            .await
            .map_err(page_error)?;
        let target = first_page(&browser).await?;
        let session = browser.attach(&target).await.map_err(page_error)?;
        let s = Session {
            launched,
            proxy,
            target,
            session,
            world: None,
            dir,
            typed: Arc::new(crate::redact::Redactor::new(&[])),
            opened: false,
        };
        s.prepare().await;
        Ok(s)
    }

    /// A page in a covered window still acts focused, so a sign-in form that checks focus works the same.
    async fn prepare(&self) {
        let _ = self
            .call(
                "Emulation.setFocusEmulationEnabled",
                json!({"enabled": true}),
            )
            .await;
    }

    pub fn browser(&self) -> Arc<Browser> {
        self.launched.browser.clone()
    }

    pub fn alive(&self) -> bool {
        self.launched.browser.alive()
    }

    pub fn page_session(&self) -> &str {
        &self.session
    }

    /// Close Chrome cleanly and drop this session's files.
    pub async fn close(mut self) {
        launch::close(&mut self.launched).await;
        let _ = std::fs::remove_dir_all(self.dir.join("uploads"));
    }

    async fn call(&self, method: &str, params: Value) -> Done<Value> {
        self.launched
            .browser
            .call(method, params, Some(&self.session))
            .await
            .map_err(page_error)
    }

    /// The page's url and title, as Chrome has them (not as page script says).
    pub async fn info(&self) -> (String, String) {
        let r = self
            .launched
            .browser
            .call(
                "Target.getTargetInfo",
                json!({"targetId": self.target}),
                None,
            )
            .await
            .unwrap_or_default();
        (
            text_of(&r["targetInfo"]["url"]),
            text_of(&r["targetInfo"]["title"]),
        )
    }

    pub async fn url(&self) -> String {
        self.info().await.0
    }

    async fn switch(&mut self, target: String) -> Done<()> {
        let browser = self.browser();
        let session = browser.attach(&target).await.map_err(page_error)?;
        browser.detach(&self.session).await;
        self.target = target;
        self.session = session;
        self.world = None;
        let _ = browser
            .call(
                "Target.activateTarget",
                json!({"targetId": self.target}),
                None,
            )
            .await;
        self.prepare().await;
        Ok(())
    }

    /// Before each command: the page this session drives is still open, else (a popup closed) the newest other one,
    /// else a new tab.
    pub async fn follow(&mut self) -> Done<()> {
        let browser = self.browser();
        let open = pages(&browser).await?;
        if open.iter().any(|(id, _, _)| *id == self.target) {
            return Ok(());
        }
        let next = match open.into_iter().last() {
            Some((id, _, _)) => id,
            None => first_page(&browser).await?,
        };
        self.switch(next).await
    }

    /// After a click: a page this one opened (a popup, a new tab) becomes the one driven.
    async fn adopt_opened(&mut self, before: &[String]) {
        let Ok(now) = pages(&self.browser()).await else {
            return;
        };
        let opened = now
            .into_iter()
            .filter(|(id, _, _)| !before.contains(id))
            .find(|(_, _, opener)| *opener == self.target);
        if let Some((id, _, _)) = opened {
            let _ = self.switch(id).await;
        }
    }

    async fn page_ids(&self) -> Vec<String> {
        pages(&self.browser())
            .await
            .unwrap_or_default()
            .into_iter()
            .map(|(id, _, _)| id)
            .collect()
    }

    /// The main frame: (id, loader id). The loader changes with each new document.
    async fn main_frame(&self) -> Done<(String, String)> {
        let r = self.call("Page.getFrameTree", json!({})).await?;
        let f = &r["frameTree"]["frame"];
        Ok((text_of(&f["id"]), text_of(&f["loaderId"])))
    }

    /// The runner's world in the page's current document.
    pub async fn world(&mut self) -> Done<i64> {
        let (frame, loader) = self.main_frame().await?;
        if let Some(w) = &self.world {
            if w.frame == frame && w.loader == loader {
                return Ok(w.context);
            }
        }
        let context = create_world(&self.browser(), &self.session, &frame).await?;
        self.world = Some(World {
            frame,
            loader,
            context,
        });
        Ok(context)
    }

    /// Run `expression` in the runner's world; a world navigation took away is made again once.
    pub async fn eval(&mut self, expression: &str, by_value: bool) -> Done<Value> {
        for attempt in 0..2 {
            let context = self.world().await?;
            match eval_in(
                &self.browser(),
                &self.session,
                context,
                expression,
                by_value,
            )
            .await
            {
                Ok(v) => return Ok(v),
                Err(e) if attempt == 0 && gone_context(&e) => self.world = None,
                Err(e) => return Err(page_error(e)),
            }
        }
        Err(failed("page_error", "The page kept changing."))
    }

    /// The element a `read` numbered `e<n>`, while it is still on the page.
    pub async fn element(&mut self, r: &str) -> Done<String> {
        let n = ref_index(r).ok_or_else(stale)?;
        let expr = format!(
            "(() => {{ const r = globalThis.__ticoRefs; const e = r && r[{}]; return e && e.isConnected ? e : null; }})()",
            n - 1
        );
        let v = self.eval(&expr, false).await?;
        node_of(&v).ok_or_else(stale)
    }

    /// Objects the command's scripts handed back are let go.
    pub async fn release(&self) {
        let _ = self
            .call("Runtime.releaseObjectGroup", json!({"objectGroup": GROUP}))
            .await;
    }

    /// Wait for the page's document to load: complete, or usable after a while.
    async fn wait_load(&mut self, within: Duration) {
        let began = Instant::now();
        while began.elapsed() < within {
            if let Ok(v) = self.eval("document.readyState", true).await {
                let state = v["value"].as_str().unwrap_or("").to_string();
                if state == "complete"
                    || (state == "interactive" && began.elapsed() > Duration::from_secs(5))
                {
                    break;
                }
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
        tokio::time::sleep(Duration::from_millis(300)).await;
    }

    /// After an action that may navigate: a new document that arrives soon is waited for.
    async fn settle(&mut self, before: &str) {
        let began = Instant::now();
        while began.elapsed() < Duration::from_millis(1500) {
            tokio::time::sleep(Duration::from_millis(150)).await;
            match self.main_frame().await {
                Ok((_, loader)) if loader != before => {
                    self.wait_load(LOAD_WAIT).await;
                    return;
                }
                Ok(_) => {}
                Err(_) => return,
            }
        }
    }

    async fn loader(&self) -> String {
        self.main_frame().await.map(|(_, l)| l).unwrap_or_default()
    }

    // ---- the verbs ----

    pub async fn open(&mut self, url: &str, sites: &[String], keep: bool) -> Done<Answer> {
        if url.trim().is_empty() || keep && super::fill::page_allowed(&self.url().await, sites) {
            self.call("Page.bringToFront", json!({})).await?;
            self.opened = true;
            return Ok(Answer::default());
        }
        let url = url.trim().to_string();
        let allowed = super::fill::page_allowed(&url, sites);
        if !allowed {
            return Err(failed(
                "not_on_site",
                "That address is not one of this tool's sites.",
            ));
        }
        let r = self.call("Page.navigate", json!({"url": url})).await?;
        let error = text_of(&r["errorText"]);
        if !error.is_empty() {
            return Err(failed(
                "navigation_failed",
                format!("The page did not open ({error})."),
            ));
        }
        self.world = None;
        self.wait_load(LOAD_WAIT).await;
        self.opened = true;
        Ok(Answer::default())
    }

    pub async fn read(&mut self) -> Done<Answer> {
        let v = self.eval(super::outline::SCRIPT, true).await?;
        let (url, title) = self.info().await;
        Ok(Answer::text(super::outline::format(
            &title,
            &url,
            &v["value"],
        )))
    }

    pub async fn click(&mut self, r: &str) -> Done<Answer> {
        let object = self.element(r).await?;
        let (before, opened) = (self.loader().await, self.page_ids().await);
        let _ = self
            .call("DOM.scrollIntoViewIfNeeded", json!({"objectId": object}))
            .await;
        let quads = self
            .call("DOM.getContentQuads", json!({"objectId": object}))
            .await
            .unwrap_or_default();
        match centre(&quads["quads"]) {
            Some((x, y)) => self.mouse_click(x, y).await?,
            // Nothing drawn to aim at (a zero-size control): the element's own click.
            None => {
                call_on(
                    &self.browser(),
                    &self.session,
                    &object,
                    "function () { this.click(); }",
                )
                .await?;
            }
        }
        self.settle(&before).await;
        self.adopt_opened(&opened).await;
        Ok(Answer::default())
    }

    async fn mouse_click(&self, x: f64, y: f64) -> Done<()> {
        self.call(
            "Input.dispatchMouseEvent",
            json!({"type": "mouseMoved", "x": x, "y": y}),
        )
        .await?;
        for kind in ["mousePressed", "mouseReleased"] {
            let buttons = if kind == "mousePressed" { 1 } else { 0 };
            self.call(
                "Input.dispatchMouseEvent",
                json!({"type": kind, "x": x, "y": y, "button": "left", "buttons": buttons, "clickCount": 1}),
            )
            .await?;
        }
        Ok(())
    }

    /// Focus a text field (an input, a textarea, an editable element) and select what it holds.
    pub async fn focus_field(&self, session: &str, object: &str) -> Done<()> {
        let took = call_on(&self.browser(), session, object, FOCUS).await?;
        if took != "ok" {
            return Err(failed("no_field", "That element does not take text."));
        }
        self.select_all(session).await;
        Ok(())
    }

    /// Select all in the focused field the way a person does, for a field whose own select() does nothing.
    async fn select_all(&self, session: &str) {
        let modifiers = if std::env::consts::OS == "macos" {
            4
        } else {
            2
        };
        for kind in ["rawKeyDown", "keyUp"] {
            let _ = self
                .launched
                .browser
                .call(
                    "Input.dispatchKeyEvent",
                    json!({"type": kind, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65,
                           "modifiers": modifiers, "commands": if kind == "rawKeyDown" { json!(["selectAll"]) } else { json!([]) }}),
                    Some(session),
                )
                .await;
        }
    }

    pub async fn type_text(&mut self, r: &str, text: &str, submit: bool) -> Done<Answer> {
        if text.chars().count() > TEXT_LIMIT {
            return Err(failed("bad_request", "That text is too long."));
        }
        let object = self.element(r).await?;
        let session = self.session.clone();
        self.focus_field(&session, &object).await?;
        if text.is_empty() {
            self.press_key("Backspace").await?;
        } else {
            self.call("Input.insertText", json!({"text": text})).await?;
        }
        if submit {
            let before = self.loader().await;
            self.press_key("Enter").await?;
            self.settle(&before).await;
        }
        Ok(Answer::default())
    }

    async fn press_key(&self, key: &str) -> Done<()> {
        let (modifiers, def) = parse_key(key).ok_or_else(|| {
            failed(
                "bad_request",
                "Unknown key: use a name like Enter, Tab, Escape or ArrowDown.",
            )
        })?;
        let typing = !def.text.is_empty() && modifiers & 6 == 0;
        let down = if typing { "keyDown" } else { "rawKeyDown" };
        let mut event = json!({
            "type": down, "key": def.key, "code": def.code, "modifiers": modifiers,
            "windowsVirtualKeyCode": def.vk, "nativeVirtualKeyCode": def.vk,
        });
        if typing {
            event["text"] = json!(def.text);
            event["unmodifiedText"] = json!(def.text);
        }
        self.call("Input.dispatchKeyEvent", event.clone()).await?;
        event["type"] = json!("keyUp");
        if let Some(o) = event.as_object_mut() {
            o.remove("text");
            o.remove("unmodifiedText");
        }
        self.call("Input.dispatchKeyEvent", event).await?;
        Ok(())
    }

    pub async fn press(&mut self, key: &str) -> Done<Answer> {
        let (before, opened) = (self.loader().await, self.page_ids().await);
        self.press_key(key).await?;
        self.settle(&before).await;
        self.adopt_opened(&opened).await;
        Ok(Answer::default())
    }

    pub async fn scroll(&mut self, dy: f64) -> Done<Answer> {
        let dy = dy.clamp(-20_000.0, 20_000.0);
        let m = self.call("Page.getLayoutMetrics", json!({})).await?;
        let v = &m["cssVisualViewport"];
        let x = v["clientWidth"].as_f64().unwrap_or(launch::WIDTH as f64) / 2.0;
        let y = v["clientHeight"].as_f64().unwrap_or(launch::HEIGHT as f64) / 2.0;
        self.call(
            "Input.dispatchMouseEvent",
            json!({"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": dy}),
        )
        .await?;
        tokio::time::sleep(Duration::from_millis(300)).await;
        Ok(Answer::default())
    }

    pub async fn back(&mut self) -> Done<Answer> {
        let h = self.call("Page.getNavigationHistory", json!({})).await?;
        let at = h["currentIndex"].as_u64().unwrap_or(0) as usize;
        let entries = h["entries"].as_array().cloned().unwrap_or_default();
        if at == 0 || entries.len() <= at {
            return Err(failed("no_history", "There is no page to go back to."));
        }
        let before = self.loader().await;
        self.call(
            "Page.navigateToHistoryEntry",
            json!({"entryId": entries[at - 1]["id"]}),
        )
        .await?;
        self.settle(&before).await;
        Ok(Answer::default())
    }

    /// The visible part of the page as a JPEG of at most 1 MB, one image pixel per page pixel (so a person's click at
    /// x,y on it lands at x,y on the page). A field the runner filled with a login value is covered while it is taken.
    pub async fn screenshot(&mut self) -> Done<Answer> {
        self.capture(true).await
    }

    fn mask_script(&self) -> String {
        format!(
            "globalThis.__ticoValues = {}; {}",
            json!(self.typed.browser_values()),
            MASK
        )
    }

    /// Cross-origin fields keep their filled-field list in their own isolated world. Cover those worlds too;
    /// walking the top document alone cannot see an out-of-process frame's values.
    async fn cover_frames(
        &self,
        covered: &mut Vec<(String, i64)>,
        attached: &mut Vec<String>,
    ) -> Done<()> {
        let browser = self.browser();
        let targets = browser
            .call("Target.getTargets", json!({}), None)
            .await
            .map_err(page_error)?;
        let targets: Vec<String> = targets["targetInfos"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|t| t["type"] == "iframe")
            .map(|t| text_of(&t["targetId"]))
            .collect();
        let mut sessions = vec![self.session.clone()];
        for target in &targets {
            let session = match browser.attach(target).await {
                Ok(session) => session,
                Err(e) if gone_frame(&e) => continue,
                Err(e) => return Err(page_error(e)),
            };
            attached.push(session.clone());
            sessions.push(session);
        }
        for session in sessions {
            let tree = match browser
                .call("Page.getFrameTree", json!({}), Some(&session))
                .await
            {
                Ok(tree) => tree,
                Err(e) if gone_frame(&e) => continue,
                Err(e) => return Err(page_error(e)),
            };
            let mut frames = vec![];
            frame_ids(&tree["frameTree"], &mut frames);
            let root = text_of(&tree["frameTree"]["frame"]["id"]);
            for frame in frames {
                // A child in another process is covered through its own target above.
                if (session == self.session && frame == root)
                    || (targets.contains(&frame) && frame != root)
                {
                    continue;
                }
                let context = match create_world(&browser, &session, &frame).await {
                    Ok(context) => context,
                    Err(e) if gone_frame(&e.text) => continue,
                    Err(e) => return Err(e),
                };
                covered.push((session.clone(), context));
                if let Err(e) =
                    eval_in(&browser, &session, context, &self.mask_script(), true).await
                {
                    if !gone_frame(&e) {
                        return Err(page_error(e));
                    }
                }
            }
        }
        Ok(())
    }

    /// The screenshot, with filled fields covered (`mask`) or not (only a test compares the two).
    pub(super) async fn capture(&mut self, mask: bool) -> Done<Answer> {
        let m = self.call("Page.getLayoutMetrics", json!({})).await?;
        let v = &m["cssVisualViewport"];
        let (x, y) = (
            v["pageX"].as_f64().unwrap_or(0.0),
            v["pageY"].as_f64().unwrap_or(0.0),
        );
        let (w, h) = (
            v["clientWidth"].as_f64().unwrap_or(launch::WIDTH as f64),
            v["clientHeight"].as_f64().unwrap_or(launch::HEIGHT as f64),
        );
        // One script both reads the pixel ratio and covers the filled fields, so a frame with nothing filled costs no
        // more than it did. A page in the middle of navigating has a new document, where nothing is filled yet.
        let script = if mask {
            self.mask_script()
        } else {
            NO_MASK.into()
        };
        let prep = self.eval(&script, true).await?;
        let ratio = prep["value"]["ratio"]
            .as_f64()
            .filter(|r| *r > 0.0)
            .unwrap_or(1.0);
        let mut covered = vec![];
        let mut attached = vec![];
        let prep_frames = if mask {
            self.cover_frames(&mut covered, &mut attached).await
        } else {
            Ok(())
        };
        let shot = match prep_frames {
            Ok(()) => self.shoot((x, y, w, h), ratio).await,
            Err(e) => Err(e),
        };
        for (session, context) in covered {
            let _ = eval_in(&self.browser(), &session, context, UNMASK, true).await;
        }
        for session in attached {
            self.browser().detach(&session).await;
        }
        if prep["value"]["masked"].as_u64().unwrap_or(0) > 0 {
            let _ = self.eval(UNMASK, true).await;
        }
        shot
    }

    async fn shoot(&self, (x, y, w, h): (f64, f64, f64, f64), ratio: f64) -> Done<Answer> {
        for quality in [80, 60, 40, 25] {
            let r = self
                .call(
                    "Page.captureScreenshot",
                    json!({"format": "jpeg", "quality": quality, "fromSurface": true,
                           "clip": {"x": x, "y": y, "width": w, "height": h, "scale": 1.0 / ratio}}),
                )
                .await?;
            let data = text_of(&r["data"]);
            if data.len() / 4 * 3 <= SHOT_LIMIT {
                return Ok(Answer {
                    image: Some(data),
                    ..Answer::default()
                });
            }
        }
        Err(failed(
            "too_large",
            "The page's picture stays over 1 MB at any quality.",
        ))
    }

    /// The newest file this session downloaded, once it has finished.
    pub async fn download(&mut self) -> Done<Answer> {
        let dir = self.dir.join("downloads");
        let began = Instant::now();
        loop {
            let (newest, busy) = newest_file(&dir);
            if !busy || began.elapsed() > Duration::from_secs(20) {
                let Some((path, size)) = newest else {
                    return Err(failed(
                        if busy {
                            "browser_timeout"
                        } else {
                            "no_download"
                        },
                        if busy {
                            "The download has not finished yet: ask again."
                        } else {
                            "Nothing has downloaded in this session."
                        },
                    ));
                };
                if size > DOWNLOAD_LIMIT {
                    return Err(failed("too_large", "That file is over 20 MB."));
                }
                let bytes = std::fs::read(&path)
                    .map_err(|e| failed("no_download", format!("cannot read the file ({e})")))?;
                let name = path
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_else(|| "download".into());
                return Ok(Answer {
                    text: Some(name.clone()),
                    file: Some((name, bytes)),
                    ..Answer::default()
                });
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    }

    pub async fn upload(&mut self, r: &str, name: &str, data: &str) -> Done<Answer> {
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(data.trim())
            .map_err(|_| failed("bad_request", "The file is not base64."))?;
        if bytes.len() > UPLOAD_LIMIT {
            return Err(failed("too_large", "That file is over 10 MB."));
        }
        let object = self.element(r).await?;
        let takes = call_on(
            &self.browser(),
            &self.session,
            &object,
            "function () { return this.tagName === 'INPUT' && (this.type || '').toLowerCase() === 'file' && !this.disabled; }",
        )
        .await?;
        if takes != true {
            return Err(failed("no_field", "That element does not take a file."));
        }
        // Chrome reads the file when the page sends it, which can be well after this: it stays until the session
        // closes.
        let folder = self
            .dir
            .join("uploads")
            .join(uuid::Uuid::new_v4().simple().to_string());
        launch::private_dir(&folder)
            .map_err(|e| failed("upload_failed", format!("cannot keep the file ({e})")))?;
        let path = folder.join(safe_name(name));
        write_private(&path, &bytes)
            .map_err(|e| failed("upload_failed", format!("cannot keep the file ({e})")))?;
        let before = self.loader().await;
        self.call(
            "DOM.setFileInputFiles",
            json!({"files": [path.display().to_string()], "objectId": object}),
        )
        .await?;
        self.settle(&before).await;
        Ok(Answer::default())
    }

    // ---- a person's input, at page pixels of the newest frame ----

    pub async fn input_click(&mut self, x: f64, y: f64) -> Done<Answer> {
        if !(0.0..=10_000.0).contains(&x) || !(0.0..=10_000.0).contains(&y) {
            return Err(failed("bad_request", "That point is off the page."));
        }
        let (before, opened) = (self.loader().await, self.page_ids().await);
        self.mouse_click(x, y).await?;
        self.settle(&before).await;
        self.adopt_opened(&opened).await;
        Ok(Answer::default())
    }

    pub async fn input_type(&mut self, text: &str) -> Done<Answer> {
        if text.chars().count() > TEXT_LIMIT {
            return Err(failed("bad_request", "That text is too long."));
        }
        if !text.is_empty() {
            self.call("Input.insertText", json!({"text": text})).await?;
        }
        Ok(Answer::default())
    }
}

fn gone_frame(error: &str) -> bool {
    let error = error.to_ascii_lowercase();
    [
        "no frame",
        "no target",
        "target closed",
        "session closed",
        "frame was detached",
        "frame with the given id was not found",
        "session with given id",
        "cannot find context",
        "execution context was destroyed",
    ]
    .iter()
    .any(|needle| error.contains(needle))
}

fn frame_ids(tree: &Value, out: &mut Vec<String>) {
    if let Some(id) = tree["frame"]["id"].as_str() {
        out.push(id.to_string());
    }
    for child in tree["childFrames"].as_array().into_iter().flatten() {
        frame_ids(child, out);
    }
}

/// `e12` (or `12`) as 12.
pub fn ref_index(r: &str) -> Option<usize> {
    let r = r.trim();
    let digits = r.strip_prefix(['e', 'E']).unwrap_or(r);
    digits.parse::<usize>().ok().filter(|n| *n >= 1)
}

/// The middle of the first drawn quad.
fn centre(quads: &Value) -> Option<(f64, f64)> {
    quads.as_array()?.iter().find_map(|q| {
        let p: Vec<f64> = q.as_array()?.iter().filter_map(|n| n.as_f64()).collect();
        if p.len() != 8 {
            return None;
        }
        let x = (p[0] + p[2] + p[4] + p[6]) / 4.0;
        let y = (p[1] + p[3] + p[5] + p[7]) / 4.0;
        let wide = (p[0] - p[2]).abs().max((p[0] - p[4]).abs());
        let tall = (p[1] - p[5]).abs().max((p[1] - p[3]).abs());
        (wide >= 1.0 && tall >= 1.0).then_some((x, y))
    })
}

/// The newest finished file in `dir`, and whether a download is still running there.
fn newest_file(dir: &Path) -> (Option<(PathBuf, u64)>, bool) {
    let mut newest: Option<(std::time::SystemTime, PathBuf, u64)> = None;
    let mut busy = false;
    for entry in std::fs::read_dir(dir).into_iter().flatten().flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if name.ends_with(".crdownload") {
            busy = true;
            continue;
        }
        if name.starts_with('.') {
            continue;
        }
        let Ok(meta) = entry.metadata() else { continue };
        if !meta.is_file() {
            continue;
        }
        let when = meta.modified().unwrap_or(std::time::UNIX_EPOCH);
        if newest.as_ref().is_none_or(|(t, _, _)| when > *t) {
            newest = Some((when, entry.path(), meta.len()));
        }
    }
    (newest.map(|(_, p, n)| (p, n)), busy)
}

/// A file name safe to write in a folder of its own: no folders, no leading dot, at most 120 characters.
pub fn safe_name(name: &str) -> String {
    let base = name.rsplit(['/', '\\']).next().unwrap_or("");
    let clean: String = base
        .chars()
        .filter(|c| !c.is_control())
        .collect::<String>()
        .trim()
        .trim_start_matches('.')
        .chars()
        .take(120)
        .collect();
    if clean.is_empty() {
        "upload".into()
    } else {
        clean
    }
}

fn write_private(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)?;
    f.write_all(bytes)
}

/// Before a screenshot: each filled field still on the page whose value would show (it is no longer a password
/// field) is drawn as dots until UNMASK. Its style attribute is kept to put back as it was.
const MASK: &str = r#"(async () => {
  const list = (globalThis.__ticoFilled || []).filter((r) => r.deref());
  const seen = new Set(list.map((r) => r.deref()));
  const values = globalThis.__ticoValues || [];
  const find = (root) => {
    for (const e of root.querySelectorAll('*')) {
      if (e.shadowRoot) find(e.shadowRoot);
      if (!seen.has(e) && typeof e.value === 'string' && e.value && values.some((v) => v && e.value.includes(v))) {
        seen.add(e);
        list.push(new WeakRef(e));
      }
    }
  };
  find(document);
  globalThis.__ticoFilled = list;
  const kept = [];
  for (const r of list) {
    const e = r.deref();
    if (!e || !e.isConnected) continue;
    if (e.tagName === 'INPUT' && (e.type || '').toLowerCase() === 'password') continue;
    try {
      kept.push([e, e.getAttribute('style')]);
      e.style.setProperty('-webkit-text-security', 'disc', 'important');
    } catch (_) {}
  }
  globalThis.__ticoMasked = kept;
  if (kept.length) {
    // Drawn once with the dots before the picture is taken.
    await new Promise((done) => {
      requestAnimationFrame(() => requestAnimationFrame(done));
      setTimeout(done, 150);
    });
  }
  return { ratio: devicePixelRatio, masked: kept.length };
})()"#;

const NO_MASK: &str = "({ ratio: devicePixelRatio, masked: 0 })";

/// After a screenshot: the covered fields' style attributes as they were.
const UNMASK: &str = r#"(() => {
  for (const [e, style] of globalThis.__ticoMasked || []) {
    try {
      // Read first: Chrome writes a style changed through `e.style` into the attribute only when it is read, and
      // one still pending would come back as `style=""` after the attribute is removed.
      e.getAttribute('style');
      if (style === null) e.removeAttribute('style');
      else e.setAttribute('style', style);
    } catch (_) {}
  }
  globalThis.__ticoMasked = [];
  return true;
})()"#;

/// Called on an element: focus it if it takes text, and select what it holds.
const FOCUS: &str = r#"function () {
  const e = this;
  const t = (e.type || '').toLowerCase();
  const text = e.tagName === 'INPUT'
    ? !['button','submit','checkbox','radio','file','hidden','image','reset','range','color'].includes(t)
    : (e.tagName === 'TEXTAREA' || e.isContentEditable);
  if (!text || e.disabled || e.readOnly) return 'no_field';
  e.focus();
  if (typeof e.select === 'function') { try { e.select(); } catch (_) {} }
  else if (e.isContentEditable) {
    const s = e.ownerDocument.getSelection();
    const r = e.ownerDocument.createRange();
    r.selectNodeContents(e);
    s.removeAllRanges();
    s.addRange(r);
  }
  return 'ok';
}"#;

pub struct Key {
    key: String,
    code: String,
    vk: u32,
    text: String,
}

/// A key as a person names it (`Enter`, `Tab`, `ArrowDown`, `a`, `Shift+Tab`, `Meta+a`): its modifier bits (Alt 1,
/// Control 2, Meta 4, Shift 8) and the key.
pub fn parse_key(name: &str) -> Option<(u32, Key)> {
    let name = name.trim();
    let mut parts: Vec<&str> = name.split('+').collect();
    // A plus sign itself: "+" or "Shift++".
    let last = if name.ends_with("++") || name == "+" {
        parts.retain(|p| !p.is_empty());
        "+"
    } else {
        parts.pop()?
    };
    let mut modifiers = 0;
    for m in parts {
        modifiers |= match m.trim().to_ascii_lowercase().as_str() {
            "alt" | "option" => 1,
            "control" | "ctrl" => 2,
            "meta" | "cmd" | "command" => 4,
            "shift" => 8,
            _ => return None,
        };
    }
    let named = |key: &str, code: &str, vk: u32, text: &str| Key {
        key: key.into(),
        code: code.into(),
        vk,
        text: text.into(),
    };
    let key = match last.trim().to_ascii_lowercase().as_str() {
        "enter" | "return" => named("Enter", "Enter", 13, "\r"),
        "tab" => named("Tab", "Tab", 9, ""),
        "escape" | "esc" => named("Escape", "Escape", 27, ""),
        "backspace" => named("Backspace", "Backspace", 8, ""),
        "delete" | "del" => named("Delete", "Delete", 46, ""),
        "arrowup" | "up" => named("ArrowUp", "ArrowUp", 38, ""),
        "arrowdown" | "down" => named("ArrowDown", "ArrowDown", 40, ""),
        "arrowleft" | "left" => named("ArrowLeft", "ArrowLeft", 37, ""),
        "arrowright" | "right" => named("ArrowRight", "ArrowRight", 39, ""),
        "home" => named("Home", "Home", 36, ""),
        "end" => named("End", "End", 35, ""),
        "pageup" => named("PageUp", "PageUp", 33, ""),
        "pagedown" => named("PageDown", "PageDown", 34, ""),
        "space" | " " => named(" ", "Space", 32, " "),
        _ => {
            let mut chars = last.chars();
            let c = chars.next()?;
            if chars.next().is_some() {
                return None;
            }
            let (code, vk) = if c.is_ascii_alphabetic() {
                (
                    format!("Key{}", c.to_ascii_uppercase()),
                    c.to_ascii_uppercase() as u32,
                )
            } else if c.is_ascii_digit() {
                (format!("Digit{c}"), c as u32)
            } else {
                (String::new(), 0)
            };
            let shown = if modifiers & 8 != 0 {
                c.to_uppercase().to_string()
            } else {
                c.to_string()
            };
            Key {
                key: shown.clone(),
                code,
                vk,
                text: shown,
            }
        }
    };
    Some((modifiers, key))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refs_keys_and_names_are_read_the_way_a_bot_writes_them() {
        assert_eq!(ref_index("e12"), Some(12));
        assert_eq!(ref_index(" 7 "), Some(7));
        assert_eq!(ref_index("e0"), None);
        assert_eq!(ref_index("button"), None);

        let (m, k) = parse_key("Enter").unwrap();
        assert_eq!(
            (m, k.key.as_str(), k.vk, k.text.as_str()),
            (0, "Enter", 13, "\r")
        );
        let (m, k) = parse_key("Shift+Tab").unwrap();
        assert_eq!((m, k.key.as_str()), (8, "Tab"));
        let (m, k) = parse_key("Meta+a").unwrap();
        assert_eq!((m, k.code.as_str(), k.vk), (4, "KeyA", 65));
        assert_eq!(parse_key("+").unwrap().1.key, "+");
        assert!(parse_key("Hyper+x").is_none());
        assert!(parse_key("Enterr").is_none());

        assert_eq!(safe_name("../../etc/passwd"), "passwd");
        assert_eq!(safe_name(".bashrc"), "bashrc");
        assert_eq!(safe_name("C:\\a\\Invoice 3.pdf"), "Invoice 3.pdf");
        assert_eq!(safe_name(""), "upload");

        assert_eq!(
            centre(&json!([[10, 10, 30, 10, 30, 20, 10, 20]])),
            Some((20.0, 15.0))
        );
        assert_eq!(centre(&json!([[5, 5, 5, 5, 5, 5, 5, 5]])), None);
    }

    #[test]
    fn the_newest_finished_download_is_found_and_a_running_one_waited_for() {
        let dir = std::env::temp_dir().join(format!("tico-dl-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        assert!(newest_file(&dir).0.is_none());
        std::fs::write(dir.join("old.csv"), "a").unwrap();
        std::thread::sleep(Duration::from_millis(20));
        std::fs::write(dir.join("new.csv"), "ab").unwrap();
        std::fs::write(dir.join("next.pdf.crdownload"), "x").unwrap();
        let (newest, busy) = newest_file(&dir);
        assert!(busy);
        let (path, size) = newest.unwrap();
        assert_eq!(
            (path.file_name().unwrap().to_str(), size),
            (Some("new.csv"), 2)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
