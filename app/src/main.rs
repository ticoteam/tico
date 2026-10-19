// Tico: a thin native shell around the local or cloud hub.
// A bundled first-run page selects the server, then one ordinary window shows the hub's own page. No
// menu-bar item: on macOS the Dock icon reopens a hidden window and carries the Needs you count. One
// source builds every company's app on macOS, Windows and Linux: the name, icon, identifier and server
// URL come in at build time (scripts/app.sh).
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

mod applog;
mod config;
mod hub;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, LogicalPosition, LogicalSize, Manager, PhysicalPosition, PhysicalSize, RunEvent, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_opener::OpenerExt;

use config::Config;
use hub::{Client, HubStatus};

const USER_AGENT: &str = "TicoHub/2.0 Tauri";
const MIN_WIDTH: u32 = 360;
const MIN_HEIGHT: u32 = 400;
// Lines of the shell's log a Support diagnostics bundle carries.
const LOG_LINES: usize = 200;

/// The main window's last outer frame (x, y, width, height) in `frame` units: points on macOS,
/// where one point space spans every display whatever its scale, and physical pixels elsewhere,
/// where the virtual screen is the consistent space. Older builds wrote `full` in physical pixels
/// and `mode` ("full" or "rail"); `mode` is ignored, so a window last used as the right rail opens
/// at its full frame.
#[derive(Serialize, Deserialize, Default, Debug, PartialEq)]
struct Saved {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    frame: Option<(f64, f64, f64, f64)>,
    #[serde(default, skip_serializing)]
    full: Option<(i32, i32, u32, u32)>,
}

/// Frames are kept in points on macOS (see `Saved`).
const POINTS: bool = cfg!(target_os = "macos");

/// Pixels per frame unit on a display with this scale factor.
fn unit(scale: f64, points: bool) -> f64 {
    if points && scale > 0.0 { scale } else { 1.0 }
}

impl Saved {
    /// The frame to restore. An older file's physical `full` frame converts to points only when every
    /// connected display has the same scale; with mixed scales its unit is unknown, so none.
    fn frame(&self, scales: &[f64], points: bool) -> Option<(f64, f64, f64, f64)> {
        if self.frame.is_some() { return self.frame; }
        let (x, y, w, h) = self.full?;
        let scale = *scales.first()?;
        if points && scales.iter().any(|s| (s - scale).abs() > 1e-6) { return None; }
        let u = unit(scale, points);
        Some((x as f64 / u, y as f64 / u, w as f64 / u, h as f64 / u))
    }
}

struct AppState {
    connection: Mutex<Option<Config>>,
    status: Mutex<HubStatus>,
    // Set while the shell itself places the window, so that placement is not saved as the person's.
    applying_frame: AtomicBool,
}

type App = AppHandle<tauri::Wry>;

fn main() {
    applog::init();
    let state = AppState {
        connection: Mutex::new(None),
        status: Mutex::new(HubStatus::default()),
        applying_frame: AtomicBool::new(false),
    };

    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build());
    builder
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_window(app);
        }))
        .manage(state)
        .invoke_handler(tauri::generate_handler![app_log, bridge, connect_server, server_address])
        .setup(move |app| {
            match applog::attach(log_dir(app.handle()).as_deref()) {
                Some(path) => log::info!("{} {} starting; this log is {}", config::app_name(), app.package_info().version, applog::shown(&path)),
                None => log::warn!("{} {} starting; no log file, so this log goes to stderr only", config::app_name(), app.package_info().version),
            }
            let path = server_path(app.handle())?;
            let config = Config::load(&path).map_err(std::io::Error::other)?;
            if let Some(config) = &config {
                app.add_capability(server_capability(&config.hub).to_string())?;
            }
            let state: State<AppState> = app.state();
            *state.connection.lock().unwrap() = config;
            if connection(app.handle()).is_some() { build_window(app.handle())?; }
            else { build_server_window(app.handle())?; }
            build_menu(app.handle())?;
            #[cfg(not(target_os = "macos"))]
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                let _ = app.deep_link().register_all();
            }
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                let handle = app.handle().clone();
                app.deep_link().on_open_url(move |event| {
                    for url in event.urls() {
                        open_deep_link(&handle, &url);
                    }
                });
            }
            start_polling(app.handle().clone());
            start_updates(app.handle().clone());
            restore_frame(app.handle());
            show_window(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != "main" {
                return;
            }
            match event {
                // On macOS closing hides and Quit quits; the Dock icon brings the window back.
                // Elsewhere there is no Dock to reopen it from, so closing closes.
                #[cfg(target_os = "macos")]
                WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    let _ = window.hide();
                }
                // A meeting window left open must not keep the app running with no main window.
                #[cfg(not(target_os = "macos"))]
                WindowEvent::Destroyed => window.app_handle().exit(0),
                WindowEvent::Moved(_) | WindowEvent::Resized(_) => remember_frame(window.app_handle()),
                _ => {}
            }
        })
        .on_menu_event(|app, event| menu_event(app, event.id().as_ref()))
        .build(tauri::generate_context!())
        .expect("the app could not start")
        .run(|app, event| {
            // A click on the Dock icon while the main window is hidden.
            #[cfg(target_os = "macos")]
            if let RunEvent::Reopen { .. } = event {
                if !main_window(app).and_then(|w| w.is_visible().ok()).unwrap_or(false) { show_window(app); }
            }
            #[cfg(not(target_os = "macos"))]
            let _ = (app, event);
        });
}

fn connection(app: &App) -> Option<Config> {
    app.state::<AppState>().connection.lock().unwrap().clone()
}

fn log_dir(app: &App) -> Option<std::path::PathBuf> {
    app.path().app_log_dir().ok()
}

fn server_path(app: &App) -> tauri::Result<std::path::PathBuf> {
    Ok(app.path().app_config_dir()?.join("server.txt"))
}

fn build_server_window(app: &App) -> tauri::Result<()> {
    if let Some(window) = app.get_webview_window("server") {
        window.show()?;
        window.set_focus()?;
        return Ok(());
    }
    WebviewWindowBuilder::new(app, "server", WebviewUrl::App("index.html".into()))
        .title(config::app_name()).inner_size(440.0, 400.0).min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        .on_navigation(|url| url.scheme() == "tauri" || matches!(url.host_str(), Some("tauri.localhost") | Some("localhost")))
        .build()?;
    Ok(())
}

#[tauri::command]
fn server_address(app: App) -> String {
    connection(&app).map(|c| c.hub.to_string()).unwrap_or_default()
}

#[tauri::command]
async fn connect_server(app: App, address: String) -> Result<(), String> {
    if !config::is_generic() { return Err("This app uses its built-in server.".into()); }
    let hub = config::validate_server(&address).await?;
    let path = server_path(&app).map_err(|_| "Could not open the app's settings folder.")?;
    std::fs::create_dir_all(path.parent().unwrap()).map_err(|_| "Could not create the app's settings folder.")?;
    let temporary = path.with_extension("tmp");
    std::fs::write(&temporary, hub.as_str()).and_then(|_| std::fs::rename(&temporary, &path))
        .map_err(|_| "Could not save the server address. Check free disk space and folder permissions.")?;
    // Runtime capabilities are additive. Restart so only the newly selected origin
    // receives permissions, and no permissions from the previous connection survive.
    app.request_restart();
    Ok(())
}

fn server_capability(hub: &url::Url) -> Value {
    let mut capability: Value = serde_json::from_str(include_str!("../capabilities/main.json")).unwrap();
    // Explicit port also confines default-port URLs: URLPattern otherwise treats an
    // omitted port as a wildcard. Host characters must stay literal in URLPattern.
    let mut host = String::new();
    for ch in hub.host_str().unwrap().chars() {
        if matches!(ch, ':' | '*' | '?' | '+' | '(' | ')' | '{' | '}' | '\\') { host.push('\\'); }
        host.push(ch);
    }
    let port = hub.port_or_known_default().unwrap();
    capability["remote"] = serde_json::json!({"urls": [format!("{}://{host}:{port}/*", hub.scheme())]});
    capability
}

