// Tico: a thin native shell around the local or cloud hub.
// A bundled first-run page selects the server, then one ordinary window shows the hub's own page. No
// menu-bar item: on macOS the Dock icon reopens a hidden window and carries the Needs you count. One
// source builds every company's app on macOS, Windows and Linux: the name, icon, identifier and server
// URL come in at build time (scripts/app.sh).
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

mod applog;
mod config;
mod hub;
mod orgs;
mod visual_origin;
mod bridge;
mod windows;


use std::sync::atomic::AtomicBool;
use std::sync::Mutex;
use std::time::Duration;

use serde_json::Value;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Manager, RunEvent, State, WebviewWindow, WindowEvent};

use bridge::{app_log, bridge};
use config::Config;
use hub::{Client, HubStatus};
use windows::{build_server_window, build_window, eval, main_window, open_search, remember_frame, reload, restore_frame, server_capability, show_window};

const USER_AGENT: &str = "TicoHub/2.0 Tauri";
const MIN_WIDTH: u32 = 360;
const MIN_HEIGHT: u32 = 400;
// Lines of the shell's log a Support diagnostics bundle carries.
pub(crate) const LOG_LINES: usize = 200;

struct AppState {
    connection: Mutex<Option<Config>>,
    visual_origin: Mutex<Option<visual_origin::VisualOrigin>>,
    status: Mutex<HubStatus>,
    // Set while the shell itself places the window, so that placement is not saved as the person's.
    applying_frame: AtomicBool,
}

type App = AppHandle<tauri::Wry>;

