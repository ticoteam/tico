//! The page's side of the native bridge: the script that gives the page `ticoNative`, the commands it calls,
//! and the check that only the selected server's page in the main window may use them.
use serde_json::Value;
use tauri::{Manager, WebviewWindow};
use tauri_plugin_opener::OpenerExt;

use crate::windows::open_meeting_window;
use crate::{log_dir, update_now, App, AppState, LOG_LINES};
use crate::{applog, visual_origin};

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
  window.ticoNativeFeatures = ['openWindow', 'checkForUpdates', 'showLog', 'appLog', 'visualOrigin', 'visualFiles'];
  // The shell's version and the end of its log ({version, lines}), for Support diagnostics.
  window.ticoNativeLog = () => invoke('app_log');
})();
"#;

/// The bridge plus this shell's version, which Settings shows beside the server's.
pub(crate) fn bridge_script(app: &App) -> String {
    let version = serde_json::to_string(&app.package_info().version.to_string()).unwrap_or_else(|_| "\"\"".into());
    format!("{BRIDGE_SCRIPT}window.ticoNativeVersion = {version};\n")
}

// ----------------------------------------------------------------------------- bridge
/// Generic builds accept any server; only the selected origin's page in the main window may call the shell.
fn from_server(app: &App, window: &WebviewWindow) -> bool {
    let document = window.url().ok();
    let state = app.state::<AppState>();
    let connection = state.connection.lock().unwrap();
    server_document(connection.as_ref().map(|c| &c.hub), window.label(), document.as_ref())
}

fn server_document(hub: Option<&url::Url>, label: &str, document: Option<&url::Url>) -> bool {
    label == "main" && hub.zip(document).is_some_and(|(hub, document)| {
        matches!(document.scheme(), "http" | "https") && document.username().is_empty()
            && document.password().is_none() && document.origin() == hub.origin()
    })
}

fn set_visual_origin(state: &AppState, label: &str, document: Option<&url::Url>, body: Option<&Value>) -> Result<bool, String> {
    let connection = state.connection.lock().unwrap();
    let hub = connection.as_ref().map(|c| &c.hub);
    if !server_document(hub, label, document) {
        return Err("Only the selected server's page may configure visuals.".into());
    }
    let mut visual = state.visual_origin.lock().unwrap();
    // `{origin}`, or `{origin, files}` from a page that saw `visualFiles` in this shell's features: Tico file
    // hosting's address, which shows pages and SVG when the server has no visual origin. Anything else clears.
    fn text(value: Option<&Value>) -> Option<&str> { value.map_or(Some(""), Value::as_str) }
    let raw = body.and_then(Value::as_object)
        .filter(|object| object.contains_key("origin") && object.keys().all(|key| key == "origin" || key == "files"))
        .and_then(|object| Some((text(object.get("origin"))?, text(object.get("files"))?)));
    *visual = hub.zip(raw).and_then(|(hub, (raw, files))| visual_origin::VisualOrigin::with_files(hub, raw, files));
    Ok(visual.is_some())
}

/// What the page posts: `openWindow` ({meeting: id}). Older pages also post `windowMode`, which is
/// ignored now that the window has one mode.
#[tauri::command]
pub(crate) fn bridge(app: App, window: WebviewWindow, name: String, body: Option<Value>) -> Result<Value, String> {
    if !from_server(&app, &window) { return Err("Only the selected server's page may call the app.".into()); }
    match name.as_str() {
        "visualOrigin" => return set_visual_origin(&app.state::<AppState>(), window.label(), window.url().ok().as_ref(), body.as_ref()).map(Value::Bool),
        "openWindow" => {
            // `recording` is what an older page called it.
            let id = body.as_ref().and_then(|b| b.get("meeting").or_else(|| b.get("recording"))).and_then(Value::as_str);
            if let Some(id) = id {
                open_meeting_window(&app, id);
            }
        }
        // The page's "App update" bar: check the feed now instead of at the next six-hourly check.
        "checkForUpdates" => update_now(&app),
        // Help's "Show the app's log": the log file, selected in Finder (or the platform's file manager).
        "showLog" => show_log(&app),
        _ => {}
    }
    Ok(Value::Null)
}

