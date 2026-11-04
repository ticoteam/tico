// Tico: a thin native shell around the local or cloud hub.
// A bundled first-run page selects the server, then one ordinary window shows the hub's own page. No
// menu-bar item: on macOS the Dock icon reopens a hidden window and carries the Needs you count. One
// source builds every company's app on macOS, Windows and Linux: the name, icon, identifier and server
// URL come in at build time (scripts/app.sh). A generic macOS build keeps a list of servers (orgs): each
// org's page is a child of the main window on its own data store, and a rail on the left switches them.
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

mod applog;
mod config;
mod cookies;
mod hub;
mod orgs;
mod visual_origin;
mod bridge;
mod signin;
mod timing;
mod windows;


use std::collections::{HashMap, HashSet};
use std::sync::Mutex;
use std::time::Duration;

use serde_json::Value;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Emitter, EventTarget, Manager, State, WindowEvent};
use tauri::RunEvent;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};

use bridge::{app_log, bridge};
use config::Config;
use hub::{Client, HubStatus};
use orgs::{Document, Org, Orgs};
#[cfg(target_os = "macos")]
use windows::clear_store;
use windows::{active_webview, build_server_window, build_window, eval, forget_owned, layout, main_window, open_search, org_page, reload, show_window, switch_to, sync_rail, take_owned, RAIL};

const USER_AGENT: &str = "TicoHub/2.0 Tauri";
const MIN_WIDTH: u32 = 360;
const MIN_HEIGHT: u32 = 400;
// Lines of the shell's log a Support diagnostics bundle carries.
pub(crate) const LOG_LINES: usize = 200;

/// The shell's state. When more than one lock is held, they are taken in this order: changing, orgs, prefetch,
/// connection, visual_origin, parked_visual, shown, kept.
struct AppState {
    connection: Mutex<Option<Config>>,
    visual_origin: Mutex<Option<visual_origin::VisualOrigin>>,
    status: Mutex<HubStatus>,
    // The orgs this run shows, and which one is open. The open one's server is `connection`.
    orgs: Mutex<Orgs>,
    // The windows an org opened (hub files, meetings) as (window label, org id): removing the org closes them.
    owned: Mutex<Vec<(String, String)>>,
    // The org capabilities granted this run, by identifier: one per org (see windows::server_capability). Org ids
    // are never reused, so a grant never has to be taken back.
    granted: Mutex<HashSet<String>>,
    // Held while the open org changes or an org goes, so a switch and a removal never interleave. Never taken on
    // the main thread: a switch waits on it for webview work.
    pub(crate) changing: Mutex<()>,
    // The orgs whose pages are open, least recently shown first: the open org's last, and before it those kept
    // hidden since a switch away (`kept`), windows::KEEP_ALIVE in all at most. A prefetched page is not among them.
    shown: Mutex<Vec<String>>,
    // The pages kept hidden after a switch away, each closing windows::KEEP_FOR after it went hidden unless it is
    // shown again first, with the visual allowance each had when open.
    kept: Mutex<windows::Kept>,
    // The page built hidden for the org the pointer rests on in the rail, and the visual allowance it sent while
    // hidden, which becomes the open one when it is shown.
    prefetch: Mutex<windows::Prefetch>,
    parked_visual: Mutex<Option<visual_origin::VisualOrigin>>,
    // Switches being timed, for the log.
    traces: Mutex<Vec<timing::Trace>>,
    // When each org page's webview was built, by its label: its first `bootTiming` report says how long the open
    // took from there (bridge.rs). Taken last: no other lock is taken while it is held.
    page_opened: Mutex<HashMap<String, std::time::Instant>>,
}

impl AppState {
    fn new(orgs: Orgs, connection: Option<Config>) -> AppState {
        AppState {
            connection: Mutex::new(connection),
            visual_origin: Mutex::new(None),
            status: Mutex::new(HubStatus::default()),
            orgs: Mutex::new(orgs),
            owned: Mutex::new(Vec::new()),
            granted: Mutex::new(HashSet::new()),
            changing: Mutex::new(()),
            shown: Mutex::new(Vec::new()),
            kept: Mutex::new(windows::Kept::default()),
            prefetch: Mutex::new(windows::Prefetch::default()),
            parked_visual: Mutex::new(None),
            traces: Mutex::new(Vec::new()),
            page_opened: Mutex::new(HashMap::new()),
        }
    }
}

type App = AppHandle<tauri::Wry>;