#[cfg(test)]
mod capability_tests {
    use super::*;

    #[test]
    fn ipc_is_confined_to_selected_origin_and_main_window() {
        for (address, pattern) in [
            ("https://team.example.com/tico/", "https://team.example.com:443/*"),
            ("http://localhost:8765/", "http://localhost:8765/*"),
            ("http://team.example.com/", "http://team.example.com:80/*"),
            ("https://[::1]:8765/", r"https://[\:\:1]:8765/*"),
            ("https://*.example.com/", r"https://\*.example.com:443/*"),
        ] {
            let capability = server_capability(&url::Url::parse(address).unwrap());
            assert_eq!(capability["remote"]["urls"], serde_json::json!([pattern]));
            let matcher: tauri::utils::acl::RemoteUrlPattern = pattern.parse().unwrap();
            assert!(matcher.test(&url::Url::parse(address).unwrap()));
            for other in ["https://accounts.google.com/", "https://team.cloudflareaccess.com/",
                          "https://other.example.com/", "https://team.example.com:8443/",
                          "http://team.example.com:8080/", "http://localhost:8766/"] {
                assert!(!matcher.test(&url::Url::parse(other).unwrap()), "{pattern} accepted {other}");
            }
            assert_eq!(capability["windows"], serde_json::json!(["main"]));
            assert_eq!(capability["local"], false);
        }
        let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        assert_eq!(config["app"]["security"]["capabilities"], serde_json::json!(["server"]));
    }
}

// ----------------------------------------------------------------------------- window
fn build_window(app: &App) -> tauri::Result<()> {
    let Some(config) = connection(app) else { return build_server_window(app) };
    let start = config.start_url();
    let mut builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(start))
        .title(config::app_name())
        .inner_size(1240.0, 820.0)
        .min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        .user_agent(USER_AGENT)
        .devtools(true)
        .visible(false)
        .initialization_script(&bridge_script(app))
        .on_new_window(new_window_handler(app));
    // A sign-in's whole redirect chain stays in this web view, so the resulting cookie belongs to the
    // app, not the browser.
    builder = follow_sign_in(app, builder);
    #[cfg(target_os = "macos")]
    {
        // The native title bar drags the window on every page, including sign-in pages that are
        // not ours; an overlay title bar would leave dragging to each page.
        builder = builder.title_bar_style(tauri::TitleBarStyle::Visible);
    }
    builder.build()?;
    Ok(())
}

/// Anything that is not the hub opens in the default browser, except during a sign-in: from the hub's
/// own Cloudflare Access login, or the hub's own sign-in, the whole redirect chain stays in the window
/// whatever identity provider it passes through, so the resulting cookie belongs to the app, not the
/// browser. Each window keeps its own `signing_in`. Embedded frames navigate through the same handler
/// with nothing to tell them apart, so only the top-level page ends a sign-in, by committing on the hub
/// origin; and only the hub's own logins start one.
fn follow_sign_in<'a>(app: &App, builder: WebviewWindowBuilder<'a, tauri::Wry, App>) -> WebviewWindowBuilder<'a, tauri::Wry, App> {
    let signing_in = Arc::new(AtomicBool::new(false));
    let (opener, page, committed) = (app.clone(), app.clone(), signing_in.clone());
    builder
        .on_navigation(move |url| stays_in_app(&opener, &signing_in, url))
        // `Started` is the top-level page committing, with its address; frames never send it.
        .on_page_load(move |_, payload| {
            if matches!(payload.event(), tauri::webview::PageLoadEvent::Started) {
                top_level_page(connection(&page).map(|c| c.hub).as_ref(), &committed, payload.url());
            }
        })
}

fn stays_in_app(opener: &App, signing_in: &AtomicBool, url: &url::Url) -> bool {
    let hub = connection(opener).map(|c| c.hub);
    let stays = follow(hub.as_ref(), signing_in, url);
    if !stays {
        let _ = opener.opener().open_url(url.as_str(), None::<&str>);
    }
    stays
}

/// Whether the window follows a navigation, from any frame; a sign-in's start sets `signing_in`.
fn follow(hub: Option<&url::Url>, signing_in: &AtomicBool, url: &url::Url) -> bool {
    match navigation(hub, signing_in.load(Ordering::Relaxed), url) {
        Navigation::Start => signing_in.store(true, Ordering::Relaxed),
        Navigation::Stay => {}
        Navigation::Browser => return false,
    }
    true
}

/// The top-level page committed at `url`: back on the hub origin, a sign-in is over. Another port or
/// scheme on the hub's host is not the hub, so a sign-in passing through one goes on.
fn top_level_page(hub: Option<&url::Url>, signing_in: &AtomicBool, url: &url::Url) {
    if hub.is_some_and(|hub| url.origin() == hub.origin()) {
        signing_in.store(false, Ordering::Relaxed);
    }
}

/// Where a navigation in an app window goes.
#[derive(Debug, PartialEq)]
enum Navigation {
    /// The hub's own sign-in: stays, and so does every page after it until the top-level page is back
    /// on the hub. Cloudflare's provider choice is itself a clicked link, and the provider may be
    /// anyone's (Google, Microsoft, Okta, GitHub), so no list of provider hosts would do.
    Start,
    /// The hub's host, Cloudflare Access, Google, a page during a sign-in, or not a site at all
    /// (`about:blank`, the app's own `tauri:` pages): stays, and changes nothing.
    Stay,
    /// Anything else opens in the default browser.
    Browser,
}

fn navigation(hub: Option<&url::Url>, signing_in: bool, url: &url::Url) -> Navigation {
    // A Markdown mailto (or tel, sms) link has no target, so it arrives here rather than as a new window.
    if matches!(url.scheme(), "mailto" | "tel" | "sms") {
        return Navigation::Browser;
    }
    let host = hub.and_then(|hub| hub.host_str()).unwrap_or("");
    let target = url.host_str().unwrap_or("");
    // Cloudflare's login names the application it signs in to: /cdn-cgi/access/login/<hostname>.
    let access_login = target.ends_with(".cloudflareaccess.com")
        && url.path().strip_prefix("/cdn-cgi/access/login/").is_some_and(|app| app.split('/').next() == Some(host));
    // The hub's own sign-in (TICO_AUTH_PROXY=oidc) starts on the hub and redirects to the provider.
    let hub_login = hub.is_some_and(|hub| url.origin() == hub.origin()) && url.path().ends_with("/auth/login");
    if target.is_empty() || url.scheme() == "tauri" {
        Navigation::Stay
    } else if !host.is_empty() && (access_login || hub_login) {
        Navigation::Start
    } else if target == host || target.ends_with(".cloudflareaccess.com") || target == "accounts.google.com"
        || (signing_in && matches!(url.scheme(), "http" | "https")) {
        Navigation::Stay
    } else {
        Navigation::Browser
    }
}

/// Where a request for a new window (a `target="_blank"` link, `window.open`) goes. The web view has
/// no tabs, and without a handler it drops the request, so the click does nothing. The page opens no
/// sign-in pop-ups (sign-in redirects within the window, see `stays_in_app`), so no pop-up is let in.
#[derive(Debug, PartialEq)]
enum NewWindow {
    /// The hub's own files and logs under `/api/`: the default browser has none of the app's sign-in,
    /// so they open in an app window of their own.
    Hub,
    /// A new tab in the default browser, as a browser would open it.
    Browser,
    /// Nothing the browser could open (`about:blank`, `javascript:`, `data:`, `blob:`, `file:`).
    Ignore,
}

