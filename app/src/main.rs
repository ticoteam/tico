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

async fn check_for_update(app: &App) {
    use tauri_plugin_updater::UpdaterExt;
    let selected = connection(app);
    if !config::is_generic() && selected.as_ref().map(|c| c.is_local()).unwrap_or(true) { return; }
    let builder = app.updater_builder();
    let company_endpoint = if config::is_generic() { None } else {
        let Some(connection) = selected.as_ref() else { return };
        Some(connection.hub.join("download/latest.json").unwrap())
    };
    let endpoint = company_endpoint.clone().or_else(|| Some(config::GENERIC_UPDATER.parse().unwrap()));
    let updater_result = if let (Some(connection), Some(endpoint)) = (selected.as_ref(), company_endpoint.as_ref()) {
        // Only the selected hub's Access cookie is attached. The updater's redirect policy is
        // disabled below, and the artifact origin is checked again before its authenticated fetch.
        if connection.hub.scheme() != "https" {
            log::warn!("company updater requires an HTTPS hub origin");
            return;
        }
        let client = Client::new(connection.clone());
        let Some(cookie) = client.cookie_header(app, endpoint) else {
            log::debug!("company updater is waiting for a signed-in hub session");
            return;
        };
        let Some(cookie) = hub::company_cookie_header(&connection.hub, endpoint, &cookie) else {
            log::warn!("company updater endpoint is outside the selected hub origin");
            return;
        };
        let Ok(cookie) = cookie.to_str() else { return };
        builder.endpoints(vec![endpoint.clone()])
            .and_then(|b| b.header("Cookie", cookie))
            .map(|b| b.configure_client(hub::no_redirects))
            .and_then(|b| b.build())
    } else {
        builder.endpoints(vec![endpoint.unwrap()]).and_then(|b| b.build())
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
    if let Some(endpoint) = company_endpoint.as_ref() {
        if !hub::same_origin(endpoint, &update.download_url) {
            log::warn!("company updater artifact is outside the selected hub origin");
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