/// The autostart entry is named by the bundle identifier, which each environment's build sets (scripts/app.sh).
/// Product names are not unique, and the package name is the same for every build, so two apps would share one entry.
fn autostart_plugin(identifier: String) -> tauri::plugin::TauriPlugin<tauri::Wry> {
    let builder = tauri_plugin_autostart::Builder::new().app_name(identifier);
    #[cfg(target_os = "macos")]
    let builder = builder.macos_launcher(tauri_plugin_autostart::MacosLauncher::LaunchAgent);
    builder.build()
}

fn main() {
    applog::init();
    // Read before the builder: the autostart plugin needs the identifier, which only the context carries.
    let context = tauri::generate_context!();
    let identifier = context.config().identifier.clone();
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
        // The main window's frame between launches. The plugin restores it while the window is built, before the app
        // shows the window, and only the main window is tracked (see windows::tracks_frame).
        .plugin(
            tauri_plugin_window_state::Builder::new()
                .with_state_flags(windows::frame_flags())
                .with_filter(windows::tracks_frame)
                .build(),
        )
        .plugin(autostart_plugin(identifier))
        // The real list is read in setup, once the app's folders are known.
        .manage(AppState::new(Orgs::new(Document::default(), None), None))
        .invoke_handler(tauri::generate_handler![app_log, bridge, bridge::page_dialog, connect_server, server_address, setup_info, cancel_setup, rail_orgs, rail_switch, rail_hover, rail_add, rail_menu, signin::tico_signin])
        .setup(move |app| {
            match applog::attach(log_dir(app.handle()).as_deref()) {
                Some(path) => log::info!("{} {} starting; this log is {}", config::app_name(), app.package_info().version, applog::shown(&path)),
                None => log::warn!("{} {} starting; no log file, so this log goes to stderr only", config::app_name(), app.package_info().version),
            }
            // The orgs this run shows: orgs.json on a build that keeps a list, otherwise the one server the app
            // has always used (server.txt, or the build's own address).
            let file = if orgs::multi_org_build() { Some(app.path().app_config_dir()?.join(orgs::FILE_NAME)) } else { None };
            let legacy = || -> Result<Option<url::Url>, String> {
                let path = server_path(app.handle()).map_err(|e| e.to_string())?;
                Ok(Config::load(&path)?.map(|config| config.hub))
            };
            let found = orgs::startup(file, legacy).map_err(std::io::Error::other)?;
            let connection = found.active().and_then(|org| org.hub()).map(Config::new);
            let state: State<AppState> = app.state();
            *state.orgs.lock().unwrap() = found;
            select_connection(&state, connection);
            // Data stores a run that quit left behind: a sign-in window open at Cmd-Q, or orgs it was adding.
            #[cfg(target_os = "macos")]
            signin::clean_up_last_run(app.handle());
            build_window(app.handle())?;
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
            // Once the org page has painted, so the window never shows blank first.
            windows::show_when_painted(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != "main" {
                // A window an org opened belongs to that org only while it is open.
                if let WindowEvent::Destroyed = event {
                    forget_owned(window.app_handle(), window.label());
                    signin::closed(window.app_handle(), window.label());
                }
                return;
            }
            match event {
                // On macOS closing hides and Quit quits; the Dock icon brings the window back.
                // Elsewhere there is no Dock to reopen it from, so closing closes.
                #[cfg(target_os = "macos")]
                WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    // The open org's next page, after a quit too, opens where this one is.
                    windows::remember_open_place(window.app_handle());
                    let _ = window.hide();
                }
                // A meeting window left open must not keep the app running with no main window.
                #[cfg(not(target_os = "macos"))]
                WindowEvent::Destroyed => window.app_handle().exit(0),
                // The rail and the org's page are placed by the shell, so both follow the window's size.
                WindowEvent::Resized(_) => layout(window.app_handle()),
                _ => {}
            }
        })
        .on_menu_event(|app, event| menu_event(app, event.id().as_ref()))
        .build(context)
        .expect("the app could not start")
        .run(|app, event| {
            // Quitting: the open org's next page, at the next launch, opens where this one is.
            if let RunEvent::Exit = event {
                windows::remember_open_place(app);
            }
            // A click on the Dock icon while the main window is hidden.
            #[cfg(target_os = "macos")]
            if let RunEvent::Reopen { .. } = event {
                if !main_window(app).and_then(|w| w.is_visible().ok()).unwrap_or(false) { show_window(app); }
            }
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

/// The open org, if there is one.
pub(crate) fn active_org(app: &App) -> Option<Org> {
    app.state::<AppState>().orgs.lock().unwrap().active().cloned()
}

/// How many orgs this run shows.
pub(crate) fn org_count(app: &App) -> usize {
    app.state::<AppState>().orgs.lock().unwrap().orgs().len()
}

/// The label of the open org's page, or "" with none (no webview has that label).
fn active_label(app: &App) -> String {
    active_webview(app).map(|page| page.label().to_string()).unwrap_or_default()
}

#[tauri::command]
fn server_address(app: App) -> String {
    connection(&app).map(|c| c.hub.to_string()).unwrap_or_default()
}

#[tauri::command]
async fn connect_server(app: App, address: String) -> Result<(), String> {
    if !config::is_generic() { return Err("This app uses its built-in server.".into()); }
    let hub = config::validate_server(&address).await?;
    if orgs::multi_org_build() {
        // An org already on this server is opened; otherwise the server is added as an org on a store of its own.
        let id = {
            let state = app.state::<AppState>();
            let mut list = state.orgs.lock().unwrap();
            let existing = list.find_hub(&hub).map(|org| org.id.clone());
            let id = match existing {
                Some(id) => id,
                None => list.add(&hub)?.id,
            };
            id
        };
        switch_to(&app, &id).map_err(|e| e.to_string())?;
        // The main window may be hidden (macOS keeps it so when closed): the org must be seen once the setup window goes.
        show_window(&app);
        close_setup_later(&app);
        return Ok(());
    }
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

/// Closes the setup window a moment after it has asked for a server: closing a window from its own call would tear
/// down the page that is still waiting for the answer.
fn close_setup_later(app: &App) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_millis(300)).await;
        if let Some(window) = app.get_webview_window("server") {
            let _ = window.close();
        }
    });
}

