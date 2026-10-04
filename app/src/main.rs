// Tico: a thin native shell around the local or cloud hub.
// A bundled first-run page selects the server. One window on the hub's own page and a tray item
// toggle the window with a left click and show status on a right click. One source builds every company's app on macOS, Windows and
// Linux: the name, icon, identifier and server URL come in at build time (scripts/app.sh).
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

mod config;
mod hub;
#[cfg(target_os = "macos")]
mod identity;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, LogicalSize, Manager, PhysicalPosition, PhysicalSize, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_opener::OpenerExt;

use config::Config;
use hub::{Client, HubStatus};

const USER_AGENT: &str = "TicoHub/2.0 Tauri";
const MIN_WIDTH: u32 = 360;
const MIN_HEIGHT: u32 = 400;
const RAIL_WIDTH: u32 = 420;

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
enum WindowMode {
    Full,
    Rail,
}

#[derive(Serialize, Deserialize, Default)]
struct Saved {
    mode: Option<WindowMode>,
    full: Option<(i32, i32, u32, u32)>,
}

struct TrayItems {
    tray: TrayIcon,
    headline: MenuItem<tauri::Wry>,
    counts: MenuItem<tauri::Wry>,
    toggle: MenuItem<tauri::Wry>,
    mode: MenuItem<tauri::Wry>,
}

struct AppState {
    connection: Mutex<Option<Config>>,
    status: Mutex<HubStatus>,
    mode: Mutex<WindowMode>,
    applying_frame: AtomicBool,
    tray: Mutex<Option<TrayItems>>,
}

type App = AppHandle<tauri::Wry>;

fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    let saved = load_saved();
    let state = AppState {
        connection: Mutex::new(None),
        status: Mutex::new(HubStatus::default()),
        mode: Mutex::new(saved.mode.unwrap_or(WindowMode::Full)),
        applying_frame: AtomicBool::new(false),
        tray: Mutex::new(None),
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
        .invoke_handler(tauri::generate_handler![bridge, connect_server, server_address])
        .setup(move |app| {
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
            build_tray(app.handle())?;
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
            apply_window_mode(app.handle(), false);
            show_window(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != "main" {
                return;
            }
            match event {
                // Closing hides; Quit quits. The tray item stays.
                WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    let _ = window.hide();
                    render_tray(window.app_handle());
                }
                WindowEvent::Moved(_) | WindowEvent::Resized(_) => remember_full_frame(window.app_handle()),
                _ => {}
            }
        })
        .on_menu_event(|app, event| menu_event(app, event.id().as_ref()))
        .build(tauri::generate_context!())
        .expect("the app could not start")
        .run(|_app, _event| {});
}

fn connection(app: &App) -> Option<Config> {
    app.state::<AppState>().connection.lock().unwrap().clone()
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
        .title(config::app_name()).inner_size(440.0, 400.0).resizable(false)
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
    let opener = app.clone();
    let mut builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(start))
        .title(config::app_name())
        .inner_size(1240.0, 820.0)
        .min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        .user_agent(USER_AGENT)
        .devtools(true)
        .visible(false)
        .initialization_script(BRIDGE_SCRIPT)
        // Anything that is not the hub opens in the default browser. Cloudflare's provider
        // choice is itself a clicked link: the whole authentication redirect chain stays in this
        // web view so the resulting CF_Authorization cookie belongs to the app, not the browser.
        .on_navigation(move |url| stays_in_app(&opener, url));
    #[cfg(target_os = "macos")]
    {
        // Native chrome stays draggable on hub pages, sign-in pages and in rail mode.
        builder = builder.title_bar_style(tauri::TitleBarStyle::Visible);
    }
    builder.build()?;
    Ok(())
}

