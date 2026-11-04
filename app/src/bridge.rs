//! The page's side of the native bridge: the script that gives the page `ticoNative`, the commands it calls,
//! and the check that only the open org's own page may use them.
use serde_json::Value;
use tauri::webview::Webview;
use tauri::Manager;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogResult};
use tauri_plugin_opener::OpenerExt;

use crate::orgs;
use crate::windows::{open_meeting_window, org_of, Home};
use crate::{add_org, applog, emit_orgs, log_dir, org_count, popup_orgs, update_now, visual_origin, App, AppState, LOG_LINES};

/// The page's side of the bridge: `window.ticoNative(name).postMessage(body)`, which
/// `nativeHandler(name)` in ui/app/native.js looks for; `/TicoHub/` in the user agent marks the
/// page native. (WKWebView's own `window.webkit.messageHandlers` cannot be replaced, so the
/// shell has its own name.)
const BRIDGE_SCRIPT: &str = r#"
(() => {
  if (!window.__TAURI__ || !window.__TAURI__.core) return;
  const invoke = window.__TAURI__.core.invoke;
  window.ticoNative = name => ({postMessage: body => invoke('bridge', {name, body: body === undefined ? null : body})});
  // What this build's bridge answers beyond the first three, so a page never posts into a
  // shell too old to hear it.
  window.ticoNativeFeatures = ['openWindow', 'checkForUpdates', 'showLog', 'appLog', 'visualOrigin', 'visualFiles', 'orgInfo', 'bootTiming'];
  // The shell's version and the end of its log ({version, lines}), for Support diagnostics.
  window.ticoNativeLog = () => invoke('app_log');
})();
"#;

/// What only a build that keeps a list of orgs answers: the org menu and adding an org. Its own constant, so that
/// BRIDGE_SCRIPT is the same text in every build.
const ORG_SCRIPT: &str = r#"
(() => {
  if (!window.ticoNativeFeatures) return;
  window.ticoNativeFeatures.push('multiOrg', 'showOrgMenu', 'addOrg');
})();
"#;

/// What a page captures, as it says (`capture`: {live, display}) whatever the page's version: whether it is sending a
/// microphone, camera or screen now, and whether it ever shared its screen. Only a hint, which a page can get around:
/// the keep-or-close rule at a switch asks WebKit itself on macOS (windows::native_capture), and closes a page that
/// ever shared its screen, which WebKit cannot tell; elsewhere a page that ever captured anything is closed.
const CAPTURE_SCRIPT: &str = r#"
(() => {
  const Media = window.MediaDevices, Track = window.MediaStreamTrack;
  if (!Media || !Track || !window.__TAURI__ || !window.__TAURI__.core) return;
  const invoke = window.__TAURI__.core.invoke;
  const live = new Set();
  let said = '', display = false;
  const say = () => {
    for (const track of live) if (track.readyState !== 'live') live.delete(track);
    const now = JSON.stringify({live: live.size > 0, display});
    if (now === said) return;
    said = now;
    invoke('bridge', {name: 'capture', body: JSON.parse(now)}).catch(() => {});
  };
  const stop = Track.prototype.stop, clone = Track.prototype.clone;
  const watch = track => { live.add(track); track.addEventListener('ended', say); return track; };
  Track.prototype.stop = function () { stop.call(this); if (live.has(this)) say(); };
  Track.prototype.clone = function () { const copy = clone.call(this); if (live.has(this)) watch(copy); return copy; };
  for (const name of ['getUserMedia', 'getDisplayMedia']) {
    const ask = Media.prototype[name];
    if (typeof ask !== 'function') continue;
    const wrapped = function (...args) {
      if (name === 'getDisplayMedia') { display = true; say(); }
      return ask.apply(this, args).then(stream => { stream.getTracks().forEach(watch); say(); return stream; });
    };
    Media.prototype[name] = wrapped;
    if (navigator.mediaDevices && Object.prototype.hasOwnProperty.call(navigator.mediaDevices, name)) navigator.mediaDevices[name] = wrapped;
  }
})();
"#;

/// The page's `alert` and `confirm`, as the dialog plugin makes them (`alert` returns at once, `confirm` returns a
/// promise of true for OK), but through `page_dialog`, which shows them only for the page on screen. Org pages have
/// no dialog plugin permission; this runs after the plugin's own script, so it is the one that stays.
const DIALOG_SCRIPT: &str = r#"
(() => {
  if (!window.__TAURI__ || !window.__TAURI__.core) return;
  const invoke = window.__TAURI__.core.invoke;
  const text = message => message === undefined ? '' : String(message);
  window.alert = message => { invoke('page_dialog', {confirm: false, message: text(message)}).catch(() => {}); };
  window.confirm = message => invoke('page_dialog', {confirm: true, message: text(message)}).catch(() => false);
})();
"#;

/// What only a Mac build answers: whether Tico Recorder is installed (`hasRecorder`) and opening it on the page's org
/// (`openRecorder`).
const RECORDER_SCRIPT: &str = r#"
(() => {
  if (!window.ticoNativeFeatures) return;
  window.ticoNativeFeatures.push('openRecorder');
})();
"#;

/// The bridge plus this shell's version, which Settings shows beside the server's.
pub(crate) fn bridge_script(app: &App) -> String {
    script_for(&app.package_info().version.to_string(), orgs::multi_org_build(), cfg!(target_os = "macos"))
}