fn new_window_target(hub: Option<&url::Url>, url: &url::Url) -> NewWindow {
    match url.scheme() {
        "http" | "https" if hub.is_some_and(|hub| url.origin() == hub.origin()) && url.path().starts_with("/api/") => NewWindow::Hub,
        "http" | "https" | "mailto" | "tel" | "sms" => NewWindow::Browser,
        _ => NewWindow::Ignore,
    }
}

fn new_window_handler(app: &App) -> impl Fn(url::Url, tauri::webview::NewWindowFeatures) -> tauri::webview::NewWindowResponse<tauri::Wry> + Send + 'static {
    let opener = app.clone();
    move |url, _| {
        match new_window_target(connection(&opener).map(|c| c.hub).as_ref(), &url) {
            NewWindow::Hub => {
                // Built off the web view's own callback, which may not block on a new window.
                let app = opener.clone();
                tauri::async_runtime::spawn(async move { open_hub_window(&app, url) });
            }
            NewWindow::Browser => { let _ = opener.opener().open_url(url.as_str(), None::<&str>); }
            NewWindow::Ignore => {}
        }
        tauri::webview::NewWindowResponse::Deny
    }
}

/// An ordinary window on one of the hub's files or logs. It shares the app's sign-in, but like a
/// meeting window it is not `main`, so the page gets no native permissions.
fn open_hub_window(app: &App, url: url::Url) {
    static NEXT: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(1);
    let label = format!("hub-{}", NEXT.fetch_add(1, Ordering::Relaxed));
    #[cfg(not(target_os = "linux"))]
    let opened = url.clone();
    #[allow(unused_mut)]
    let mut builder = follow_sign_in(app, WebviewWindowBuilder::new(app, &label, WebviewUrl::External(url))
        .title(config::app_name())
        .inner_size(1000.0, 800.0)
        .min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        .user_agent(USER_AGENT)
        .on_new_window(new_window_handler(app)));
    // A file the web view cannot show (Word, Excel, zip) downloads, leaving this window empty: save
    // it to Downloads, show it there and close the window. Only the file this window opened on
    // counts, so a window that showed its file stays open for later downloads from it. Not on
    // Linux: there each window's handler sits on the shared web context and outlives its window,
    // then refuses every later download in the app; WebKit saves to Downloads by itself and the
    // window stays empty.
    #[cfg(not(target_os = "linux"))]
    {
        let saved = Mutex::new(None);
        builder = builder.on_download(move |webview, event| {
            match event {
                tauri::webview::DownloadEvent::Requested { url, destination } if same_file(&url, &opened) => {
                    *saved.lock().unwrap() = Some(destination.clone());
                }
                tauri::webview::DownloadEvent::Finished { url, success, .. } if same_file(&url, &opened) => {
                    match (success, saved.lock().unwrap().take()) {
                        (true, Some(path)) => { let _ = webview.app_handle().opener().reveal_item_in_dir(path); }
                        _ => log::warn!("a file window's download failed"),
                    }
                    let _ = webview.window().close();
                }
                _ => {}
            }
            true
        });
    }
    let _ = builder.build();
}

/// The same file, whatever its fragment.
#[cfg_attr(target_os = "linux", allow(dead_code))]
fn same_file(a: &url::Url, b: &url::Url) -> bool {
    a.as_str().split('#').next() == b.as_str().split('#').next()
}

#[cfg(test)]
mod new_window_tests {
    use super::*;

    #[test]
    fn new_windows_open_in_the_browser_except_hub_files() {
        for hub in ["https://team.example.com/", "https://team.example.com/tico/"] {
            let hub = url::Url::parse(hub).unwrap();
            for (address, expected) in [
                ("https://github.com/ticoteam/tico/pull/1", NewWindow::Browser),
                ("http://example.com/", NewWindow::Browser),
                ("https://team.example.com/#/tasks", NewWindow::Browser),
                ("https://team.example.com/api/v2/files/f1", NewWindow::Hub),
                ("https://team.example.com:8443/api/v2/files/f1", NewWindow::Browser),
                ("http://team.example.com/api/v2/files/f1", NewWindow::Browser),
                ("https://other.example.com/api/v2/files/f1", NewWindow::Browser),
                ("https://team.example.com@evil.example/api/v2/files/f1", NewWindow::Browser),
                ("mailto:support@example.com", NewWindow::Browser),
                ("tel:+15550100", NewWindow::Browser),
                ("sms:+15550100", NewWindow::Browser),
                ("https://team.cloudflareaccess.com/cdn-cgi/access/login", NewWindow::Browser),
                ("https://accounts.google.com/o/oauth2/auth", NewWindow::Browser),
                ("about:blank", NewWindow::Ignore),
                ("javascript:alert(1)", NewWindow::Ignore),
                ("data:text/html,hi", NewWindow::Ignore),
                ("blob:https://team.example.com/0b1c", NewWindow::Ignore),
                ("file:///etc/passwd", NewWindow::Ignore),
            ] {
                assert_eq!(new_window_target(Some(&hub), &url::Url::parse(address).unwrap()), expected, "{hub} {address}");
            }
        }
        assert_eq!(new_window_target(None, &url::Url::parse("https://team.example.com/api/x").unwrap()), NewWindow::Browser);
    }

    const HUB: &str = "https://team.example.com/";
    const ACCESS_LOGIN: &str = "https://team.cloudflareaccess.com/cdn-cgi/access/login/team.example.com?kid=aud&redirect_url=%2F";
    const MICROSOFT: &str = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize";

    fn go(signing_in: bool, address: &str) -> Navigation {
        navigation(Some(&url::Url::parse(HUB).unwrap()), signing_in, &url::Url::parse(address).unwrap())
    }

    /// What one window does with a run of events: `Nav` is a navigation from any frame, `Top` the
    /// top-level page committing. Returns whether each navigation stayed in the window.
    enum Event { Nav(&'static str), Top(&'static str) }

    fn run(signing_in: &AtomicBool, events: &[Event]) -> Vec<bool> {
        let hub = url::Url::parse(HUB).unwrap();
        events.iter().filter_map(|event| match event {
            Event::Nav(address) => Some(follow(Some(&hub), signing_in, &url::Url::parse(address).unwrap())),
            Event::Top(address) => { top_level_page(Some(&hub), signing_in, &url::Url::parse(address).unwrap()); None }
        }).collect()
    }

    #[test]
    fn mailto_navigation_leaves_the_app() {
        for signing_in in [false, true] {
            assert_eq!(go(signing_in, "mailto:support@example.com"), Navigation::Browser);
            assert_eq!(go(signing_in, "tel:+15550100"), Navigation::Browser);
            assert_eq!(go(signing_in, "sms:+15550100"), Navigation::Browser);
            assert_eq!(go(signing_in, "about:blank"), Navigation::Stay);
            assert_eq!(go(signing_in, "ftp://files.example.com/"), Navigation::Browser);
        }
        assert_eq!(go(false, "https://example.com/"), Navigation::Browser);
        assert_eq!(go(false, "https://team.example.com/#/tasks"), Navigation::Stay);
    }