/// Anything that is not the hub opens in the default browser. Cloudflare's provider choice is
/// itself a clicked link: the whole authentication redirect chain stays in this web view so the
/// resulting CF_Authorization cookie belongs to the app, not the browser.
fn stays_in_app(opener: &App, url: &url::Url) -> bool {
    let host = connection(opener).map(|c| c.host()).unwrap_or_default();
    let target = url.host_str().unwrap_or("");
    if target.is_empty() || target == host || url.scheme() == "tauri" {
        return true;
    }
    if target.ends_with(".cloudflareaccess.com") || target == "accounts.google.com" {
        return true;
    }
    let _ = opener.opener().open_url(url.as_str(), None::<&str>);
    false
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
    let opener = app.clone();
    let _ = WebviewWindowBuilder::new(app, &label, WebviewUrl::External(url))
        .title("Meeting")
        .inner_size(1100.0, 820.0)
        .min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        .resizable(true)
        .user_agent(USER_AGENT)
        .on_navigation(move |url| stays_in_app(&opener, url))
        .build();
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
  window.ticoNativeFeatures = ['openWindow'];
})();
"#;

fn main_window(app: &App) -> Option<WebviewWindow> {
    app.get_webview_window("main")
}

fn show_window(app: &App) {
    if let Some(window) = main_window(app).or_else(|| app.get_webview_window("server")) {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
    render_tray(app);
}

fn toggle_window(app: &App) {
    if main_window(app).is_none() { let _ = build_server_window(app); return; }
    if let Some(window) = main_window(app) {
        let visible = window.is_visible().unwrap_or(false);
        let focused = window.is_focused().unwrap_or(false);
        if visible && focused {
            let _ = window.hide();
        } else {
            show_window(app);
            return;
        }
    }
    render_tray(app);
}

fn saved_path() -> Option<std::path::PathBuf> {
    let base = dirs_config_dir()?;
    Some(base.join("window.json"))
}

fn dirs_config_dir() -> Option<std::path::PathBuf> {
    let home = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE"))?;
    let name = option_env!("TICO_ENV_SLUG").filter(|s| !s.is_empty()).unwrap_or("tico");
    let dir = std::path::PathBuf::from(home).join(".config").join("tico").join("app").join(name);
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir)
}

fn load_saved() -> Saved {
    saved_path().and_then(|p| std::fs::read_to_string(p).ok()).and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}

fn save(saved: &Saved) {
    if let Some(path) = saved_path() {
        let _ = std::fs::write(path, serde_json::to_string(saved).unwrap_or_default());
    }
}

fn remember_full_frame(app: &App) {
    let state: State<AppState> = app.state();
    if *state.mode.lock().unwrap() != WindowMode::Full || state.applying_frame.load(Ordering::Relaxed) {
        return;
    }
    let Some(window) = main_window(app) else { return };
    let (Ok(position), Ok(size)) = (window.outer_position(), window.outer_size()) else { return };
    if size.width < MIN_WIDTH || size.height < MIN_HEIGHT {
        return;
    }
    let mut saved = load_saved();
    saved.full = Some((position.x, position.y, size.width, size.height));
    saved.mode = Some(WindowMode::Full);
    save(&saved);
}

fn set_outer_size(window: &WebviewWindow, width: u32, height: u32) -> tauri::Result<()> {
    // Saved frames and monitor work areas include the title bar; set_size takes content size.
    let outer = window.outer_size()?;
    let inner = window.inner_size()?;
    window.set_size(PhysicalSize::new(
        width.saturating_sub(outer.width.saturating_sub(inner.width)).max(1),
        height.saturating_sub(outer.height.saturating_sub(inner.height)).max(1),
    ))
}

fn apply_window_mode(app: &App, _animated: bool) {
    let state: State<AppState> = app.state();
    let Some(window) = main_window(app) else { return };
    let mode = *state.mode.lock().unwrap();
    state.applying_frame.store(true, Ordering::Relaxed);
    match mode {
        WindowMode::Rail => {
            if let Ok(Some(monitor)) = window.current_monitor() {
                let area = monitor.work_area();
                // Match the logical minimum width on Retina displays before positioning in pixels.
                let rail = LogicalSize::new(RAIL_WIDTH.max(MIN_WIDTH), 0)
                    .to_physical::<u32>(monitor.scale_factor());
                let width = rail.width.min(area.size.width);
                let _ = set_outer_size(&window, width, area.size.height);
                let _ = window.set_position(PhysicalPosition::new(area.position.x + area.size.width as i32 - width as i32, area.position.y));
            }
        }
        WindowMode::Full => {
            let saved = load_saved();
            if let Some((x, y, w, h)) = saved.full {
                let _ = set_outer_size(&window, w, h);
                let _ = window.set_position(PhysicalPosition::new(x, y));
            } else {
                let _ = window.set_size(LogicalSize::new(1240.0, 820.0));
                let _ = window.center();
            }
        }
    }
    let mut saved = load_saved();
    saved.mode = Some(mode);
    save(&saved);
    state.applying_frame.store(false, Ordering::Relaxed);
    sync_window_mode(app);
    render_tray(app);
}