fn show_log(app: &App) {
    let Some(dir) = log_dir(app) else { return };
    let file = dir.join(applog::FILE_NAME);
    let shown = if file.exists() { app.opener().reveal_item_in_dir(&file) } else { app.opener().open_path(dir.to_string_lossy(), None::<&str>) };
    if let Err(e) = shown { log::warn!("could not show the log: {e}"); }
}

/// This shell's version and the end of its log, which the page adds to a Support diagnostics bundle.
#[tauri::command]
pub(crate) fn app_log(app: App, window: WebviewWindow) -> Result<Value, String> {
    if !from_server(&app, &window) { return Err("Only the selected server's page may read the app's log.".into()); }
    let lines = log_dir(&app).map(|dir| applog::tail(&dir.join(applog::FILE_NAME), LOG_LINES)).unwrap_or_default();
    Ok(serde_json::json!({"version": app.package_info().version.to_string(), "lines": lines}))
}

#[cfg(test)]
mod visual_bridge_tests {
    use super::*;
    use crate::config::Config;
    use crate::hub::HubStatus;
    use crate::windows::{follow_window, session_exit, top_level_window};
    use crate::{select_connection, AppState};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Mutex;

    const HUB: &str = "https://team.example.com/tico/";
    const ASSETS: &str = "https://team.files.example.com";

    fn state() -> AppState {
        AppState {
            connection: Mutex::new(Some(Config::new(url::Url::parse(HUB).unwrap()))),
            visual_origin: Mutex::new(None),
            status: Mutex::new(HubStatus::default()),
            applying_frame: AtomicBool::new(false),
        }
    }