/// The page script for a build: the bridge, the org features when this build keeps a list of orgs, the Recorder's on
/// a Mac, and the version.
fn script_for(version: &str, several_orgs: bool, recorder: bool) -> String {
    let version = serde_json::to_string(version).unwrap_or_else(|_| "\"\"".into());
    let extra = if several_orgs { ORG_SCRIPT } else { "" };
    let recorder = if recorder { RECORDER_SCRIPT } else { "" };
    format!("{BRIDGE_SCRIPT}{DIALOG_SCRIPT}{CAPTURE_SCRIPT}{extra}{recorder}window.ticoNativeVersion = {version};\n")
}

// ----------------------------------------------------------------------------- bridge
/// Generic builds accept any server; only the open org's own page may call the shell.
fn from_server(app: &App, webview: &Webview) -> bool {
    let document = webview.url().ok();
    let state = app.state::<AppState>();
    let list = state.orgs.lock().unwrap();
    let open = list.active().map(|org| org.id.as_str());
    let connection = state.connection.lock().unwrap();
    let caller = org_of(webview.label()).unwrap_or_default();
    server_document(connection.as_ref().map(|c| &c.hub), caller, open, document.as_ref())
}

/// The page is the open org's page: in that org's own webview (`caller`, the org its label names), at that org's
/// origin. Not the rail, not an org that is no longer open, and not a hosted file.
fn server_document(hub: Option<&url::Url>, caller: &str, open: Option<&str>, document: Option<&url::Url>) -> bool {
    !caller.is_empty() && open == Some(caller) && hub.zip(document).is_some_and(|(hub, document)| {
        matches!(document.scheme(), "http" | "https") && document.username().is_empty()
            && document.password().is_none() && document.origin() == hub.origin()
    })
}

/// What a page that is not on screen (a prefetch, or one kept since a switch away) may still post: about itself
/// only, so that it is ready when shown, its own `bootTiming` (one checked log line, so a prefetched open is timed
/// too), and whether it is capturing (`capture`). Everything else (windows, menus, updates, opening the log) is the
/// open page's alone.
fn own_page_call(name: &str) -> bool {
    matches!(name, "orgInfo" | "visualOrigin" | "multiOrg" | "bootTiming" | "capture" | "hasRecorder")
}

/// The org whose page this is, when the page is at that org's own origin: any org page, open or not.
fn page_org(app: &App, webview: &Webview) -> Option<String> {
    let id = org_of(webview.label())?;
    let hub = app.state::<AppState>().orgs.lock().unwrap().get(id)?.hub();
    let document = webview.url().ok();
    server_document(hub.as_ref(), id, Some(id), document.as_ref()).then(|| id.to_string())
}

/// `{origin}`, or `{origin, files}` from a page that saw `visualFiles` in this shell's features: Tico file hosting's
/// address, which shows pages and SVG when the server has no visual origin. Anything else clears.
fn visual_from(hub: Option<&url::Url>, body: Option<&Value>) -> Option<visual_origin::VisualOrigin> {
    fn text(value: Option<&Value>) -> Option<&str> { value.map_or(Some(""), Value::as_str) }
    let raw = body.and_then(Value::as_object)
        .filter(|object| object.contains_key("origin") && object.keys().all(|key| key == "origin" || key == "files"))
        .and_then(|object| Some((text(object.get("origin"))?, text(object.get("files"))?)));
    hub.zip(raw).and_then(|(hub, (raw, files))| visual_origin::VisualOrigin::with_files(hub, raw, files))
}

fn set_visual_origin(state: &AppState, label: &str, document: Option<&url::Url>, body: Option<&Value>) -> Result<bool, String> {
    let list = state.orgs.lock().unwrap();
    let caller = org_of(label).unwrap_or_default();
    let open = list.active().map(|org| org.id.as_str());
    if open != Some(caller) {
        // A hidden page, the prefetched one or one kept since a switch away: its allowance waits apart from the open
        // page's until it is shown.
        let prefetched = state.prefetch.lock().unwrap().org.as_deref() == Some(caller);
        let hub = list.get(caller).and_then(|org| org.hub());
        if hub.is_none() || !server_document(hub.as_ref(), caller, Some(caller), document) {
            return Err("Only the selected server's page may configure visuals.".into());
        }
        let visual = visual_from(hub.as_ref(), body);
        let set = visual.is_some();
        if prefetched {
            *state.parked_visual.lock().unwrap() = visual;
        } else if !state.kept.lock().unwrap().set_visual(caller, visual) {
            return Err("Only the selected server's page may configure visuals.".into());
        }
        return Ok(set);
    }
    let connection = state.connection.lock().unwrap();
    let hub = connection.as_ref().map(|c| &c.hub);
    if !server_document(hub, caller, open, document) {
        return Err("Only the selected server's page may configure visuals.".into());
    }
    let mut visual = state.visual_origin.lock().unwrap();
    *visual = visual_from(hub, body);
    Ok(visual.is_some())
}

/// The reply to a bridge call that has one. `multiOrg` says whether this app has 2+ orgs, which is all the page
/// learns about the others. Every other call replies null.
pub(crate) fn bridge_reply(name: &str, count: usize) -> Value {
    if name == "multiOrg" { Value::Bool(count >= 2) } else { Value::Null }
}