    #[test]
    fn only_the_hubs_own_logins_start_a_sign_in() {
        for address in [ACCESS_LOGIN, "https://team.example.com/auth/login?next=/", "https://team.example.com/tico/auth/login"] {
            assert_eq!(go(false, address), Navigation::Start, "{address}");
        }
        // Google and the rest of Access stay as before, but start nothing; so do other apps' Access logins.
        for address in ["https://accounts.google.com/o/oauth2/auth", "https://team.cloudflareaccess.com/cdn-cgi/access/callback",
                        "https://team.cloudflareaccess.com/cdn-cgi/access/login/other.example.com",
                        "https://team.cloudflareaccess.com/cdn-cgi/access/login/team.example.com.evil.example",
                        "https://team.example.com:8443/auth/login", "http://team.example.com/auth/login"] {
            assert_eq!(go(false, address), Navigation::Stay, "{address}");
        }
        // A look-alike of the hub or of Access is someone else's site.
        for address in ["https://team.example.com.evil.example/auth/login", "https://team.example.com@evil.example/auth/login",
                        "https://cloudflareaccess.com.evil.example/cdn-cgi/access/login/team.example.com"] {
            assert_eq!(go(false, address), Navigation::Browser, "{address}");
        }
        // No hub configured: nothing starts.
        let url = url::Url::parse("https://team.cloudflareaccess.com/cdn-cgi/access/login/").unwrap();
        assert_eq!(navigation(None, false, &url), Navigation::Stay);
    }

    #[test]
    fn during_a_sign_in_any_provider_stays() {
        for address in [MICROSOFT, "https://acme.okta.com/oauth2/v1/authorize", "https://github.com/login/oauth/authorize",
                        "http://sso.acme.example/"] {
            assert_eq!(go(true, address), Navigation::Stay, "{address}");
            assert_eq!(go(false, address), Navigation::Browser, "{address}");
        }
    }

    #[test]
    fn access_with_microsoft_ends_back_on_the_hub() {
        use Event::*;
        let signing_in = AtomicBool::new(false);
        // Access's provider page, Microsoft, back through Access to the hub; then Microsoft as an ordinary link.
        let stays = run(&signing_in, &[Top(HUB), Nav(ACCESS_LOGIN), Top(ACCESS_LOGIN), Nav(MICROSOFT), Nav("https://login.live.com/ppsecure"),
            Top(MICROSOFT), Nav("about:blank"), Nav("https://team.cloudflareaccess.com/cdn-cgi/access/callback"),
            Nav("https://team.example.com/cdn-cgi/access/authorized"), Nav(HUB), Top(HUB), Nav(MICROSOFT)]);
        assert_eq!(stays, [true, true, true, true, true, true, true, false]);
        // Instant Auth: the hub's redirect goes through Access straight to the provider, before any page commits.
        assert_eq!(run(&signing_in, &[Nav(HUB), Nav(ACCESS_LOGIN), Nav(MICROSOFT)]), [true, true, true]);
    }

    #[test]
    fn a_frame_neither_ends_nor_starts_a_sign_in() {
        use Event::*;
        // The provider's page frames the hub mid sign-in: no top-level commit, so the next redirect still stays.
        let signing_in = AtomicBool::new(false);
        assert_eq!(run(&signing_in, &[Nav(ACCESS_LOGIN), Top(ACCESS_LOGIN), Nav(MICROSOFT), Top(MICROSOFT),
                                      Nav("https://team.example.com/api/v2/me"), Nav("https://acme.okta.com/oauth2/v1/authorize")]),
                   [true, true, true, true]);
        // The hub frames Google, or another app's Access login: an ordinary link still opens the browser.
        let signing_in = AtomicBool::new(false);
        assert_eq!(run(&signing_in, &[Top(HUB), Nav("https://accounts.google.com/ServiceLogin"),
                                      Nav("https://team.cloudflareaccess.com/cdn-cgi/access/login/docs.example.com"),
                                      Nav("https://github.com/login")]),
                   [true, true, false]);
    }

    #[test]
    fn the_hubs_host_on_another_port_or_scheme_is_not_the_end() {
        use Event::*;
        let signing_in = AtomicBool::new(false);
        let stays = run(&signing_in, &[Nav(ACCESS_LOGIN), Nav("https://team.example.com:8443/sso/authorize"),
            Top("https://team.example.com:8443/sso/authorize"), Nav(MICROSOFT), Nav("http://team.example.com/sso/next"),
            Top("http://team.example.com/sso/next"), Nav("https://acme.okta.com/oauth2/v1/authorize"),
            Nav("https://team.example.com/auth/callback?code=x"), Top("https://team.example.com/#/tasks"), Nav(MICROSOFT)]);
        assert_eq!(stays, [true, true, true, true, true, true, false]);
    }

    #[test]
    fn each_window_has_its_own_sign_in() {
        use Event::*;
        let (signing_in, other) = (AtomicBool::new(false), AtomicBool::new(false));
        assert_eq!(run(&signing_in, &[Nav(ACCESS_LOGIN)]), [true]);
        assert_eq!(run(&other, &[Nav("https://github.com/login")]), [false]);
    }

    #[test]
    fn a_file_window_acts_only_on_its_own_download() {
        let opened = url::Url::parse("https://team.example.com/api/v2/files/f1?v=2").unwrap();
        let same = |address: &str| same_file(&url::Url::parse(address).unwrap(), &opened);
        assert!(same("https://team.example.com/api/v2/files/f1?v=2"));
        assert!(same("https://team.example.com/api/v2/files/f1?v=2#page=1"));
        assert!(!same("https://team.example.com/api/v2/files/f1"));
        assert!(!same("https://team.example.com/api/v2/files/f2?v=2"));
        assert!(!same("blob:https://team.example.com/0b1c"));
    }
}

/// One ordinary, resizable window per meeting, showing only that meeting (`&window=1`); asking
/// again for the same one brings it to the front.
fn open_meeting_window(app: &App, id: &str) {
    if id.is_empty() || id.len() > 64 || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return;
    }
    let label = format!("meeting-{id}");
    if let Some(window) = app.get_webview_window(&label) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    let Some(config) = connection(app) else { return };
    let mut url = config.hub.clone();
    url.set_fragment(Some(&format!("/meetings?meeting={id}&window=1")));
    let builder = WebviewWindowBuilder::new(app, &label, WebviewUrl::External(url))
        .title("Meeting")
        .inner_size(1100.0, 820.0)
        .min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        .resizable(true)
        .user_agent(USER_AGENT)
        .on_new_window(new_window_handler(app));
    let _ = follow_sign_in(app, builder).build();
}

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
  window.ticoNativeFeatures = ['openWindow', 'checkForUpdates', 'showLog', 'appLog'];
  // The shell's version and the end of its log ({version, lines}), for Support diagnostics.
  window.ticoNativeLog = () => invoke('app_log');
})();
"#;

/// The bridge plus this shell's version, which Settings shows beside the server's.
fn bridge_script(app: &App) -> String {
    let version = serde_json::to_string(&app.package_info().version.to_string()).unwrap_or_else(|_| "\"\"".into());
    format!("{BRIDGE_SCRIPT}window.ticoNativeVersion = {version};\n")
}

fn main_window(app: &App) -> Option<WebviewWindow> {
    app.get_webview_window("main")
}

