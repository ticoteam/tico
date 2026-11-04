//! The page's side of the native bridge: the script that gives the page `ticoNative`, the commands it calls,
//! and the check that only the open org's own page may use them.
use serde_json::Value;
use tauri::webview::Webview;
use tauri::Manager;
use tauri_plugin_opener::OpenerExt;

use crate::orgs;
use crate::windows::{open_meeting_window, org_label, Home};
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
  window.ticoNativeFeatures = ['openWindow', 'checkForUpdates', 'showLog', 'appLog', 'visualOrigin', 'visualFiles', 'orgInfo'];
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

/// The bridge plus this shell's version, which Settings shows beside the server's.
pub(crate) fn bridge_script(app: &App) -> String {
    script_for(&app.package_info().version.to_string(), orgs::multi_org_build())
}

/// The page script for a build: the bridge, the org features when this build keeps a list of orgs, and the version.
fn script_for(version: &str, several_orgs: bool) -> String {
    let version = serde_json::to_string(version).unwrap_or_else(|_| "\"\"".into());
    let extra = if several_orgs { ORG_SCRIPT } else { "" };
    format!("{BRIDGE_SCRIPT}{extra}window.ticoNativeVersion = {version};\n")
}

// ----------------------------------------------------------------------------- bridge
/// Generic builds accept any server; only the open org's own page may call the shell.
fn from_server(app: &App, webview: &Webview) -> bool {
    let document = webview.url().ok();
    let state = app.state::<AppState>();
    let list = state.orgs.lock().unwrap();
    let open = list.active().map(|org| org_label(&org.id));
    let connection = state.connection.lock().unwrap();
    server_document(connection.as_ref().map(|c| &c.hub), webview.label(), open.as_deref(), document.as_ref())
}

/// The page is the open org's page: in that org's own webview, at that org's origin. Not the rail, not an org that is
/// no longer open, and not a hosted file.
fn server_document(hub: Option<&url::Url>, label: &str, open: Option<&str>, document: Option<&url::Url>) -> bool {
    open == Some(label) && hub.zip(document).is_some_and(|(hub, document)| {
        matches!(document.scheme(), "http" | "https") && document.username().is_empty()
            && document.password().is_none() && document.origin() == hub.origin()
    })
}

/// What a page that is not on screen (a prefetch) may still post: about itself only, so that it is ready when shown.
/// Everything else (windows, menus, updates, the log) is the open page's alone.
fn own_page_call(name: &str) -> bool {
    matches!(name, "orgInfo" | "visualOrigin" | "multiOrg")
}

/// The org whose page this is, when the page is at that org's own origin: any org page, open or not.
fn page_org(app: &App, webview: &Webview) -> Option<String> {
    let id = webview.label().strip_prefix("org-")?;
    let hub = app.state::<AppState>().orgs.lock().unwrap().get(id)?.hub();
    let document = webview.url().ok();
    server_document(hub.as_ref(), webview.label(), Some(webview.label()), document.as_ref()).then(|| id.to_string())
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
    let open = list.active().map(|org| org_label(&org.id));
    if open.as_deref() != Some(label) {
        // The prefetched page, hidden: its allowance waits apart from the open page's until it is shown.
        let prefetched = state.prefetch.lock().unwrap().org.clone();
        let hub = prefetched.filter(|id| org_label(id) == label).and_then(|id| list.get(&id).and_then(|org| org.hub()));
        if hub.is_none() || !server_document(hub.as_ref(), label, Some(label), document) {
            return Err("Only the selected server's page may configure visuals.".into());
        }
        let mut parked = state.parked_visual.lock().unwrap();
        *parked = visual_from(hub.as_ref(), body);
        return Ok(parked.is_some());
    }
    let connection = state.connection.lock().unwrap();
    let hub = connection.as_ref().map(|c| &c.hub);
    if !server_document(hub, label, open.as_deref(), document) {
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
            let caller = webview.label().strip_prefix("org-").and_then(|org| app.state::<AppState>().orgs.lock().unwrap().get(org).cloned());
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
        // The org menu, at the page's point. It lists the shell's own orgs; the page is told nothing back.
        "showOrgMenu" => if orgs::multi_org_build() { popup_orgs(&app) },
        "addOrg" => {
            if orgs::multi_org_build() { add_org(&app); }
        }
        _ => {}
    }
    Ok(bridge_reply(&name, org_count(&app)))
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
        let id = label.strip_prefix("org-").filter(|id| list.get(id).is_some()).map(str::to_string);
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
        Org { id: id.into(), url: url.into(), store: None, name: name.into(), email: email.into(), added_at: String::new(), sign_in: false }
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
        for name in ["orgInfo", "visualOrigin", "multiOrg"] { assert!(own_page_call(name)); }
        for name in ["openWindow", "checkForUpdates", "showLog", "showOrgMenu", "addOrg", "windowMode"] { assert!(!own_page_call(name)); }
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
        for name in ["orgInfo", "multiOrg", "showOrgMenu", "addOrg", "openWindow", "visualOrigin", "checkForUpdates", "showLog", "appLog"] {
            let reply = bridge_reply(name, 2).to_string();
            for secret in secrets {
                assert!(!reply.contains(secret), "{name} replied with {secret}");
            }
        }
        // The page script carries no org data in any build, and only this build's org features.
        for several in [false, true] {
            let script = script_for("0.5.0", several);
            for secret in secrets {
                assert!(!script.contains(secret), "the page script carries {secret}");
            }
            assert_eq!(script.contains("'multiOrg'"), several);
        }
        assert!(BRIDGE_SCRIPT.contains("'orgInfo'"));
    }
}