fn toggle_window_mode(app: &App) {
    let state: State<AppState> = app.state();
    // This helper reads mode too, so capture the full frame before taking the mode lock.
    remember_full_frame(app);
    {
        let mut mode = state.mode.lock().unwrap();
        *mode = match *mode {
            WindowMode::Full => WindowMode::Rail,
            WindowMode::Rail => WindowMode::Full,
        };
    }
    apply_window_mode(app, true);
}

// Search from the View menu (⌘K) or the tray: bring the window forward, then the page opens
// its own search dialog (`window.ticoSearch` in ui/app/search.js). The menu accelerator takes
// ⌘K before the webview sees it, so the page's own shortcut still works everywhere else.
fn open_search(app: &App) {
    show_window(app);
    eval(app, "window.ticoSearch && window.ticoSearch()");
}

fn sync_window_mode(app: &App) {
    let state: State<AppState> = app.state();
    let mode = match *state.mode.lock().unwrap() { WindowMode::Full => "full", WindowMode::Rail => "rail" };
    eval(app, &format!("window.ticoWindowModeChanged && window.ticoWindowModeChanged('{mode}')"));
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
/// What the page posts: `windowMode` ("toggle" | "state"), `openWindow` ({meeting: id}).
#[tauri::command]
fn bridge(app: App, window: WebviewWindow, name: String, body: Option<Value>) {
    // Generic builds accept any server; only the selected origin may call the native bridge.
    let Some(config) = connection(&app) else { return };
    if window.label() != "main" || window.url().map(|u| u.origin() != config.hub.origin()).unwrap_or(true) { return; }
    match name.as_str() {
        "windowMode" => {
            if body.as_ref().and_then(Value::as_str) == Some("toggle") { toggle_window_mode(&app) } else { sync_window_mode(&app) }
        }
        "openWindow" => {
            // `recording` is what an older page called it.
            let id = body.as_ref().and_then(|b| b.get("meeting").or_else(|| b.get("recording"))).and_then(Value::as_str);
            if let Some(id) = id {
                open_meeting_window(&app, id);
            }
        }
        _ => {}
    }
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
        &MenuItem::with_id(app, "mode", "Toggle Right Rail", true, Some("CmdOrCtrl+\\"))?,
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
        "mode" => toggle_window_mode(app),
        "toggle" => toggle_window(app),
        "quit" => app.exit(0),
        _ => {}
    }
}

// ----------------------------------------------------------------------------- tray
fn build_tray(app: &App) -> tauri::Result<()> {
    let headline = MenuItem::with_id(app, "headline", "", false, None::<&str>)?;
    let counts = MenuItem::with_id(app, "counts", "", false, None::<&str>)?;
    let toggle = MenuItem::with_id(app, "toggle", "Hide window", true, None::<&str>)?;
    let mode = MenuItem::with_id(app, "mode", "Move to right rail", true, None::<&str>)?;
    let search = MenuItem::with_id(app, "search", "Search…", true, None::<&str>)?;
    let reload = MenuItem::with_id(app, "reload", "Reload", true, None::<&str>)?;
    let state: State<AppState> = app.state();
    let quit = MenuItem::with_id(app, "quit", format!("Quit {}", config::app_name()), true, None::<&str>)?;
    let menu = Menu::with_items(app, &[
        &headline, &counts, &PredefinedMenuItem::separator(app)?,
        &toggle, &mode, &search, &reload,
    ])?;
    if config::is_generic() {
        menu.append(&MenuItem::with_id(app, "server", "Change server…", true, None::<&str>)?)?;
    }
    menu.append_items(&[&PredefinedMenuItem::separator(app)?, &quit])?;
    let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png"))?;
    let builder = TrayIconBuilder::with_id("tico")
        .icon(icon)
        .icon_as_template(true)
        .tooltip(config::app_name())
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                toggle_window(tray.app_handle());
            }
        });
    // Native text and the template mark follow macOS light/dark appearance together.
    #[cfg(target_os = "macos")]
    let builder = match identity::tray_label(config::app_name(),
        option_env!("TICO_ENV_SLUG").unwrap_or(""), option_env!("TICO_TRAY_LABEL").unwrap_or("")) {
        Some(label) => builder.title(label),
        None => builder,
    };
    let tray = builder.build(app)?;
    *state.tray.lock().unwrap() = Some(TrayItems { tray, headline, counts, toggle, mode });
    render_tray_now(app);
    Ok(())
}