fn show_window(app: &App) {
    if let Some(window) = main_window(app).or_else(|| app.get_webview_window("server")) {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

/// Where this app keeps its window frame: its own config folder, which follows the bundle
/// identifier, so each company's app (and the generic app) remembers its own place and size.
fn saved_path(app: &App) -> Option<std::path::PathBuf> {
    Some(app.path().app_config_dir().ok()?.join("window.json"))
}

/// Builds before this one kept the frame under ~/.config/tico/app/<environment>, shared by every
/// app built without an environment. Read once when the app's own file does not exist yet.
fn legacy_saved_path() -> Option<std::path::PathBuf> {
    let home = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE"))?;
    let name = option_env!("TICO_ENV_SLUG").filter(|s| !s.is_empty()).unwrap_or("tico");
    Some(std::path::PathBuf::from(home).join(".config").join("tico").join("app").join(name).join("window.json"))
}

fn parse_saved(text: &str) -> Saved {
    serde_json::from_str(text).unwrap_or_default()
}

fn load_saved(app: &App) -> Saved {
    let read = |path: Option<std::path::PathBuf>| path.and_then(|p| std::fs::read_to_string(p).ok());
    read(saved_path(app)).or_else(|| read(legacy_saved_path())).map(|s| parse_saved(&s)).unwrap_or_default()
}

fn save(app: &App, saved: &Saved) {
    let Some(path) = saved_path(app) else { return };
    if let Some(dir) = path.parent() { let _ = std::fs::create_dir_all(dir); }
    let _ = std::fs::write(path, serde_json::to_string(saved).unwrap_or_default());
}

/// Every move and resize the person makes is kept, so the next launch opens where they left it.
fn remember_frame(app: &App) {
    let state: State<AppState> = app.state();
    if state.applying_frame.load(Ordering::Relaxed) {
        return;
    }
    let Some(window) = main_window(app) else { return };
    // A minimised or full-screen window is not a frame to reopen at.
    if window.is_minimized().unwrap_or(false) || window.is_fullscreen().unwrap_or(false) {
        return;
    }
    let (Ok(position), Ok(size), Ok(scale)) = (window.outer_position(), window.outer_size(), window.scale_factor()) else { return };
    if size.width < MIN_WIDTH || size.height < MIN_HEIGHT {
        return;
    }
    let u = unit(scale, POINTS);
    let frame = (position.x as f64 / u, position.y as f64 / u, size.width as f64 / u, size.height as f64 / u);
    save(app, &Saved { frame: Some(frame), full: None });
}

/// A saved frame is usable when enough of its title bar lies on a display that is still connected,
/// so the person can grab it; a frame from a display since unplugged is not restored. Frame and work
/// areas are in the same units (points on macOS).
fn frame_reachable(frame: (f64, f64, f64, f64), areas: &[(f64, f64, f64, f64)]) -> bool {
    let (x, y, w, _) = frame;
    areas.iter().any(|&(ax, ay, aw, ah)| {
        let (left, right) = (x.max(ax), (x + w).min(ax + aw));
        let (top, bottom) = (y.max(ay), (y + 24.0).min(ay + ah));
        right - left >= 80f64.min(w) && bottom > top
    })
}

/// Once, before the main window first shows: the saved frame if it is still on a connected display,
/// otherwise the default size, centred. The window is never moved again by the app after that.
fn restore_frame(app: &App) {
    let state: State<AppState> = app.state();
    let Some(window) = main_window(app) else { return };
    state.applying_frame.store(true, Ordering::Relaxed);
    let monitors = window.available_monitors().unwrap_or_default();
    let scales: Vec<f64> = monitors.iter().map(|m| m.scale_factor()).collect();
    let areas: Vec<_> = monitors.iter().map(|m| {
        let (area, u) = (m.work_area(), unit(m.scale_factor(), POINTS));
        (area.position.x as f64 / u, area.position.y as f64 / u, area.size.width as f64 / u, area.size.height as f64 / u)
    }).collect();
    match load_saved(app).frame(&scales, POINTS) {
        Some(frame @ (x, y, w, h)) if frame_reachable(frame, &areas) => {
            // Saved frames include the title bar; set_size takes the content size.
            let u = window.scale_factor().map(|s| unit(s, POINTS)).unwrap_or(1.0);
            let (outer, inner) = (window.outer_size(), window.inner_size());
            let (dw, dh) = match (outer, inner) {
                (Ok(o), Ok(i)) => (o.width.saturating_sub(i.width) as f64 / u, o.height.saturating_sub(i.height) as f64 / u),
                _ => (0.0, 0.0),
            };
            let (cw, ch) = ((w - dw).max(1.0), (h - dh).max(1.0));
            if POINTS {
                let _ = window.set_size(LogicalSize::new(cw, ch));
                let _ = window.set_position(LogicalPosition::new(x, y));
            } else {
                let _ = window.set_size(PhysicalSize::new(cw.round() as u32, ch.round() as u32));
                let _ = window.set_position(PhysicalPosition::new(x.round() as i32, y.round() as i32));
            }
        }
        _ => {
            let _ = window.set_size(LogicalSize::new(1240.0, 820.0));
            let _ = window.center();
        }
    }
    state.applying_frame.store(false, Ordering::Relaxed);
}

#[cfg(test)]
mod window_frame_tests {
    use super::*;

    #[test]
    fn a_window_saved_as_the_right_rail_reopens_at_its_full_frame() {
        let rail = parse_saved(r#"{"mode":"rail","full":[240,160,2480,1640]}"#);
        assert_eq!(rail.frame(&[2.0, 2.0], true), Some((120.0, 80.0, 1240.0, 820.0)));
        assert_eq!(rail.frame(&[1.25], false), Some((240.0, 160.0, 2480.0, 1640.0)));
        // Rail mode with no full frame yet: the default size, centred once.
        assert_eq!(parse_saved(r#"{"mode":"rail"}"#).frame(&[2.0], true), None);
        assert_eq!(parse_saved("not json"), Saved::default());
        let saved = Saved { frame: Some((1.0, 2.5, 3.0, 4.0)), full: None };
        assert_eq!(serde_json::to_string(&saved).unwrap(), r#"{"frame":[1.0,2.5,3.0,4.0]}"#);
        assert_eq!(parse_saved(&serde_json::to_string(&saved).unwrap()).frame(&[1.0, 2.0], true), saved.frame);
    }

    #[test]
    fn an_old_pixel_frame_is_not_guessed_at_on_mixed_scale_displays() {
        // Laptop at 2x, external at 1x: the old physical frame's unit depends on the screen it was
        // saved on, so the window is centred once instead.
        let old = parse_saved(r#"{"full":[5800,200,800,1600]}"#);
        assert_eq!(old.frame(&[1.0, 2.0], true), None);
        // Windows and Linux kept virtual-screen pixels all along.
        assert_eq!(old.frame(&[1.0, 2.0], false), Some((5800.0, 200.0, 800.0, 1600.0)));
    }

    #[test]
    fn a_saved_frame_is_restored_only_where_its_title_bar_can_be_grabbed() {
        let laptop = (0.0, 25.0, 1512.0, 920.0);
        let external = (1512.0, -200.0, 2560.0, 1415.0);
        assert!(frame_reachable((100.0, 100.0, 1240.0, 820.0), &[laptop]));
        assert!(frame_reachable((1700.0, 0.0, 1240.0, 820.0), &[laptop, external]));
        // The external display was unplugged.
        assert!(!frame_reachable((1700.0, 0.0, 1240.0, 820.0), &[laptop]));
        // Title bar above the menu bar's work area, or almost entirely off the right edge.
        assert!(!frame_reachable((100.0, -900.0, 1240.0, 820.0), &[laptop]));
        assert!(!frame_reachable((1480.0, 100.0, 1240.0, 820.0), &[laptop]));
        assert!(frame_reachable((1400.0, 100.0, 1240.0, 820.0), &[laptop]));
        assert!(!frame_reachable((100.0, 100.0, 1240.0, 820.0), &[]));
    }

    #[test]
    fn mixed_scale_displays_are_compared_in_points() {
        // A 1x 1920x1080 main display and a 2x laptop (1512x982 points) to its right, as macOS
        // reports their work areas in pixels, converted per display.
        let scaled = |(x, y, w, h): (i32, i32, u32, u32), s: f64| {
            let u = unit(s, true);
            (x as f64 / u, y as f64 / u, w as f64 / u, h as f64 / u)
        };
        let main = scaled((0, 25, 1920, 1055), 1.0);
        let laptop = scaled((3840, 0, 3024, 1964), 2.0);
        assert_eq!(laptop, (1920.0, 0.0, 1512.0, 982.0));
        // A window saved on the laptop at (2900, 100) points is reachable there.
        assert!(frame_reachable((2900.0, 100.0, 400.0, 800.0), &[main, laptop]));
        // In points, the same pixel frame (5800, 200) lies past the laptop's right edge at 3432.
        assert!(!frame_reachable((5800.0, 200.0, 800.0, 1600.0), &[main, laptop]));
    }
}

// Search from the View menu (⌘K): bring the window forward, then the page opens
// its own search dialog (`window.ticoSearch` in ui/app/search.js). The menu accelerator takes
// ⌘K before the webview sees it, so the page's own shortcut still works everywhere else.
fn open_search(app: &App) {
    show_window(app);
    eval(app, "window.ticoSearch && window.ticoSearch()");
}

fn eval(app: &App, js: &str) {
    match main_window(app) {
        Some(window) => { if let Err(e) = window.eval(js) { log::warn!("eval failed: {e}"); } }
        None => log::warn!("eval: no main window"),
    }
}

// Cmd+R reaches this through the View menu too.
fn reload(app: &App) {
    if let (Some(config), Some(window)) = (connection(app), main_window(app)) {
        let _ = window.navigate(config.start_url());
    }
}

// ----------------------------------------------------------------------------- bridge
/// Generic builds accept any server; only the selected origin's page in the main window may call the shell.
fn from_server(app: &App, window: &WebviewWindow) -> bool {
    let Some(config) = connection(app) else { return false };
    window.label() == "main" && window.url().map(|u| u.origin() == config.hub.origin()).unwrap_or(false)
}

/// What the page posts: `openWindow` ({meeting: id}). Older pages also post `windowMode`, which is
/// ignored now that the window has one mode.
#[tauri::command]
fn bridge(app: App, window: WebviewWindow, name: String, body: Option<Value>) {
    if !from_server(&app, &window) { return; }
    match name.as_str() {
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
}

fn show_log(app: &App) {
    let Some(dir) = log_dir(app) else { return };
    let file = dir.join(applog::FILE_NAME);
    let shown = if file.exists() { app.opener().reveal_item_in_dir(&file) } else { app.opener().open_path(dir.to_string_lossy(), None::<&str>) };
    if let Err(e) = shown { log::warn!("could not show the log: {e}"); }
}

/// This shell's version and the end of its log, which the page adds to a Support diagnostics bundle.
#[tauri::command]
fn app_log(app: App, window: WebviewWindow) -> Result<Value, String> {
    if !from_server(&app, &window) { return Err("Only the selected server's page may read the app's log.".into()); }
    let lines = log_dir(&app).map(|dir| applog::tail(&dir.join(applog::FILE_NAME), LOG_LINES)).unwrap_or_default();
    Ok(serde_json::json!({"version": app.package_info().version.to_string(), "lines": lines}))
}

// ----------------------------------------------------------------------------- deep links
// tico://meetings/<id> and any other hub path land on the page in the app rather than a browser.
fn open_deep_link(app: &App, url: &url::Url) {
    let path = format!("{}{}", url.host_str().unwrap_or(""), url.path()).trim_matches('/').to_string();
    if path.is_empty() {
        show_window(app);
        return;
    }
    let hash = serde_json::to_string(&format!("#/{path}")).unwrap();
    eval(app, &format!("location.hash = {hash}"));
    show_window(app);
}

// ----------------------------------------------------------------------------- app menu
fn build_menu(app: &App) -> tauri::Result<()> {
    let name = config::app_name();
    let app_menu = Submenu::new(app, &name, true)?;
    app_menu.append(&PredefinedMenuItem::about(app, Some(&format!("About {name}")), None)?)?;
    app_menu.append(&MenuItem::with_id(app, "updates", "Check for Updates…", true, None::<&str>)?)?;
    if config::is_generic() {
        app_menu.append(&MenuItem::with_id(app, "server", "Change server…", true, None::<&str>)?)?;
    }
    app_menu.append_items(&[
        &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::hide(app, None)?,
        &PredefinedMenuItem::quit(app, Some(&format!("Quit {name}")))?,
    ])?;
    let edit = Submenu::with_items(app, "Edit", true, &[
        &PredefinedMenuItem::undo(app, None)?, &PredefinedMenuItem::redo(app, None)?, &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::cut(app, None)?, &PredefinedMenuItem::copy(app, None)?, &PredefinedMenuItem::paste(app, None)?,
        &PredefinedMenuItem::select_all(app, None)?,
    ])?;
    let view = Submenu::with_items(app, "View", true, &[
        &MenuItem::with_id(app, "search", "Search…", true, Some("CmdOrCtrl+K"))?,
        &PredefinedMenuItem::separator(app)?,
        &MenuItem::with_id(app, "reload", "Reload", true, Some("CmdOrCtrl+R"))?,
    ])?;
    let window = Submenu::with_items(app, "Window", true, &[
        &PredefinedMenuItem::close_window(app, None)?, &PredefinedMenuItem::minimize(app, None)?,
    ])?;
    app.set_menu(Menu::with_items(app, &[&app_menu, &edit, &view, &window])?)?;
    Ok(())
}

fn menu_event(app: &App, id: &str) {
    match id {
        "server" if config::is_generic() => { let _ = build_server_window(app); }
        "search" => open_search(app),
        "reload" => reload(app),
        "updates" => update_now(app),
        _ => {}
    }
}

// ----------------------------------------------------------------------------- Dock badge
/// The Needs you count on the Dock icon (macOS), cleared at zero or when the hub cannot be read. Signed out of
/// Cloudflare Access, it shows "!" instead: the count is unknown, and the window says to sign in again.
fn render_badge(app: &App) {
    let HubStatus { needs, signed_out, .. } = app.state::<AppState>().status.lock().unwrap().clone();
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        if let Some(window) = main_window(&handle) {
            if signed_out { badge_signed_out(&window) } else { let _ = window.set_badge_count(badge_count(needs)); }
        }
    });
}

#[cfg(target_os = "macos")]
fn badge_signed_out(window: &WebviewWindow) {
    let _ = window.set_badge_label(Some("!".into()));
}

// A label is macOS only; elsewhere the unknown count is cleared.
#[cfg(not(target_os = "macos"))]
fn badge_signed_out(window: &WebviewWindow) {
    let _ = window.set_badge_count(None);
}

fn badge_count(needs: usize) -> Option<i64> {
    (needs > 0).then(|| needs.min(i64::MAX as usize) as i64)
}

#[cfg(test)]
mod badge_tests {
    #[test]
    fn the_dock_badge_shows_needs_you_and_clears_at_zero() {
        assert_eq!(super::badge_count(0), None);
        assert_eq!(super::badge_count(7), Some(7));
    }
}

// ----------------------------------------------------------------------------- updates
// The page comes from the hub, so the site changes with no app update at all. The shell itself
// changes rarely; when it does, the generic app fetches a signed GitHub release, installs
// it and relaunches automatically. Per-environment builds retain their hub updater. Checked hourly.
fn start_updates(app: App) {
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_secs(20)).await;
        loop {
            check_for_update(&app).await;
            tokio::time::sleep(Duration::from_secs(60 * 60)).await;
        }
    });
}

fn configure_company_updater(
    builder: tauri_plugin_updater::UpdaterBuilder,
    policy: &hub::CompanyUpdatePolicy,
) -> Result<tauri_plugin_updater::UpdaterBuilder, tauri_plugin_updater::Error> {
    let endpoint = policy.endpoint().clone();
    let policy = policy.clone();
    builder.endpoints(vec![endpoint]).map(|builder| {
        builder.configure_client(move |client| policy.configure_client(client))
    })
}

/// Checks now, from the menu or the page; a check already running is not doubled.
fn update_now(app: &App) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move { check_for_update(&app).await });
}

static CHECKING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

async fn check_for_update(app: &App) {
    use std::sync::atomic::Ordering;
    if CHECKING.swap(true, Ordering::SeqCst) { return; }
    check_for_update_once(app).await;
    CHECKING.store(false, Ordering::SeqCst);
}

/// Tells the page how a check went (`window.ticoAppUpdate` in ui/app/notices.js), so a failed update is
/// shown with its reason instead of only reaching the log.
fn report_update(app: &App, state: &str, detail: &str) {
    match state {
        "failed" => log::warn!("update check failed: {detail}"),
        "current" => log::info!("update check: {} is current", app.package_info().version),
        _ => log::info!("update check: {state} {detail} (from {})", app.package_info().version),
    }
    let body = serde_json::json!({"state": state, "detail": detail});
    eval(app, &format!("window.ticoAppUpdate && window.ticoAppUpdate({body})"));
}

async fn check_for_update_once(app: &App) {
    use tauri_plugin_updater::UpdaterExt;
    let selected = connection(app);
    if !config::is_generic() && selected.as_ref().map(|c| c.is_local()).unwrap_or(true) { return; }
    let builder = app.updater_builder();
    let company_policy = if config::is_generic() { None } else {
        let Some(connection) = selected.as_ref() else { return };
        if connection.hub.scheme() != "https" {
            report_update(app, "failed", "the update feed needs an HTTPS server address");
            return;
        }
        let configured = app.config().plugins.0.get("updater").and_then(|p| p.get("endpoints"))
            .and_then(Value::as_array).and_then(|a| a.first()).and_then(Value::as_str);
        let endpoint = match hub::company_update_endpoint(&connection.hub, configured) {
            Ok(endpoint) if endpoint.scheme() == "https" => endpoint,
            Ok(_) => { report_update(app, "failed", "the update feed is not HTTPS"); return; }
            Err(_) => { report_update(app, "failed", "the update feed address is invalid"); return; }
        };
        let cookie = if hub::same_origin(&connection.hub, &endpoint) {
            Client::new(connection.clone()).cookie_header(app, &endpoint)
        } else {
            None
        };
        // A cookie is optional: public team feeds work without an Access session. CompanyUpdatePolicy
        // drops it for a configured runner_url on another origin and limits hub downloads/redirects.
        Some(hub::CompanyUpdatePolicy::new(connection.hub.clone(), endpoint, cookie.as_deref()))
    };
    let updater_result = if let Some(policy) = &company_policy {
        configure_company_updater(builder, policy).and_then(|builder| builder.build())
    } else {
        builder.build()
    };
    let updater = match updater_result {
        Ok(updater) => updater,
        Err(e) => { report_update(app, "failed", &format!("updater unavailable: {e}")); return; }
    };
    log::info!("update check: reading the {} feed", if company_policy.is_some() { "company" } else { "release" });
    let update = match updater.check().await {
        Ok(Some(update)) => update,
        Ok(None) => { report_update(app, "current", ""); return; }
        Err(e) => { report_update(app, "failed", &format!("could not read the update feed: {e}")); return; }
    };
    if let Some(policy) = company_policy.as_ref() {
        if update.download_url.scheme() != "https" || !policy.allows_download(&update.download_url) {
            report_update(app, "failed", "the update file is outside the allowed address");
            return;
        }
    }
    report_update(app, "installing", &update.version);
    if let Err(e) = update.download_and_install(|_, _| {}, || {}).await {
        report_update(app, "failed", &format!("install failed: {e}"));
        return;
    }
    app.restart();
}

// ----------------------------------------------------------------------------- polling
// Status every 30 s, for the Dock badge.
fn start_polling(app: App) {
    tauri::async_runtime::spawn(async move {
        loop {
            refresh_status(&app).await;
            tokio::time::sleep(Duration::from_secs(30)).await;
        }
    });
}

async fn refresh_status(app: &App) {
    let state: State<AppState> = app.state();
    let Some(config) = connection(app) else { return };
    let fresh = Client::new(config.clone()).status(app).await;
    // A status request for the previous server may finish after someone changes the address.
    if connection(app).map(|c| c.hub) != Some(config.hub) { return; }
    *state.status.lock().unwrap() = fresh;
    render_badge(app);
}

#[cfg(test)]
mod updater_integration_tests {
    use super::*;
    use url::Url;
    use minisign::{KeyPair, sign};
    use base64::Engine;
    use std::io::{Cursor, Read, Write};
    use std::net::{SocketAddr, TcpListener, TcpStream};
    use std::thread;
    use std::time::Duration;
    use tauri_plugin_updater::UpdaterExt;

    const VERSION: &str = "0.3.21";
    const TARGET: &str = "tico-updater-test";
    const COOKIE: &str = "CF_Authorization=synthetic-test-session";

    fn key_and_signature(bytes: &[u8]) -> (String, String) {
        let pair = KeyPair::generate_unencrypted_keypair().unwrap();
        let public_key = pair.pk.to_box().unwrap().to_string();
        let signature = sign(
            Some(&pair.pk),
            &pair.sk,
            Cursor::new(bytes.to_vec()),
            Some("version: 0.3.21"),
            Some("synthetic updater test"),
        ).unwrap().into_string();
        (base64::engine::general_purpose::STANDARD.encode(public_key),
         base64::engine::general_purpose::STANDARD.encode(signature))
    }

    fn test_app() -> tauri::App<tauri::test::MockRuntime> {
        let mut context = tauri::test::mock_context(tauri::test::noop_assets());
        context.config_mut().plugins.0.insert("updater".into(), serde_json::json!({"pubkey": "", "dangerousInsecureTransportProtocol": true}));
        tauri::test::mock_builder()
            .plugin(tauri_plugin_updater::Builder::new().build())
            .build(context)
            .expect("build mock Tauri app")
    }

    fn test_updater(
        builder: tauri_plugin_updater::UpdaterBuilder,
        policy: &hub::CompanyUpdatePolicy,
        public_key: String,
    ) -> tauri_plugin_updater::Updater {
        let executable = std::env::temp_dir()
            .join("TicoUpdaterTest.app")
            .join("Contents")
            .join("MacOS")
            .join("Tico");
        configure_company_updater(builder, policy).unwrap()
            .pubkey(public_key)
            .target(TARGET)
            .version_comparator(|_, _| true)
            .executable_path(executable)
            .build()
            .expect("build test updater")
    }

    fn bind() -> (TcpListener, SocketAddr) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        (listener, address)
    }

    fn url(address: SocketAddr, path: &str) -> Url {
        Url::parse(&format!("http://{address}{path}")).unwrap()
    }

    fn manifest(artifact: &Url, signature: &str) -> String {
        let mut platforms = serde_json::Map::new();
        platforms.insert(
            TARGET.to_string(),
            serde_json::json!({
                "url": artifact.as_str(),
                "signature": signature
            }),
        );
        serde_json::json!({
            "version": VERSION,
            "app_kind": "company",
            "platforms": platforms
        }).to_string()
    }

    fn read_request(socket: &mut TcpStream) -> String {
        socket.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
        let mut request = Vec::new();
        let mut chunk = [0; 1024];
        loop {
            let count = socket.read(&mut chunk).unwrap();
            request.extend_from_slice(&chunk[..count]);
            if count == 0 || request.windows(4).any(|window| window == b"\r\n\r\n") {
                break;
            }
        }
        String::from_utf8_lossy(&request).to_string()
    }

    fn respond(socket: &mut TcpStream, status: &str, headers: &[(&str, &str)], body: &[u8]) {
        let mut response = format!(
            "HTTP/1.1 {status}\r\nConnection: close\r\nContent-Length: {}\r\n",
            body.len()
        );
        for (name, value) in headers {
            response.push_str(&format!("{name}: {value}\r\n"));
        }
        response.push_str("\r\n");
        socket.write_all(response.as_bytes()).unwrap();
        socket.write_all(body).unwrap();
    }

    fn signed_s3_location(address: SocketAddr) -> String {
        format!(
            "http://{address}/object?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test%2Fscope&X-Amz-Date=20261004T170000Z&X-Amz-Expires=60&X-Amz-SignedHeaders=host&X-Amz-Signature={}",
            "ab".repeat(32)
        )
    }

    fn new_hub_policy(address: SocketAddr) -> hub::CompanyUpdatePolicy {
        let hub = url(address, "/");
        let endpoint = hub.join("download/latest.json").unwrap();
        hub::CompanyUpdatePolicy::new(hub, endpoint, Some(COOKIE))
    }

    #[tokio::test]
    async fn tauri_updater_checks_hub_manifest_downloads_signed_s3_artifact_and_verifies_signature() {
        let expected = b"synthetic Tico updater artifact";
        let tampered = b"tampered synthetic updater artifact";
        let (public_key, signature) = key_and_signature(expected);
        let (hub_listener, hub_address) = bind();
        let (storage_listener, storage_address) = bind();
        let artifact_url = url(hub_address, "/download/file/0.3.21/Tico.tar.gz");
        let feed_manifest = manifest(&artifact_url, &signature);
        let storage_url = signed_s3_location(storage_address);
        let hub_server = thread::spawn(move || {
            let (mut feed_socket, _) = hub_listener.accept().unwrap();
            let feed_request = read_request(&mut feed_socket);
            respond(&mut feed_socket, "200 OK", &[("Content-Type", "application/json")],
                    feed_manifest.as_bytes());
            let mut artifact_requests = Vec::new();
            for _ in 0..2 {
                let (mut artifact_socket, _) = hub_listener.accept().unwrap();
                artifact_requests.push(read_request(&mut artifact_socket));
                respond(&mut artifact_socket, "302 Found", &[("Location", &storage_url)], b"");
            }
            (feed_request, artifact_requests)
        });
        let storage_server = thread::spawn(move || {
            let mut requests = Vec::new();
            for body in [expected.as_slice(), tampered.as_slice()] {
                let (mut socket, _) = storage_listener.accept().unwrap();
                requests.push(read_request(&mut socket));
                respond(&mut socket, "200 OK", &[("Content-Type", "application/octet-stream")], body);
            }
            requests
        });

        let policy = new_hub_policy(hub_address);
        let app = test_app();
        let updater = test_updater(app.updater_builder(), &policy, public_key);
        let update = updater.check().await.unwrap().expect("hub manifest update");
        assert_eq!(update.version, VERSION);
        assert!(policy.allows_download(&update.download_url));
        let downloaded = update.download(|_, _| {}, || {}).await.unwrap();
        assert_eq!(downloaded.as_slice(), expected.as_slice());
        assert!(update.download(|_, _| {}, || {}).await.is_err(),
                "a tampered artifact must fail the updater signature check");

        let (feed_request, artifact_requests) = hub_server.join().unwrap();
        let storage_requests = storage_server.join().unwrap();
        assert!(feed_request.starts_with("GET /download/latest.json "));
        assert!(feed_request.to_ascii_lowercase().contains(&format!("cookie: {COOKIE}").to_ascii_lowercase()));
        assert_eq!(artifact_requests.len(), 2);
        assert!(artifact_requests.iter().all(|request| {
            request.starts_with("GET /download/file/0.3.21/Tico.tar.gz ")
                && request.to_ascii_lowercase().contains(&format!("cookie: {COOKIE}").to_ascii_lowercase())
        }));
        assert_eq!(storage_requests.len(), 2);
        assert!(storage_requests.iter().all(|request| {
            request.starts_with("GET /object?X-Amz-Algorithm=AWS4-HMAC-SHA256")
                && !request.to_ascii_lowercase().contains("cookie:")
                && !request.to_ascii_lowercase().contains("authorization:")
        }));
    }

    #[tokio::test]
    async fn tauri_updater_rejects_access_login_redirect_without_requesting_login_origin() {
        let expected = b"synthetic Tico updater artifact";
        let (public_key, signature) = key_and_signature(expected);
        let (hub_listener, hub_address) = bind();
        let (login_listener, login_address) = bind();
        login_listener.set_nonblocking(true).unwrap();
        let artifact_url = url(hub_address, "/download/file/0.3.21/Tico.tar.gz");
        let login_manifest = manifest(&artifact_url, &signature);
        let login_server = thread::spawn(move || {
            let deadline = std::time::Instant::now() + Duration::from_secs(2);
            loop {
                match login_listener.accept() {
                    Ok((mut socket, _)) => {
                        let request = read_request(&mut socket);
                        respond(&mut socket, "200 OK", &[("Content-Type", "application/json")],
                                login_manifest.as_bytes());
                        return Some(request);
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
                        && std::time::Instant::now() < deadline => {
                        thread::sleep(Duration::from_millis(10));
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => return None,
                    Err(error) => panic!("login listener failed: {error}"),
                }
            }
        });
        let hub_server = thread::spawn(move || {
            let (mut socket, _) = hub_listener.accept().unwrap();
            let request = read_request(&mut socket);
            let location = format!("http://{login_address}/cdn-cgi/access/login");
            respond(&mut socket, "302 Found", &[("Location", &location)], b"");
            request
        });

        let policy = new_hub_policy(hub_address);
        let app = test_app();
        let updater = test_updater(app.updater_builder(), &policy, public_key);
        assert!(updater.check().await.is_err(), "login redirects are not updater manifests");
        let hub_request = hub_server.join().unwrap();
        assert!(hub_request.to_ascii_lowercase().contains(&format!("cookie: {COOKIE}").to_ascii_lowercase()));
        assert!(login_server.join().unwrap().is_none(),
                "the updater must not request Cloudflare Access login endpoints");
    }

    #[tokio::test]
    async fn tauri_updater_keeps_configured_public_runner_feed_and_artifact_unauthenticated() {
        let bytes = b"synthetic public runner artifact";
        let (public_key, signature) = key_and_signature(bytes);
        let (runner_listener, runner_address) = bind();
        let hub = Url::parse("https://tico.example.test/").unwrap();
        let endpoint = url(runner_address, "/download/latest.json");
        let policy = hub::CompanyUpdatePolicy::new(hub, endpoint.clone(), Some(COOKIE));
        assert!(!policy.has_cookie());
        let artifact_url = url(runner_address, "/download/file/0.3.21/Tico.tar.gz");
        let body = manifest(&artifact_url, &signature);
        let runner = thread::spawn(move || {
            let (mut feed_socket, _) = runner_listener.accept().unwrap();
            let feed_request = read_request(&mut feed_socket);
            respond(&mut feed_socket, "200 OK", &[("Content-Type", "application/json")], body.as_bytes());
            let (mut artifact_socket, _) = runner_listener.accept().unwrap();
            let artifact_request = read_request(&mut artifact_socket);
            respond(&mut artifact_socket, "200 OK", &[("Content-Type", "application/octet-stream")], bytes);
            (feed_request, artifact_request)
        });

        let app = test_app();
        let updater = test_updater(app.updater_builder(), &policy, public_key);
        let update = updater.check().await.unwrap().expect("public runner update");
        assert_eq!(update.download_url, artifact_url);
        assert!(policy.allows_download(&update.download_url));
        let downloaded = update.download(|_, _| {}, || {}).await.unwrap();
        assert_eq!(downloaded.as_slice(), bytes.as_slice());
        let (feed_request, artifact_request) = runner.join().unwrap();
        assert!(feed_request.starts_with("GET /download/latest.json "));
        assert!(artifact_request.starts_with("GET /download/file/0.3.21/Tico.tar.gz "));
        assert!(!feed_request.to_ascii_lowercase().contains("cookie:"));
        assert!(!artifact_request.to_ascii_lowercase().contains("cookie:"));
    }
}