    #[test]
    fn visual_allowance_never_grants_bridge_or_log_authority() {
        let state = state();
        let hub = url::Url::parse(HUB).unwrap();
        let body = serde_json::json!({"origin": ASSETS});
        assert_eq!(set_visual_origin(&state, "main", Some(&hub), Some(&body)), Ok(true));
        let visual = url::Url::parse(&format!("{ASSETS}/visuals/payload.{}", "A".repeat(43))).unwrap();
        assert!(state.visual_origin.lock().unwrap().as_ref().unwrap().permits(Some(&hub), &visual));
        for document in [visual, url::Url::parse("https://team.example.com.evil.example/").unwrap(),
                         url::Url::parse("about:blank").unwrap(), url::Url::parse("data:text/html,hi").unwrap(),
                         url::Url::parse("blob:https://team.example.com/opaque").unwrap()] {
            // Both bridge and app_log use this guard, including after a visual becomes top-level.
            assert!(!server_document(Some(&hub), "main", Some(&document)), "{document}");
            assert!(set_visual_origin(&state, "main", Some(&document), Some(&body)).is_err());
        }
        assert!(server_document(Some(&hub), "main", Some(&hub)));
        assert!(!server_document(Some(&hub), "meeting-1", Some(&hub)));
        assert!(!server_document(Some(&hub), "main", None));
        assert!(set_visual_origin(&state, "meeting-1", Some(&hub), Some(&body)).is_err());
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
            assert_eq!(set_visual_origin(&state, "main", Some(&hub), Some(&valid)), Ok(true));
            assert_eq!(set_visual_origin(&state, "main", Some(&hub), body.as_ref()), Ok(false));
            assert!(state.visual_origin.lock().unwrap().is_none());
        }
        assert_eq!(set_visual_origin(&state, "main", Some(&hub), Some(&valid)), Ok(true));
        let old = state.visual_origin.lock().unwrap().clone().unwrap();
        select_connection(&state, None);
        assert!(state.visual_origin.lock().unwrap().is_none());
        assert!(set_visual_origin(&state, "main", Some(&hub), Some(&valid)).is_err());
        let next = url::Url::parse("https://other.example.com/").unwrap();
        select_connection(&state, Some(Config::new(next.clone())));
        assert!(set_visual_origin(&state, "main", Some(&hub), Some(&valid)).is_err());
        let visual = url::Url::parse(&format!("{ASSETS}/visuals/payload.{}", "A".repeat(43))).unwrap();
        assert!(!old.permits(Some(&next), &visual));
    }

    const FILES: &str = "https://acme-files.ticofiles.com";

    #[test]
    fn file_hosting_alone_allows_its_signed_visual_links_in_frames() {
        let state = state();
        let hub = url::Url::parse(HUB).unwrap();
        let link = url::Url::parse(&format!("{FILES}/visuals/ab/ab{}?e=1793250000&k=k1&s={}", "0".repeat(62), "A".repeat(43))).unwrap();
        let signing_in = AtomicBool::new(false);
        assert!(!follow_window(&state, &signing_in, true, &link), "not before the signed-in page names the address");
        let body = serde_json::json!({"origin": "", "files": FILES});
        assert_eq!(set_visual_origin(&state, "main", Some(&hub), Some(&body)), Ok(true));
        assert!(follow_window(&state, &signing_in, true, &link));
        assert!(!signing_in.load(Ordering::Relaxed));
        let other = url::Url::parse(&format!("{FILES}/blobs/ab/ab{}?e=1793250000&k=k1&s={}", "0".repeat(62), "A".repeat(43))).unwrap();
        assert!(!follow_window(&state, &signing_in, true, &other));
        assert!(!follow_window(&state, &signing_in, false, &link), "a meeting window never follows one");
        assert!(set_visual_origin(&state, "main", Some(&link), Some(&body)).is_err(), "a hosted page can't configure visuals");
    }

    #[test]
    fn visual_navigation_neither_starts_nor_ends_a_sign_in() {
        let state = state();
        let hub = url::Url::parse(HUB).unwrap();
        let body = serde_json::json!({"origin": ASSETS});
        let url = url::Url::parse(&format!("{ASSETS}/visuals/payload.{}", "A".repeat(43))).unwrap();
        assert!(state.visual_origin.lock().unwrap().is_none());
        for active in [false, true] {
            assert_eq!(set_visual_origin(&state, "main", Some(&hub), Some(&body)), Ok(true));
            let signing_in = AtomicBool::new(active);
            assert!(follow_window(&state, &signing_in, true, &url));
            assert_eq!(signing_in.load(Ordering::Relaxed), active);
            top_level_window(&state, &signing_in, true, &url);
            assert_eq!(signing_in.load(Ordering::Relaxed), active);
            assert!(state.visual_origin.lock().unwrap().is_some());
        }
        let signing_in = AtomicBool::new(false);
        assert!(!follow_window(&state, &signing_in, false, &url));
        top_level_window(&state, &signing_in, false, &hub);
        assert!(state.visual_origin.lock().unwrap().is_some(), "another window cannot clear main's allowance");
        for boundary in ["https://team.example.com/api/v2/logout", "https://team.example.com/tico/auth/logout",
                         "https://team.cloudflareaccess.com/cdn-cgi/access/logout", "https://team.example.com/tico/auth/login",
                         "https://team.cloudflareaccess.com/cdn-cgi/access/login/team.example.com"] {
            assert_eq!(set_visual_origin(&state, "main", Some(&hub), Some(&body)), Ok(true));
            follow_window(&state, &signing_in, true, &url::Url::parse(boundary).unwrap());
            assert!(state.visual_origin.lock().unwrap().is_none(), "{boundary}");
        }
        for top in [HUB, "https://accounts.google.com/ServiceLogin"] {
            assert_eq!(set_visual_origin(&state, "main", Some(&hub), Some(&body)), Ok(true));
            top_level_window(&state, &signing_in, true, &url::Url::parse(top).unwrap());
            assert!(state.visual_origin.lock().unwrap().is_none(), "{top}");
        }
        assert!(!session_exit(Some(&hub), &url::Url::parse("https://other.example.com/api/v2/logout").unwrap()));
    }
}