// ----------------------------------------------------------------------------- orgs
/// Brings the menu, the rail and the open org's place up to date after the orgs or the open one change. It runs as
/// its own task, because a menu cannot be rebuilt inside the menu event that asked for the change.
pub(crate) fn refresh_chrome(app: &App) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let _ = build_menu(&app);
        let _ = sync_rail(&app);
        layout(&app);
        emit_orgs(&app);
    });
}

/// What the rail shows: each org's id and title, and which one is open. Nothing else of an org leaves the shell.
fn rail_list(app: &App) -> Value {
    let state = app.state::<AppState>();
    let list = state.orgs.lock().unwrap();
    let open = list.active().map(|org| org.id.clone());
    // The rail is the app's own page, so it may show each org's address and email (its initials and color come from
    // them); org pages never receive this list.
    let items: Vec<Value> = list.orgs().iter().map(|org| serde_json::json!({
        "id": org.id,
        "url": org.url,
        "name": org.name,
        "email": org.email,
        "active": open.as_deref() == Some(org.id.as_str()),
    })).collect();
    Value::Array(items)
}

/// Tells the rail that the list changed. The event carries nothing: Tauri also delivers targeted events to listeners
/// on any target, so the rail reads the list itself through `rail_orgs`, which only the rail may call.
fn emit_orgs(app: &App) {
    let _ = app.emit_to(EventTarget::webview(RAIL), "orgs-changed", ());
}

/// The setup window, to add an org: from the rail, the menu, or the bridge. Spawned, as window creation is.
pub(crate) fn add_org(app: &App) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let _ = build_server_window(&app);
    });
}

/// The org menu, at the pointer: the orgs, then add and remove. Spawned, because a popup cannot run inside the call
/// that asked for it. At the pointer, because macOS measures popup points from the frame top, which Tauri can't give.
pub(crate) fn popup_orgs(app: &App) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let (Some(window), Ok(menu)) = (main_window(&app), org_menu(&app)) else { return };
        let _ = window.popup_menu(&menu);
    });
}

fn org_menu(app: &App) -> tauri::Result<Menu<tauri::Wry>> {
    let (list, open) = {
        let state = app.state::<AppState>();
        let orgs = state.orgs.lock().unwrap();
        let open = orgs.active().cloned();
        (orgs.orgs().to_vec(), open)
    };
    let menu = Menu::new(app)?;
    for org in &list {
        let checked = open.as_ref().is_some_and(|open| open.id == org.id);
        menu.append(&CheckMenuItem::with_id(app, format!("org-switch:{}", org.id), org.title(), true, checked, None::<&str>)?)?;
    }
    menu.append(&PredefinedMenuItem::separator(app)?)?;
    menu.append(&MenuItem::with_id(app, "org-add", "Add Organization…", true, None::<&str>)?)?;
    if let Some(open) = &open {
        menu.append(&MenuItem::with_id(app, "org-remove", format!("Remove {}…", open.title()), true, None::<&str>)?)?;
    }
    Ok(menu)
}