/// What the page posts: `openWindow` ({meeting: id}), `orgInfo`, `showOrgMenu`, `addOrg` and the rest. Older pages
/// also post `windowMode`, which is ignored now that the window has one mode.
#[tauri::command]
pub(crate) fn bridge(app: App, webview: Webview, name: String, body: Option<Value>) -> Result<Value, String> {
    let caller = if from_server(&app, &webview) || own_page_call(&name) { page_org(&app, &webview) } else { None };
    let Some(caller) = caller else { return Err("Only the selected server's page may call the app.".into()) };
    // The page's first call: the app on it is up (for the switch timing in the log).
    crate::timing::ready(&app, &caller, &name);
    match name.as_str() {
        "visualOrigin" => return set_visual_origin(&app.state::<AppState>(), webview.label(), webview.url().ok().as_ref(), body.as_ref()).map(Value::Bool),
        "openWindow" => {
            // `recording` is what an older page called it.
            let id = body.as_ref().and_then(|b| b.get("meeting").or_else(|| b.get("recording"))).and_then(Value::as_str);
            // The caller's own org, from its label: a switch since the check must not open it on another org.
            let caller = org_of(webview.label()).and_then(|org| app.state::<AppState>().orgs.lock().unwrap().get(org).cloned());
            if let (Some(id), Some(home)) = (id, caller.and_then(|org| Home::of(&org))) {
                open_meeting_window(&app, &home, id);
            }
        }
        // The page's "App update" bar: check the feed now instead of at the next six-hourly check.
        "checkForUpdates" => update_now(&app),
        // Help's "Show the app's log": the log file, selected in Finder (or the platform's file manager).
        "showLog" => show_log(&app),
        // The open org's name and email, as its page reports them: kept for the rail and the org menu.
        "orgInfo" => org_info(&app, webview.label(), body.as_ref())?,
        // How long the page took to open (ui/app/boot-timing.js), once per load: one line in the app's log, with how
        // long it has been since this shell built the page's webview (its first report only; a reload has none).
        "bootTiming" => {
            let since = app.state::<AppState>().page_opened.lock().unwrap().remove(webview.label()).map(|at| at.elapsed().as_millis());
            log::info!("{}", boot_line(body.as_ref(), since)?);
        }
        // The page started or stopped sending a microphone, camera or screen (CAPTURE_SCRIPT).
        "capture" => {
            let flag = |name: &str| body.as_ref().and_then(|b| b.get(name)).and_then(Value::as_bool).unwrap_or(false);
            crate::windows::note_capture(&mut app.state::<AppState>().capture.lock().unwrap(), &caller, flag("live"), flag("display"));
        }
        // The org menu, at the page's point. It lists the shell's own orgs; the page is told nothing back.
        "showOrgMenu" => if orgs::multi_org_build() { popup_orgs(&app) },
        "addOrg" => {
            if orgs::multi_org_build() { add_org(&app); }
        }
        // Whether Record (Tico Recorder) can be offered instead of its download.
        "hasRecorder" => return Ok(Value::Bool(crate::recorder::installed())),
        // Record: the Recorder opens on the caller's own org, from its label, never an address the page names.
        "openRecorder" => {
            let org = org_of(webview.label()).and_then(|org| app.state::<AppState>().orgs.lock().unwrap().get(org).cloned());
            if let Some(hub) = org.and_then(|org| org.hub()) {
                crate::recorder::open_recorder(&hub);
            }
        }
        _ => {}
    }
    Ok(bridge_reply(&name, org_count(&app)))
}

/// The page dialogs open now. A switch (and a removal) waits until none is: the page that asked keeps the screen
/// until its dialog is answered, so a dialog never shows over another org.
pub(crate) static DIALOGS: DialogGate = DialogGate::new();

pub(crate) struct DialogGate {
    open: std::sync::Mutex<usize>,
    answered: std::sync::Condvar,
}