/// Status updates keep the company label; state lives in the tooltip and menu.
fn render_tray(app: &App) {
    // Always on the main thread: window questions round-trip there, and a lock held while
    // asking one would wait on itself.
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || render_tray_now(&handle));
}

fn render_tray_now(app: &App) {
    let state: State<AppState> = app.state();
    let status = state.status.lock().unwrap().clone();
    let visible = main_window(app).and_then(|w| w.is_visible().ok()).unwrap_or(false);
    let rail = *state.mode.lock().unwrap() == WindowMode::Rail;
    let guard = state.tray.lock().unwrap();
    let Some(items) = guard.as_ref() else { return };
    let name = config::app_name();
    let _ = items.toggle.set_text(if visible { "Hide window" } else { "Show window" });
    let _ = items.mode.set_text(if rail { "Use full window" } else { "Move to right rail" });
    let _ = items.headline.set_text(status.headline());
    let _ = items.counts.set_text(format!("{} running · {} need you", status.running, status.needs));
    let _ = items.tray.set_tooltip(Some(format!("{name} · {} · {} running · {} need you", status.headline(), status.running, status.needs)));
}

// ----------------------------------------------------------------------------- updates
// The page comes from the hub, so the site changes with no app update at all. The shell itself
// changes rarely; when it does, the generic app fetches a signed GitHub release, installs
// it and relaunches automatically. Per-environment builds retain their hub updater.
fn start_updates(app: App) {
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_secs(20)).await;
        loop {
            check_for_update(&app).await;
            tokio::time::sleep(Duration::from_secs(6 * 60 * 60)).await;
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

async fn check_for_update(app: &App) {
    use tauri_plugin_updater::UpdaterExt;
    let selected = connection(app);
    if !config::is_generic() && selected.as_ref().map(|c| c.is_local()).unwrap_or(true) { return; }
    let builder = app.updater_builder();
    let company_policy = if config::is_generic() { None } else {
        let Some(connection) = selected.as_ref() else { return };
        if connection.hub.scheme() != "https" {
            log::warn!("company updater requires an HTTPS hub origin");
            return;
        }
        let configured = app.config().plugins.0.get("updater").and_then(|p| p.get("endpoints"))
            .and_then(Value::as_array).and_then(|a| a.first()).and_then(Value::as_str);
        let endpoint = match hub::company_update_endpoint(&connection.hub, configured) {
            Ok(endpoint) if endpoint.scheme() == "https" => endpoint,
            Ok(_) => { log::warn!("company updater requires an HTTPS feed"); return; }
            Err(_) => { log::warn!("company updater feed is invalid"); return; }
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
        Err(e) => { log::debug!("updater unavailable: {e}"); return; }
    };
    let update = match updater.check().await {
        Ok(Some(update)) => update,
        Ok(None) => return,
        Err(e) => { log::debug!("update check: {e}"); return; }
    };
    if let Some(policy) = company_policy.as_ref() {
        if update.download_url.scheme() != "https" || !policy.allows_download(&update.download_url) {
            log::warn!("company updater artifact is outside the allowed update origin");
            return;
        }
    }
    log::info!("update {} available; installing", update.version);
    if let Err(e) = update.download_and_install(|_, _| {}, || {}).await {
        log::warn!("update failed: {e}");
        return;
    }
    app.restart();
}

// ----------------------------------------------------------------------------- polling
// Status every 30 s, for the tray.
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
    render_tray(app);
}

#[cfg(test)]
mod updater_integration_tests {
    use super::*;
    use minisign::{KeyPair, sign};
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
        (public_key, signature)
    }

    fn test_app() -> tauri::App<tauri::test::MockRuntime> {
        tauri::test::mock_builder()
            .plugin(tauri_plugin_updater::Builder::new().build())
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
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