/// Asks before an org is removed. Its sign-in and its data on this Mac go with it; the other orgs are not touched.
fn ask_remove(app: &App, org: &Org) {
    let (handle, id) = (app.clone(), org.id.clone());
    app.dialog()
        .message(format!("Remove {}? Its sign-in and its data on this Mac are deleted. Other organizations are not affected.", org.title()))
        .title("Remove organization")
        .buttons(MessageDialogButtons::OkCancelCustom("Remove".into(), "Cancel".into()))
        .show(move |confirmed| {
            if confirmed {
                tauri::async_runtime::spawn(async move { remove_org(&handle, &id).await });
            }
        });
}

/// Removes an org: its windows and page close, its data store is deleted, and the list is saved. Removing the open
/// org opens the first one left, or the setup window when none is left.
async fn remove_org(app: &App, id: &str) {
    // A session being renewed into the org's store finishes first (see signin::RENEWING); one that starts after this
    // finds the org gone.
    let renewing = signin::RENEWING.lock().await;
    let (removed, was_open) = {
        let state = app.state::<AppState>();
        let _changing = state.changing.lock().unwrap();
        // A dialog the org's page has open is answered first.
        bridge::DIALOGS.wait_closed();
        let (removed, was_open) = {
            let mut list = state.orgs.lock().unwrap();
            let was_open = list.active().is_some_and(|org| org.id == id);
            (list.remove(id), was_open)
        };
        for label in take_owned(app, id) {
            if let Some(window) = app.get_webview_window(&label) {
                let _ = window.close();
            }
        }
        windows::forget_page(app, id);
        if let Some(page) = org_page(app, id) {
            let _ = page.close();
        }
        (removed, was_open)
    };
    #[cfg_attr(not(target_os = "macos"), allow(unused_variables))]
    let Some(removed) = removed else { return };
    // Its page is closed. Its store is noted for deletion before anything else, so a deletion that fails (or a quit
    // first) is retried at the next launch; the org is no longer in the list, so nothing keeps the store.
    #[cfg(target_os = "macos")]
    let store = removed.store_id();
    #[cfg(target_os = "macos")]
    if let Some(store) = store {
        signin::note_removed_store(app, store);
    }
    drop(renewing);
    // The next org opens (and the rail drops the removed one) before the store is deleted, which can take seconds.
    if was_open {
        match active_org(app) {
            Some(next) => {
                if let Err(e) = switch_to(app, &next.id) {
                    log::warn!("could not open the next org: {e}");
                }
            }
            None => {
                let state = app.state::<AppState>();
                // Checked again under the lock: an org added meanwhile opens through its own switch, which waits on it.
                let _changing = state.changing.lock().unwrap();
                if active_org(app).is_none() {
                    select_connection(&state, None);
                    *state.status.lock().unwrap() = HubStatus::default();
                    state.shown.lock().unwrap().clear();
                    *state.kept.lock().unwrap() = windows::Kept::default();
                    // The setup window first, so that the app keeps a window while the main one goes.
                    let _ = build_server_window(app);
                    if let Some(window) = main_window(app) {
                        let _ = window.destroy();
                    }
                }
            }
        }
    }
    refresh_chrome(app);
    #[cfg(target_os = "macos")]
    match store {
        // The org moved over from server.txt uses the default store, which cannot be deleted: cleared, so its sign-in
        // goes too. Every other org has a store of its own, so nothing of theirs is in it.
        None => if let Err(e) = clear_store(app, &removed).await { log::warn!("could not clear the default data store: {e}"); },
        Some(store) => if !signin::delete_store(app, store).await {
            // Still in use: its data is cleared at least, and the next launch deletes it.
            log::warn!("could not delete an org's data store, so clearing it now; the next launch deletes it");
            if let Err(e) = clear_store(app, &removed).await { log::warn!("could not clear an org's data store: {e}"); }
        }
    }
}

#[tauri::command]
fn rail_orgs(app: App, webview: tauri::Webview) -> Result<Value, String> {
    only_rail(&webview)?;
    Ok(rail_list(&app))
}