fn main() {
    applog::init();
    let state = AppState {
        connection: Mutex::new(None),
        visual_origin: Mutex::new(None),
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
            select_connection(&state, config);
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

fn select_connection(state: &AppState, config: Option<Config>) {
    // Bridge updates and navigation use this same lock order, so an old Hub cannot restore trust
    // while its connection is being replaced. No visual origin survives a connection selection.
    let mut connection = state.connection.lock().unwrap();
    let mut visual = state.visual_origin.lock().unwrap();
    *connection = config;
    *visual = None;
}

fn log_dir(app: &App) -> Option<std::path::PathBuf> {
    app.path().app_log_dir().ok()
}

fn server_path(app: &App) -> tauri::Result<std::path::PathBuf> {
    Ok(app.path().app_config_dir()?.join("server.txt"))
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
    select_connection(&app.state::<AppState>(), None);
    app.request_restart();
    Ok(())
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
pub(crate) fn update_now(app: &App) {
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
    match install_update(&updater, company_policy.as_ref(), |version| report_update(app, "installing", version)).await {
        Ok(false) => report_update(app, "current", ""),
        Ok(true) => app.restart(),
        Err(detail) => report_update(app, "failed", &detail),
    }
}

/// Reads the feed, then downloads, verifies and installs a newer version: true when one was installed.
/// Separate from the check so the updater test runs this same path against a local feed.
async fn install_update(
    updater: &tauri_plugin_updater::Updater,
    company_policy: Option<&hub::CompanyUpdatePolicy>,
    installing: impl FnOnce(&str),
) -> Result<bool, String> {
    let update = match updater.check().await {
        Ok(Some(update)) => update,
        Ok(None) => return Ok(false),
        Err(e) => return Err(format!("could not read the update feed: {e}")),
    };
    if let Some(policy) = company_policy {
        // Tests serve the feed on loopback HTTP; a built app downloads over HTTPS only.
        let secure = update.download_url.scheme() == "https"
            || cfg!(test) && update.download_url.host_str() == Some("127.0.0.1");
        if !secure || !policy.allows_download(&update.download_url) {
            return Err("the update file is outside the allowed address".into());
        }
    }
    installing(&update.version);
    update.download_and_install(|_, _| {}, || {}).await.map_err(|e| format!("install failed: {e}"))?;
    Ok(true)
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

    fn new_hub_policy(address: SocketAddr) -> hub::CompanyUpdatePolicy {
        let hub = url(address, "/");
        let endpoint = hub.join("download/latest.json").unwrap();
        hub::CompanyUpdatePolicy::new(hub, endpoint, Some(COOKIE))
    }

    /// A Linux company app updating from what a release publishes: app/tests/update_hub.py publishes a signed
    /// candidate AppImage with scripts/app_release.py and serves it through the server's own download routes behind
    /// an Access gate. The app finds the update only with the hub session, downloads it through the server's
    /// presigned storage redirect and installs it over the running AppImage, and refuses it under another updater key.
    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    #[tokio::test]
    async fn company_app_finds_authenticates_downloads_verifies_and_installs_a_published_update() {
        use std::io::BufRead;
        use std::os::unix::fs::PermissionsExt;
        const CANDIDATE: &str = "0.3.99";
        let scratch = std::env::temp_dir().join(format!("tico-app-update-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&scratch);
        let (bundles, installed_dir) = (scratch.join("bundles"), scratch.join("installed"));
        std::fs::create_dir_all(&bundles).unwrap();
        std::fs::create_dir_all(&installed_dir).unwrap();
        let candidate = b"#!/bin/sh\necho Acme Tico 0.3.99\n";
        let (public_key, signature) = key_and_signature(candidate);
        let (other_key, _) = key_and_signature(candidate);
        // The file names `cargo tauri build --bundles appimage` writes, which app_release.py classifies.
        let appimage = bundles.join(format!("Acme Tico_{CANDIDATE}_amd64.AppImage"));
        std::fs::write(&appimage, candidate).unwrap();
        std::fs::write(bundles.join(format!("Acme Tico_{CANDIDATE}_amd64.AppImage.sig")), &signature).unwrap();
        let installed = installed_dir.join("Acme Tico.AppImage");
        let previous = b"#!/bin/sh\necho Acme Tico 0.3.98\n";
        std::fs::write(&installed, previous).unwrap();
        std::fs::set_permissions(&installed, std::fs::Permissions::from_mode(0o755)).unwrap();

        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let python = std::env::var("TICO_PYTHON").unwrap_or_else(|_| "python3".into());
        let mut feed = std::process::Command::new(python)
            .arg(root.join("app/tests/update_hub.py")).arg(&bundles).arg(CANDIDATE).arg(COOKIE)
            .stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped())
            .spawn().expect("start app/tests/update_hub.py");
        let mut ready = String::new();
        std::io::BufReader::new(feed.stdout.take().unwrap()).read_line(&mut ready).unwrap();
        let ready: serde_json::Value = serde_json::from_str(&ready).expect("update_hub.py did not start");
        let hub = Url::parse(ready["hub"].as_str().unwrap()).unwrap();
        // A company build's configured endpoint: <url>/download/latest.json on the hub it connects to.
        let configured = hub.join("download/latest.json").unwrap();
        let endpoint = hub::company_update_endpoint(&hub, Some(configured.as_str())).unwrap();

        let updater = |key: &str, cookie: Option<&str>| {
            let mut context = tauri::test::mock_context(tauri::test::noop_assets());
            context.config_mut().plugins.0.insert("updater".into(),
                serde_json::json!({"pubkey": key, "endpoints": [configured.as_str()],
                                   "dangerousInsecureTransportProtocol": true}));
            context.package_info_mut().version = "0.3.98".parse().unwrap();
            let app = tauri::test::mock_builder()
                .plugin(tauri_plugin_updater::Builder::new().build())
                .build(context).expect("build mock Tauri app");
            let policy = hub::CompanyUpdatePolicy::new(hub.clone(), endpoint.clone(), cookie);
            let updater = configure_company_updater(app.updater_builder(), &policy).unwrap()
                .executable_path(&installed).build().expect("build updater");
            (app, updater, policy)
        };
        async fn run(
            (app, updater, policy): (tauri::App<tauri::test::MockRuntime>, tauri_plugin_updater::Updater,
                                     hub::CompanyUpdatePolicy),
        ) -> (Result<bool, String>, Option<String>) {
            let mut offered = None;
            let result = install_update(&updater, Some(&policy), |version| offered = Some(version.to_string())).await;
            drop(app);
            (result, offered)
        }

        let (signed_out, _) = run(updater(&public_key, None)).await;
        assert!(signed_out.is_err(), "without the hub session the feed is Access's sign-in page: {signed_out:?}");
        let (updated, offered) = run(updater(&public_key, Some(COOKIE))).await;
        assert_eq!(offered.as_deref(), Some(CANDIDATE), "{updated:?}");
        assert_eq!(updated, Ok(true), "the published update did not install");
        assert_eq!(std::fs::read(&installed).unwrap(), candidate);
        assert_eq!(std::fs::metadata(&installed).unwrap().permissions().mode() & 0o777, 0o755);
        // The same download under another updater key: only the signature check can refuse it now.
        std::fs::write(&installed, previous).unwrap();
        let (wrong_key, offered) = run(updater(&other_key, Some(COOKIE))).await;
        assert_eq!(offered.as_deref(), Some(CANDIDATE), "{wrong_key:?}");
        assert!(wrong_key.as_ref().is_err_and(|e| e.starts_with("install failed")),
                "an update signed by another key must not install: {wrong_key:?}");
        assert_eq!(std::fs::read(&installed).unwrap(), previous, "a refused update must leave the app in place");

        drop(feed.stdin.take());
        feed.wait().unwrap();
        let requests: Vec<serde_json::Value> = std::fs::read_to_string(ready["log"].as_str().unwrap()).unwrap()
            .lines().map(|line| serde_json::from_str(line).unwrap()).collect();
        let downloads: Vec<_> = requests.iter()
            .filter(|r| r["service"] == "bucket" && r["method"] == "GET"
                && r["path"].as_str().unwrap().contains("X-Amz-Signature="))
            .collect();
        assert_eq!(downloads.len(), 2, "one download per installing attempt: {requests:?}");
        assert!(downloads.iter().all(|r| r["cookie"] == false), "the hub session reached the bucket: {downloads:?}");
        let _ = std::fs::remove_dir_all(&scratch);
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