/// One open dialog; it counts as answered when dropped.
pub(crate) struct OpenDialog(&'static DialogGate);

impl DialogGate {
    const fn new() -> Self {
        DialogGate { open: std::sync::Mutex::new(0), answered: std::sync::Condvar::new() }
    }

    pub(crate) fn opened(&'static self) -> OpenDialog {
        *self.open.lock().unwrap() += 1;
        OpenDialog(self)
    }

    /// Waits until every open dialog is answered.
    pub(crate) fn wait_closed(&self) {
        let mut open = self.open.lock().unwrap();
        while *open > 0 {
            open = self.answered.wait(open).unwrap();
        }
    }
}

impl Drop for OpenDialog {
    fn drop(&mut self) {
        let mut open = self.0.open.lock().unwrap();
        *open = open.saturating_sub(1);
        self.0.answered.notify_all();
    }
}

/// The page's `alert` (`confirm` false) or `confirm`: a dialog over the main window, for the open org's page while
/// it is on screen only, so a page built hidden for a prefetch never puts one over another org. Answers whether OK
/// was chosen. The check and the dialog's opening happen under `changing`, which a switch holds: a switch under way
/// finishes first (and the page, no longer on screen, is refused), and a switch after it waits for the answer
/// (`DIALOGS`). Async, so it waits off the main thread, as the dialog plugin's own command does.
#[tauri::command]
pub(crate) async fn page_dialog(app: App, webview: Webview, confirm: bool, message: String) -> Result<bool, String> {
    let open = {
        let state = app.state::<AppState>();
        let _changing = state.changing.lock().unwrap();
        let shown = org_of(webview.label()).is_some_and(|id| crate::windows::page_on_screen(&app, id));
        (shown && from_server(&app, &webview)).then(|| DIALOGS.opened())
    };
    let Some(open) = open else { return Err("Only the page on screen may show a dialog.".into()) };
    let mut dialog = app.dialog().message(message).parent(&webview.window());
    if confirm {
        dialog = dialog.buttons(MessageDialogButtons::OkCancel);
    }
    let (answer, answered) = tokio::sync::oneshot::channel();
    dialog.show_with_result(move |result| {
        drop(open);
        let _ = answer.send(result);
    });
    Ok(confirm && matches!(answered.await, Ok(MessageDialogResult::Ok)))
}

/// The open org's name and email from its page, kept for that org alone. Missing fields are blank.
fn org_info(app: &App, label: &str, body: Option<&Value>) -> Result<(), String> {
    let object = body.and_then(Value::as_object).ok_or("The org details need a name and an email.")?;
    let text = |key: &str| match object.get(key) {
        None | Some(Value::Null) => Ok(String::new()),
        Some(Value::String(value)) => Ok(value.clone()),
        Some(_) => Err("The org details must be text.".to_string()),
    };
    let (name, email) = (text("name")?, text("email")?);
    let state = app.state::<AppState>();
    // The caller's own org, from its webview's label: a switch between the check and here must not move the
    // details to the org that opened next.
    let id = {
        let mut list = state.orgs.lock().unwrap();
        let id = org_of(label).filter(|id| list.get(id).is_some()).map(str::to_string);
        if let Some(id) = &id {
            list.set_details(id, &name, &email);
        }
        id
    };
    if id.is_some() {
        emit_orgs(app);
    }
    Ok(())
}

/// The most a `bootTiming` report may be, as JSON: its marks and at most BOOT_READS reads come to well under this.
const BOOT_TIMING_MAX: usize = 4096;
const BOOT_READS: usize = 16;
/// The marks a report may carry, in the order an open passes them (ui/app/boot-timing.js BOOT_MARKS).
const BOOT_MARKS: [&str; 8] = ["head", "bundle-start", "boot", "paint-cached", "roster", "route", "native-ack", "paint-fresh"];
/// No time in a report is longer than ten minutes.
const BOOT_MS_MAX: u64 = 600_000;

/// A read's path as the page writes it (ui/app/boot-timing.js bootPath): `/api/`, then `v2/` if there, the resource's
/// name, and `:id` for every part after it, so the log can never name a bot, task or person. `/api/v2/messaging/bots/ops`
/// is refused; the page sends `/api/v2/messaging/:id/:id`.
fn boot_path_ok(path: &str) -> bool {
    let Some(rest) = path.strip_prefix("/api/") else { return false };
    let mut parts = rest.split('/').peekable();
    if parts.peek() == Some(&"v2") { parts.next(); }
    let name = |p: &str| p == ":id" || (p.len() <= 24 && p.starts_with(|c: char| c.is_ascii_lowercase())
        && p.chars().all(|c| c.is_ascii_lowercase() || c == '-'));
    path.len() <= 96 && parts.next().is_some_and(name) && parts.all(|p| p == ":id")
}

/// The page's `bootTiming` report as one log line. Only its known fields reach the log: whole milliseconds of at most
/// ten minutes, the page as `#/word`, and API paths in boot_path_ok's shape. Anything else refuses the whole report, so
/// a page can neither add lines to the log nor fill it.
pub(crate) fn boot_line(body: Option<&Value>, since_built: Option<u128>) -> Result<String, String> {
    let refuse = || "The open's timing is not in the expected form.".to_string();
    let body = body.and_then(Value::as_object).ok_or_else(refuse)?;
    if Value::Object(body.clone()).to_string().len() > BOOT_TIMING_MAX {
        return Err("The open's timing is too long.".into());
    }
    if !body.keys().all(|key| ["route", "settled", "marks", "html", "bundle", "server"].contains(&key.as_str())) {
        return Err(refuse());
    }
    let ms = |value: &Value| value.as_u64().filter(|n| *n <= BOOT_MS_MAX).ok_or_else(refuse);
    let maybe_ms = |value: Option<&Value>| match value {
        None | Some(Value::Null) => Ok(None),
        Some(value) => ms(value).map(Some),
    };
    let route = match body.get("route") { None | Some(Value::Null) => "", Some(value) => value.as_str().ok_or_else(refuse)? };
    let word = |text: &str| text.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-');
    if route.len() > 34 || !(route.is_empty() || route.strip_prefix("#/").is_some_and(word)) {
        return Err(refuse());
    }
    let settled = match body.get("settled") { None | Some(Value::Null) => false, Some(value) => value.as_bool().ok_or_else(refuse)? };
    let mut line = format!("org page open: {}, {}", if route.is_empty() { "no page" } else { route },
                           if settled { "settled" } else { "not settled" });
    let mut times = Vec::new();
    if let Some(html) = maybe_ms(body.get("html"))? { times.push(format!("html {html}")); }
    let marks = match body.get("marks") {
        None | Some(Value::Null) => None,
        Some(value) => Some(value.as_object().ok_or_else(refuse)?),
    };
    if let Some(marks) = marks {
        if !marks.keys().all(|key| BOOT_MARKS.contains(&key.as_str())) { return Err(refuse()); }
        for name in BOOT_MARKS {
            if let Some(at) = marks.get(name) { times.push(format!("{name} {}", ms(at)?)); }
        }
    }
    if !times.is_empty() { line += &format!("; {} ms", times.join(", ")); }
    match body.get("bundle") {
        None | Some(Value::Null) => {}
        Some(bundle) => {
            let bundle = bundle.as_object().filter(|b| b.keys().all(|key| key == "bytes" || key == "ms")).ok_or_else(refuse)?;
            let bytes = bundle.get("bytes").and_then(Value::as_u64).filter(|n| *n <= 1 << 30).ok_or_else(refuse)?;
            let took = ms(bundle.get("ms").ok_or_else(refuse)?)?;
            line += &if bytes == 0 { format!("; bundle cached ({took} ms)") } else { format!("; bundle {} KB in {took} ms", bytes.div_ceil(1024)) };
        }
    }
    let reads = match body.get("server") {
        None | Some(Value::Null) => Vec::new(),
        Some(value) => value.as_array().filter(|reads| reads.len() <= BOOT_READS).ok_or_else(refuse)?.iter().map(|read| -> Result<String, String> {
            let read = read.as_object().filter(|r| r.keys().all(|key| ["path", "ms", "app", "auth"].contains(&key.as_str())))
                .ok_or_else(refuse)?;
            let path = read.get("path").and_then(Value::as_str).filter(|path| boot_path_ok(path)).ok_or_else(refuse)?;
            let mut text = format!("{path} {} ms", ms(read.get("ms").ok_or_else(refuse)?)?);
            let server: Vec<String> = [("app", maybe_ms(read.get("app"))?), ("auth", maybe_ms(read.get("auth"))?)].into_iter()
                .filter_map(|(name, at)| at.map(|at| format!("{name} {at}"))).collect();
            if !server.is_empty() { text += &format!(" ({})", server.join(", ")); }
            Ok(text)
        }).collect::<Result<Vec<_>, String>>()?,
    };
    if !reads.is_empty() { line += &format!("; reads: {}", reads.join(", ")); }
    if let Some(since) = since_built { line += &format!("; {since} ms since the webview was built"); }
    Ok(line)
}

fn show_log(app: &App) {
    let Some(dir) = log_dir(app) else { return };
    let file = dir.join(applog::FILE_NAME);
    let shown = if file.exists() { app.opener().reveal_item_in_dir(&file) } else { app.opener().open_path(dir.to_string_lossy(), None::<&str>) };
    if let Err(e) = shown { log::warn!("could not show the log: {e}"); }
}

/// This shell's version and the end of its log, which the page adds to a Support diagnostics bundle.
#[tauri::command]
pub(crate) fn app_log(app: App, webview: Webview) -> Result<Value, String> {
    if !from_server(&app, &webview) { return Err("Only the selected server's page may read the app's log.".into()); }
    let lines = log_dir(&app).map(|dir| applog::tail(&dir.join(applog::FILE_NAME), LOG_LINES)).unwrap_or_default();
    Ok(serde_json::json!({"version": app.package_info().version.to_string(), "lines": lines}))
}

#[cfg(test)]
mod boot_timing_tests {
    use super::*;

    #[test]
    fn a_report_is_one_line_of_its_known_fields() {
        let body = serde_json::json!({
            "route": "#/home", "settled": true, "html": 182,
            "marks": {"head": 190, "bundle-start": 236, "boot": 402, "roster": 1180, "route": 1194, "native-ack": 1201, "paint-fresh": 1690},
            "bundle": {"bytes": 0, "ms": 4},
            "server": [{"path": "/api/employees", "ms": 412, "app": 380, "auth": 2}, {"path": "/api/v2/places/:id", "ms": 300, "app": null, "auth": null}],
        });
        assert_eq!(boot_line(Some(&body), Some(2020)).unwrap(),
            "org page open: #/home, settled; html 182, head 190, bundle-start 236, boot 402, roster 1180, route 1194, \
             native-ack 1201, paint-fresh 1690 ms; bundle cached (4 ms); reads: /api/employees 412 ms (app 380, auth 2), \
             /api/v2/places/:id 300 ms; 2020 ms since the webview was built");
        let fetched = serde_json::json!({"route": "", "settled": false, "bundle": {"bytes": 1_300_000, "ms": 230}});
        assert_eq!(boot_line(Some(&fetched), None).unwrap(), "org page open: no page, not settled; bundle 1270 KB in 230 ms");
        let empty = serde_json::json!({});
        assert_eq!(boot_line(Some(&empty), None).unwrap(), "org page open: no page, not settled");
    }

    #[test]
    fn anything_else_refuses_the_whole_report() {
        let read = |path: &str| serde_json::json!({"server": [{"path": path, "ms": 1}]});
        let many: Vec<Value> = (0..17).map(|_| serde_json::json!({"path": "/api/me", "ms": 1})).collect();
        for body in [
            Value::Null, serde_json::json!("text"), serde_json::json!([]),
            serde_json::json!({"other": 1}),
            serde_json::json!({"route": "#/home\nforged line"}), serde_json::json!({"route": "#/Bot"}),
            serde_json::json!({"route": "home"}), serde_json::json!({"route": format!("#/{}", "a".repeat(40))}),
            serde_json::json!({"settled": "yes"}),
            serde_json::json!({"marks": {"made-up": 1}}), serde_json::json!({"marks": {"boot": -1}}),
            serde_json::json!({"marks": {"boot": 1.5}}), serde_json::json!({"marks": {"boot": 600_001}}),
            serde_json::json!({"html": "fast"}),
            serde_json::json!({"bundle": {"bytes": 1}}), serde_json::json!({"bundle": {"bytes": 1, "ms": 1, "url": "x"}}),
            read("/api/me\n"), read("/api/me?token=1"), read("/other"), read(&format!("/api/{}", "a".repeat(100))),
            read("/api/v2/messaging/bots/ops"), read("/api/v2/places/p_4f2a"), read("/api/tasks/t1/files"), read("/api/Me"),
            read("/api/v2/"), read("/api/me/"), read("/api/v2/places/:id/"), read("/api/v2/places/:id/x"),
            serde_json::json!({"server": many}),
            serde_json::json!({"server": [{"path": "/api/me", "ms": 1, "body": "x"}]}),
        ] {
            assert!(boot_line(Some(&body), None).is_err(), "{body}");
        }
        assert!(boot_line(None, None).is_err());
        for path in ["/api/me", "/api/v2/needs-you", "/api/v2/messaging/:id/:id", "/api/:id"] {
            assert!(boot_line(Some(&read(path)), None).is_ok(), "{path}");
        }
        let long = serde_json::json!({"route": "#/home", "pad": "x".repeat(BOOT_TIMING_MAX)});
        assert_eq!(boot_line(Some(&long), None), Err("The open's timing is too long.".into()));
    }
}

#[cfg(test)]
mod visual_bridge_tests {
    use super::*;
    use crate::config::Config;
    use crate::orgs::{Document, Org, Orgs};
    use crate::select_connection;
    use crate::windows::{follow_window, session_exit, top_level_window};
    use std::sync::atomic::{AtomicBool, Ordering};

    const HUB: &str = "https://team.example.com/tico/";
    const ASSETS: &str = "https://team.files.example.com";
    const FILES: &str = "https://acme-files.ticofiles.com";
    const ORG: &str = "o_7f3a0001";
    const LABEL: &str = "org-o_7f3a0001";

    fn org(id: &str, url: &str, name: &str, email: &str) -> Org {
        Org { id: id.into(), url: url.into(), store: None, name: name.into(), email: email.into(), added_at: String::new(), sign_in: false, last_url: None }
    }

    /// One org, open, on its own page's label.
    fn state() -> AppState {
        let doc = Document { version: 1, active: Some(ORG.into()), orgs: vec![org(ORG, HUB, "", "")] };
        AppState::new(Orgs::new(doc, None), Some(Config::new(url::Url::parse(HUB).unwrap())))
    }

    #[test]
    fn visual_allowance_never_grants_bridge_or_log_authority() {
        let state = state();
        let hub = url::Url::parse(HUB).unwrap();
        let body = serde_json::json!({"origin": ASSETS});
        assert_eq!(set_visual_origin(&state, LABEL, Some(&hub), Some(&body)), Ok(true));
        let visual = url::Url::parse(&format!("{ASSETS}/visuals/payload.{}", "A".repeat(43))).unwrap();
        assert!(state.visual_origin.lock().unwrap().as_ref().unwrap().permits(Some(&hub), &visual));
        for document in [visual, url::Url::parse("https://team.example.com.evil.example/").unwrap(),
                         url::Url::parse("about:blank").unwrap(), url::Url::parse("data:text/html,hi").unwrap(),
                         url::Url::parse("blob:https://team.example.com/opaque").unwrap()] {
            // Both bridge and app_log use this guard, including after a visual becomes top-level.
            assert!(!server_document(Some(&hub), LABEL, Some(LABEL), Some(&document)), "{document}");
            assert!(set_visual_origin(&state, LABEL, Some(&document), Some(&body)).is_err());
        }
        assert!(server_document(Some(&hub), LABEL, Some(LABEL), Some(&hub)));
        assert!(!server_document(Some(&hub), LABEL, Some(LABEL), None));
        // The rail, and an org that is not the open one, never count as the open org's page.
        assert!(!server_document(Some(&hub), "rail", Some(LABEL), Some(&hub)));
        assert!(!server_document(Some(&hub), "org-o_other000", Some(LABEL), Some(&hub)));
        assert!(!server_document(Some(&hub), LABEL, None, Some(&hub)));
        assert!(set_visual_origin(&state, "rail", Some(&hub), Some(&body)).is_err());
        assert!(set_visual_origin(&state, "org-o_other000", Some(&hub), Some(&body)).is_err());
    }

    #[test]
    fn malformed_updates_clear_and_old_connection_updates_are_refused() {
        let state = state();
        let hub = url::Url::parse(HUB).unwrap();
        let valid = serde_json::json!({"origin": ASSETS});
        for body in [None, Some(Value::Null), Some(Value::String(ASSETS.into())),
                     Some(serde_json::json!({})), Some(serde_json::json!({"origin": false})),
                     Some(serde_json::json!({"origin": ASSETS, "other": true})),
                     Some(serde_json::json!({"origin": ""})), Some(serde_json::json!({"origin": "http://team.files.example.com"})),
                     Some(serde_json::json!({"files": FILES})), Some(serde_json::json!({"origin": "", "files": ""})),
                     Some(serde_json::json!({"origin": "", "files": false})),
                     Some(serde_json::json!({"origin": ASSETS, "files": "http://acme-files.ticofiles.com"}))] {
            assert_eq!(set_visual_origin(&state, LABEL, Some(&hub), Some(&valid)), Ok(true));
            assert_eq!(set_visual_origin(&state, LABEL, Some(&hub), body.as_ref()), Ok(false));
            assert!(state.visual_origin.lock().unwrap().is_none());
        }
        assert_eq!(set_visual_origin(&state, LABEL, Some(&hub), Some(&valid)), Ok(true));
        let old = state.visual_origin.lock().unwrap().clone().unwrap();
        select_connection(&state, None);
        assert!(state.visual_origin.lock().unwrap().is_none());
        assert!(set_visual_origin(&state, LABEL, Some(&hub), Some(&valid)).is_err());
        let next = url::Url::parse("https://other.example.com/").unwrap();
        select_connection(&state, Some(Config::new(next.clone())));
        assert!(set_visual_origin(&state, LABEL, Some(&hub), Some(&valid)).is_err());
        let visual = url::Url::parse(&format!("{ASSETS}/visuals/payload.{}", "A".repeat(43))).unwrap();
        assert!(!old.permits(Some(&next), &visual));
    }

    #[test]
    fn file_hosting_alone_allows_its_signed_visual_links_in_frames() {
        let state = state();
        let hub = url::Url::parse(HUB).unwrap();
        let link = url::Url::parse(&format!("{FILES}/visuals/ab/ab{}?e=1793250000&k=k1&s={}", "0".repeat(62), "A".repeat(43))).unwrap();
        let signing_in = AtomicBool::new(false);
        assert!(!follow_window(&state, &signing_in, true, Some(&hub), &link), "not before the signed-in page names the address");
        let body = serde_json::json!({"origin": "", "files": FILES});
        assert_eq!(set_visual_origin(&state, LABEL, Some(&hub), Some(&body)), Ok(true));
        assert!(follow_window(&state, &signing_in, true, Some(&hub), &link));
        assert!(!signing_in.load(Ordering::Relaxed));
        let other = url::Url::parse(&format!("{FILES}/blobs/ab/ab{}?e=1793250000&k=k1&s={}", "0".repeat(62), "A".repeat(43))).unwrap();
        assert!(!follow_window(&state, &signing_in, true, Some(&hub), &other));
        assert!(!follow_window(&state, &signing_in, false, Some(&hub), &link), "a meeting window never follows one");
        assert!(set_visual_origin(&state, "rail", Some(&link), Some(&body)).is_err(), "a hosted page can't configure visuals");
    }

    #[test]
    fn visual_navigation_neither_starts_nor_ends_a_sign_in() {
        let state = state();
        let hub = url::Url::parse(HUB).unwrap();
        let body = serde_json::json!({"origin": ASSETS});
        let url = url::Url::parse(&format!("{ASSETS}/visuals/payload.{}", "A".repeat(43))).unwrap();
        assert!(state.visual_origin.lock().unwrap().is_none());
        for active in [false, true] {
            assert_eq!(set_visual_origin(&state, LABEL, Some(&hub), Some(&body)), Ok(true));
            let signing_in = AtomicBool::new(active);
            assert!(follow_window(&state, &signing_in, true, Some(&hub), &url));
            assert_eq!(signing_in.load(Ordering::Relaxed), active);
            top_level_window(&state, &signing_in, true, Some(&hub), &url);
            assert_eq!(signing_in.load(Ordering::Relaxed), active);
            assert!(state.visual_origin.lock().unwrap().is_some());
        }
        let signing_in = AtomicBool::new(false);
        assert!(!follow_window(&state, &signing_in, false, Some(&hub), &url));
        top_level_window(&state, &signing_in, false, Some(&hub), &hub);
        assert!(state.visual_origin.lock().unwrap().is_some(), "another window cannot clear the open org's allowance");
        for boundary in ["https://team.example.com/api/v2/logout", "https://team.example.com/tico/auth/logout",
                         "https://team.cloudflareaccess.com/cdn-cgi/access/logout", "https://team.example.com/tico/auth/login",
                         "https://team.cloudflareaccess.com/cdn-cgi/access/login/team.example.com"] {
            assert_eq!(set_visual_origin(&state, LABEL, Some(&hub), Some(&body)), Ok(true));
            follow_window(&state, &signing_in, true, Some(&hub), &url::Url::parse(boundary).unwrap());
            assert!(state.visual_origin.lock().unwrap().is_none(), "{boundary}");
        }
        for top in [HUB, "https://accounts.google.com/ServiceLogin"] {
            assert_eq!(set_visual_origin(&state, LABEL, Some(&hub), Some(&body)), Ok(true));
            top_level_window(&state, &signing_in, true, Some(&hub), &url::Url::parse(top).unwrap());
            assert!(state.visual_origin.lock().unwrap().is_none(), "{top}");
        }
        assert!(!session_exit(Some(&hub), &url::Url::parse("https://other.example.com/api/v2/logout").unwrap()));
    }

    #[test]
    fn a_prefetched_page_keeps_its_own_visual_allowance_apart_from_the_open_one() {
        const OTHER: &str = "https://tidy.example.com/";
        let doc = Document { version: 1, active: Some(ORG.into()), orgs: vec![org(ORG, HUB, "", ""), org("o_bbbbbbbb", OTHER, "", "")] };
        let state = AppState::new(Orgs::new(doc, None), Some(Config::new(url::Url::parse(HUB).unwrap())));
        let (hub, other) = (url::Url::parse(HUB).unwrap(), url::Url::parse(OTHER).unwrap());
        let body = serde_json::json!({"origin": ASSETS});
        assert!(set_visual_origin(&state, "org-o_bbbbbbbb", Some(&other), Some(&body)).is_err(), "not prefetched: refused");
        state.prefetch.lock().unwrap().org = Some("o_bbbbbbbb".into());
        assert!(set_visual_origin(&state, "org-o_bbbbbbbb", Some(&hub), Some(&body)).is_err(), "only at its own origin");
        assert_eq!(set_visual_origin(&state, "org-o_bbbbbbbb", Some(&other), Some(&body)), Ok(true));
        assert!(state.visual_origin.lock().unwrap().is_none(), "the open page's allowance is untouched");
        assert!(state.parked_visual.lock().unwrap().as_ref().unwrap().serves(Some(&other)));
        // The open page's own allowance and navigation never touch the hidden page's, nor the other way round.
        assert_eq!(set_visual_origin(&state, LABEL, Some(&hub), Some(&body)), Ok(true));
        let signing_in = AtomicBool::new(false);
        top_level_window(&state, &signing_in, true, Some(&other), &url::Url::parse("https://accounts.google.com/").unwrap());
        assert!(state.visual_origin.lock().unwrap().is_some());
        assert!(state.parked_visual.lock().unwrap().is_none());
        // Only calls about the page itself are let through from a page that is not on screen.
        for name in ["orgInfo", "visualOrigin", "multiOrg", "bootTiming", "capture", "hasRecorder"] { assert!(own_page_call(name)); }
        for name in ["openWindow", "checkForUpdates", "showLog", "showOrgMenu", "addOrg", "windowMode", "openRecorder"] { assert!(!own_page_call(name)); }
    }

    #[test]
    fn the_bridge_never_tells_an_org_about_another() {
        let acme = org("o_aaaaaaaa", "https://acme.example.com/", "Acme Lights", "ops@acme.example");
        let tidy = org("o_bbbbbbbb", "https://tidy.example.com/", "TIDY Team", "chris@tidy.example");
        let secrets = ["o_bbbbbbbb", "https://tidy.example.com/", "tidy.example.com", "TIDY Team", "chris@tidy.example"];
        let doc = Document { version: 1, active: Some(acme.id.clone()), orgs: vec![acme, tidy] };
        let state = AppState::new(Orgs::new(doc, None), None);
        assert_eq!(state.orgs.lock().unwrap().orgs().len(), 2);
        // Every reply the page can get, with two orgs open in the shell, carries none of the other org's details.
        for name in ["orgInfo", "multiOrg", "showOrgMenu", "addOrg", "openWindow", "visualOrigin", "checkForUpdates", "showLog", "appLog", "bootTiming"] {
            let reply = bridge_reply(name, 2).to_string();
            for secret in secrets {
                assert!(!reply.contains(secret), "{name} replied with {secret}");
            }
        }
        // The page script carries no org data in any build, and only this build's org features.
        for several in [false, true] {
            let script = script_for("0.5.0", several, several);
            for secret in secrets {
                assert!(!script.contains(secret), "the page script carries {secret}");
            }
            assert_eq!(script.contains("'multiOrg'"), several);
            assert_eq!(script.contains("'openRecorder'"), several, "Record is offered only by a Mac build");
        }
        assert!(BRIDGE_SCRIPT.contains("'orgInfo'"));
    }

    #[test]
    fn a_switch_waits_until_every_open_dialog_is_answered() {
        static GATE: DialogGate = DialogGate::new();
        GATE.wait_closed();
        let (first, second) = (GATE.opened(), GATE.opened());
        let (done, finished) = std::sync::mpsc::channel();
        let switch = std::thread::spawn(move || { GATE.wait_closed(); done.send(()).unwrap(); });
        drop(first);
        assert!(finished.recv_timeout(std::time::Duration::from_millis(200)).is_err(), "one dialog is still open");
        drop(second);
        finished.recv_timeout(std::time::Duration::from_secs(5)).expect("the switch goes on once both are answered");
        switch.join().unwrap();
    }

    #[test]
    fn every_page_reports_its_capture() {
        for several in [false, true] {
            let script = script_for("0.5.0", several);
            assert!(script.contains("name: 'capture'") && !script.contains("__ticoCapture"), "nothing the page can call to switch it off");
            assert!(script.contains("'getUserMedia', 'getDisplayMedia'"));
        }
    }

    #[test]
    fn a_page_alert_and_confirm_go_through_the_on_screen_check() {
        for several in [false, true] {
            let script = script_for("0.5.0", several, !several);
            assert!(script.contains("window.alert =") && script.contains("window.confirm ="));
            assert!(script.contains("invoke('page_dialog'") && !script.contains("plugin:dialog"));
        }
    }
}