/// A click on an org in the rail, sent for every click (the rail's own idea of the open org can be a draw behind).
/// Not async, so it runs on the main thread in the order the clicks came, and numbers each one there; the switch
/// itself runs apart, and a click that a later one overtook does nothing (`windows::switch_for_click`).
#[tauri::command]
fn rail_switch(app: App, webview: tauri::Webview, id: String) -> Result<(), String> {
    only_rail(&webview)?;
    timing::click(&app, &id, "click");
    let click = windows::next_click();
    tauri::async_runtime::spawn(async move {
        if let Err(e) = windows::switch_for_click(&app, &id, click) {
            log::warn!("could not switch organization: {e}");
        }
    });
    Ok(())
}

/// The pointer rested on an org in the rail (`id`), or left it (none): its page is built hidden ahead of a click.
#[tauri::command]
async fn rail_hover(app: App, webview: tauri::Webview, id: Option<String>) -> Result<(), String> {
    only_rail(&webview)?;
    match id {
        Some(id) => windows::prefetch(&app, &id).map_err(|e| e.to_string()),
        None => Ok(windows::leave(&app)),
    }
}

#[tauri::command]
async fn rail_add(app: App, webview: tauri::Webview) -> Result<(), String> {
    only_rail(&webview)?;
    add_org(&app);
    Ok(())
}

#[tauri::command]
fn rail_menu(app: App, webview: tauri::Webview, id: String) -> Result<(), String> {
    only_rail(&webview)?;
    let Some(org) = app.state::<AppState>().orgs.lock().unwrap().get(&id).cloned() else { return Ok(()) };
    let app = app.clone();
    // One org's menu at the pointer: Remove that org (the rail's own right-click). Spawned, like every popup.
    tauri::async_runtime::spawn(async move {
        let Some(window) = main_window(&app) else { return };
        let Ok(menu) = Menu::with_items(&app, &[
            &match MenuItem::with_id(&app, format!("org-remove:{}", org.id), format!("Remove {}…", org.title()), true, None::<&str>) {
                Ok(item) => item,
                Err(_) => return,
            },
        ]) else { return };
        let _ = window.popup_menu(&menu);
    });
    Ok(())
}

/// The setup page asks whether it is adding an org (one is already open), so it can offer Cancel, and whether Tico
/// sign-in is offered (a build that keeps a list of orgs), so it can make that the default.
#[tauri::command]
fn setup_info(app: App) -> Value {
    let multi = orgs::multi_org_build();
    serde_json::json!({"hasOrgs": multi && org_count(&app) > 0, "signIn": multi})
}

/// Cancel on the setup page while adding an org: the setup window closes, with any Tico sign-in, and the open org stays.
#[tauri::command]
fn cancel_setup(app: App) {
    if org_count(&app) > 0 {
        signin::close(&app);
        close_setup_later(&app);
    }
}

/// The org commands are the rail's alone; the org pages have no use for them.
fn only_rail(webview: &tauri::Webview) -> Result<(), String> {
    if webview.label() == RAIL { Ok(()) } else { Err("Only the org list may call this.".into()) }
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
    if orgs::multi_org_build() {
        app_menu.append(&MenuItem::with_id(app, "org-add", "Add Organization…", true, None::<&str>)?)?;
        if let Some(open) = active_org(app) {
            app_menu.append(&MenuItem::with_id(app, "org-remove", format!("Remove {}…", open.title()), true, None::<&str>)?)?;
        }
    } else if config::is_generic() {
        app_menu.append(&MenuItem::with_id(app, "server", "Change server…", true, None::<&str>)?)?;
    }
    #[cfg(target_os = "macos")]
    app_menu.append(&open_at_login_item(app)?)?;
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
    // Switching runs as its own task: it opens a page, which a menu event should not wait for.
    if let Some(org) = id.strip_prefix("org-remove:") {
        let found = app.state::<AppState>().orgs.lock().unwrap().get(org).cloned();
        if let Some(found) = found { ask_remove(app, &found); }
        return;
    }
    if let Some(org) = id.strip_prefix("org-switch:") {
        let (app, org) = (app.clone(), org.to_string());
        timing::click(&app, &org, "menu");
        tauri::async_runtime::spawn(async move {
            if let Err(e) = switch_to(&app, &org) {
                log::warn!("could not open the org: {e}");
            }
        });
        return;
    }
    match id {
        "server" => { let _ = build_server_window(app); }
        "org-add" => add_org(app),
        "org-remove" => {
            if let Some(open) = active_org(app) {
                ask_remove(app, &open);
            }
        }
        "search" => open_search(app),
        "reload" => reload(app),
        "updates" => update_now(app),
        #[cfg(target_os = "macos")]
        "open-at-login" => toggle_open_at_login(app),
        _ => {}
    }
}

/// Open at Login, checked while the app starts at login. It is off until the person turns it on.
#[cfg(target_os = "macos")]
fn open_at_login_item(app: &App) -> tauri::Result<CheckMenuItem<tauri::Wry>> {
    use tauri_plugin_autostart::ManagerExt;
    let enabled = app.autolaunch().is_enabled().unwrap_or(false);
    CheckMenuItem::with_id(app, "open-at-login", "Open at Login", true, enabled, None::<&str>)
}

/// Turns Open at Login on or off, then rebuilds the menu so its check shows what the system now has. Spawned: a menu
/// cannot be rebuilt inside the menu event that asked for the change.
#[cfg(target_os = "macos")]
fn toggle_open_at_login(app: &App) {
    use tauri_plugin_autostart::ManagerExt;
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let autolaunch = app.autolaunch();
        let result = match autolaunch.is_enabled() {
            Ok(true) => autolaunch.disable(),
            _ => autolaunch.enable(),
        };
        if let Err(e) = result {
            log::warn!("could not change Open at Login: {e}");
        }
        let _ = build_menu(&app);
    });
}

// ----------------------------------------------------------------------------- Dock badge
/// The Needs you count on the Dock icon (macOS), cleared at zero or when the hub cannot be read. Signed out of
/// Cloudflare Access, it shows "!" instead: the count is unknown, and the window says to sign in again. With 2+ orgs
/// there is no one count to show, so the badge is cleared.
fn render_badge(app: &App) {
    let several = !polls_status(org_count(app));
    let HubStatus { needs, signed_out, .. } = app.state::<AppState>().status.lock().unwrap().clone();
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        let Some(window) = main_window(&handle) else { return };
        if several {
            let _ = window.set_badge_count(None);
        } else if signed_out {
            badge_signed_out(&window);
        } else {
            let _ = window.set_badge_count(badge_count(needs));
        }
    });
}

/// Whether the Needs you count is polled. One server has one count, so with 2+ orgs nothing is polled.
fn polls_status(count: usize) -> bool {
    count < 2
}

#[cfg(target_os = "macos")]
fn badge_signed_out(window: &tauri::Window) {
    let _ = window.set_badge_label(Some("!".into()));
}

// A label is macOS only; elsewhere the unknown count is cleared.
#[cfg(not(target_os = "macos"))]
fn badge_signed_out(window: &tauri::Window) {
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

    #[test]
    fn needs_you_is_polled_only_while_there_is_one_org() {
        assert!(super::polls_status(0));
        assert!(super::polls_status(1));
        assert!(!super::polls_status(2));
        assert!(!super::polls_status(3));
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
            Client::new(connection.clone(), active_label(app)).cookie_header(app, &endpoint).await
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
    // With 2+ orgs the Needs you count is off, so nothing is requested from any hub.
    if !polls_status(org_count(app)) {
        render_badge(app);
        return;
    }
    let Some(config) = connection(app) else { return };
    let fresh = Client::new(config.clone(), active_label(app)).status(app).await;
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

    /// Signs `bytes` the way `cargo tauri build` does: the trusted comment records the version, which the updater
    /// (2.14+) checks against the version the feed announces.
    fn key_and_signature(bytes: &[u8], version: &str) -> (String, String) {
        let pair = KeyPair::generate_unencrypted_keypair().unwrap();
        let public_key = pair.pk.to_box().unwrap().to_string();
        let comment = format!("timestamp:0\tfile:Tico.tar.gz\tversion:{version}");
        let signature = sign(
            Some(&pair.pk),
            &pair.sk,
            Cursor::new(bytes.to_vec()),
            Some(&comment),
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
        // The updater installs only ELF AppImages, and `infer` needs a full 64-byte ELF header to call it one.
        let mut candidate = b"\x7fELF Acme Tico 0.3.99".to_vec();
        candidate.resize(64, 0);
        let (public_key, signature) = key_and_signature(&candidate, CANDIDATE);
        let (other_key, _) = key_and_signature(&candidate, CANDIDATE);
        // The file names `cargo tauri build --bundles appimage` writes, which app_release.py classifies.
        let appimage = bundles.join(format!("Acme Tico_{CANDIDATE}_amd64.AppImage"));
        std::fs::write(&appimage, &candidate).unwrap();
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
        let (public_key, signature) = key_and_signature(expected, VERSION);
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
        let (public_key, signature) = key_and_signature(bytes, VERSION);
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
