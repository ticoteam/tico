//! The main window and the web views in it: building them, the sign-in and new-window policy each one
//! follows, the window frame kept between launches, and the page's eval and reload.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::webview::{NewWindowFeatures, NewWindowResponse, PageLoadEvent, PageLoadPayload, Webview, WebviewBuilder};
use tauri::window::{Window, WindowBuilder};
use tauri::{LogicalPosition, LogicalSize, Manager, PhysicalPosition, PhysicalSize, Rect, State, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;
use uuid::Uuid;

use crate::bridge::bridge_script;
use crate::config::{self, Config};
use crate::orgs::Org;
use crate::visual_origin;
use crate::{active_org, connection, refresh_chrome, select_connection, App, AppState, MIN_HEIGHT, MIN_WIDTH, USER_AGENT};

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

pub(crate) fn build_server_window(app: &App) -> tauri::Result<()> {
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

/// The runtime capability for one org's page. It names that page's webview and no window: a window grant would
/// match every child webview in "main", so one org's grant would reach the others. The remote URL confines it to
/// the org's own origin.
pub(crate) fn server_capability(hub: &url::Url, label: &str) -> Value {
    let mut capability: Value = serde_json::from_str(include_str!("../capabilities/main.json")).unwrap();
    capability["identifier"] = Value::from(label);
    capability["windows"] = serde_json::json!([]);
    capability["webviews"] = serde_json::json!([label]);
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

    const ORG: &str = "org-o_7f3a0001";

    #[test]
    fn ipc_is_confined_to_selected_origin_and_its_own_webview() {
        for (address, pattern) in [
            ("https://team.example.com/tico/", "https://team.example.com:443/*"),
            ("http://localhost:8765/", "http://localhost:8765/*"),
            ("http://team.example.com/", "http://team.example.com:80/*"),
            ("https://[::1]:8765/", r"https://[\:\:1]:8765/*"),
            ("https://*.example.com/", r"https://\*.example.com:443/*"),
        ] {
            let capability = server_capability(&url::Url::parse(address).unwrap(), ORG);
            assert_eq!(capability["remote"]["urls"], serde_json::json!([pattern]));
            let matcher: tauri::utils::acl::RemoteUrlPattern = pattern.parse().unwrap();
            assert!(matcher.test(&url::Url::parse(address).unwrap()));
            for other in ["https://accounts.google.com/", "https://team.cloudflareaccess.com/",
                          "https://other.example.com/", "https://team.example.com:8443/",
                          "http://team.example.com:8080/", "http://localhost:8766/",
                          "https://team.files.example.com/visuals/payload.mac", "https://team.example.com.evil.example/",
                          "https://team.example.com@evil.example/", "about:blank", "data:text/html,hi",
                          "blob:https://team.example.com/opaque"] {
                assert!(!matcher.test(&url::Url::parse(other).unwrap()), "{pattern} accepted {other}");
            }
            assert_eq!(capability["identifier"], ORG);
            assert_eq!(capability["windows"], serde_json::json!([]));
            assert_eq!(capability["webviews"], serde_json::json!([ORG]));
            assert_eq!(capability["local"], false);
        }
        let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        assert_eq!(config["app"]["security"]["capabilities"], serde_json::json!(["server", "rail"]));
    }
    #[test]
    fn two_orgs_pages_cannot_reach_each_other() {
        let (acme, tidy) = (url::Url::parse("https://acme.example.com/").unwrap(), url::Url::parse("https://tidy.example.com/").unwrap());
        let (a, b) = (server_capability(&acme, "org-o_aaaaaaaa"), server_capability(&tidy, "org-o_bbbbbbbb"));
        assert_eq!(a["webviews"], serde_json::json!(["org-o_aaaaaaaa"]));
        assert_eq!(b["webviews"], serde_json::json!(["org-o_bbbbbbbb"]));
        let own: tauri::utils::acl::RemoteUrlPattern = a["remote"]["urls"][0].as_str().unwrap().parse().unwrap();
        let other: tauri::utils::acl::RemoteUrlPattern = b["remote"]["urls"][0].as_str().unwrap().parse().unwrap();
        assert!(own.test(&acme) && !own.test(&tidy));
        assert!(other.test(&tidy) && !other.test(&acme));
    }

    #[test]
    fn the_rail_capability_names_only_the_rail_and_its_own_commands() {
        let rail: Value = serde_json::from_str(include_str!("../capabilities/rail.json")).unwrap();
        assert_eq!(rail["webviews"], serde_json::json!(["rail"]));
        assert_eq!(rail["windows"], serde_json::json!([]));
        assert_eq!(rail["local"], true);
        let permissions = rail["permissions"].as_array().unwrap();
        for permission in permissions {
            let permission = permission.as_str().unwrap();
            assert!(!permission.contains("bridge") && !permission.contains("app-log") && !permission.contains("dialog"), "{permission}");
        }
    }
}

// ----------------------------------------------------------------------------- window
pub(crate) fn build_window(app: &App) -> tauri::Result<()> {
    let Some(org) = active_org(app) else { return build_server_window(app) };
    if main_window(app).is_none() {
        build_main(app)?;
        restore_frame(app);
    }
    if active_webview(app).is_none() {
        open_org_webview(app, &org)?;
    }
    sync_rail(app)?;
    layout(app);
    Ok(())
}

/// The window itself, hidden until its frame is restored. Its content is child webviews, placed by `layout`.
fn build_main(app: &App) -> tauri::Result<Window> {
    let builder = WindowBuilder::new(app, "main")
        .title(config::app_name())
        .inner_size(1240.0, 820.0)
        .min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        .visible(false);
    // The native title bar drags the window on every page, including sign-in pages that are
    // not ours; an overlay title bar would leave dragging to each page.
    #[cfg(target_os = "macos")]
    let builder = builder.title_bar_style(tauri::TitleBarStyle::Visible);
    builder.build()
}

/// The label of an org's page webview. Ids are never reused, so a label never names two orgs.
pub(crate) fn org_label(id: &str) -> String {
    format!("org-{id}")
}

/// The open org's page, if its webview is open.
pub(crate) fn active_webview(app: &App) -> Option<Webview> {
    let org = active_org(app)?;
    app.get_webview(&org_label(&org.id))
}

/// The org list down the left of the window, shown with 2+ orgs.
pub(crate) const RAIL: &str = "rail";
pub(crate) const RAIL_WIDTH: f64 = 60.0;

/// The org a window belongs to: its id, the address its navigation follows, and its own data store (macOS 14+).
#[derive(Clone, Debug)]
pub(crate) struct Home {
    pub id: String,
    pub hub: url::Url,
    pub store: Option<Uuid>,
}

impl Home {
    pub(crate) fn of(org: &Org) -> Option<Home> {
        Some(Home { id: org.id.clone(), hub: org.hub()?, store: org.store_id() })
    }
}

fn bounds(x: f64, y: f64, width: f64, height: f64) -> Rect {
    Rect { position: LogicalPosition::new(x, y).into(), size: LogicalSize::new(width, height).into() }
}

/// Places the rail and the open org's page side by side, filling the window. Logical units throughout.
pub(crate) fn layout(app: &App) {
    let Some(window) = main_window(app) else { return };
    let (Ok(size), Ok(scale)) = (window.inner_size(), window.scale_factor()) else { return };
    let (width, height) = (size.width as f64 / scale, size.height as f64 / scale);
    let rail = app.get_webview(RAIL);
    let left = if rail.is_some() { RAIL_WIDTH } else { 0.0 };
    if let Some(rail) = rail {
        let _ = rail.set_bounds(bounds(0.0, 0.0, RAIL_WIDTH, height));
    }
    if let Some(page) = active_webview(app) {
        let _ = page.set_bounds(bounds(left, 0.0, (width - left).max(1.0), height));
    }
}

/// Shows the rail with 2+ orgs and removes it below that. Callers run `layout` afterwards.
pub(crate) fn sync_rail(app: &App) -> tauri::Result<()> {
    let several = app.state::<AppState>().orgs.lock().unwrap().orgs().len() >= 2;
    match (several, app.get_webview(RAIL)) {
        (true, None) => {
            if let Some(window) = main_window(app) {
                let builder = WebviewBuilder::new(RAIL, WebviewUrl::App("rail.html".into()))
                    .on_navigation(|url| local_page(url));
                window.add_child(builder, LogicalPosition::new(0.0, 0.0), LogicalSize::new(RAIL_WIDTH, 820.0))?;
            }
        }
        (false, Some(rail)) => rail.close()?,
        _ => {}
    }
    Ok(())
}

/// The app's own pages: `tauri:` and the local page server.
pub(crate) fn local_page(url: &url::Url) -> bool {
    url.scheme() == "tauri" || matches!(url.host_str(), Some("tauri.localhost") | Some("localhost"))
}

/// Opens an org's page as a child of the main window, on the org's own data store (macOS 14+).
fn open_org_webview(app: &App, org: &Org) -> tauri::Result<()> {
    let (Some(window), Some(home)) = (main_window(app), Home::of(org)) else { return Ok(()) };
    grant_org(app, &home)?;
    let config = Config::new(home.hub.clone());
    let (navigate, page) = sign_in_hooks(app, Some(home.hub.clone()), true);
    let mut builder = WebviewBuilder::new(org_label(&home.id), WebviewUrl::External(config.start_url()))
        .user_agent(USER_AGENT)
        // Files dropped on a page reach it (chat composers, task files): Tauri's own drop handler would swallow them on macOS.
        .disable_drag_drop_handler()
        .devtools(true)
        .initialization_script(&bridge_script(app))
        .on_new_window(new_window_handler(app, home.clone()))
        .on_navigation(navigate)
        .on_page_load(move |_, payload| page(payload));
    #[cfg(target_os = "macos")]
    {
        if let Some(store) = home.store {
            builder = builder.data_store_identifier(*store.as_bytes());
        }
    }
    window.add_child(builder, LogicalPosition::new(0.0, 0.0), LogicalSize::new(1240.0, 820.0))?;
    Ok(())
}

/// Adds the page capability for an org's webview, once per org id. Ids are never reused, so a removed org's grant
/// cannot match a live webview.
fn grant_org(app: &App, home: &Home) -> tauri::Result<()> {
    let state = app.state::<AppState>();
    let mut granted = state.granted.lock().unwrap();
    if !granted.contains(&home.id) {
        app.add_capability(server_capability(&home.hub, &org_label(&home.id)).to_string())?;
        granted.insert(home.id.clone());
    }
    Ok(())
}

/// Makes another org the open one: its page replaces the old one in the main window. The old page closes first,
/// so the two never run at once. No restart and no new sign-in: the org's own store keeps its sign-in.
pub(crate) fn switch_to(app: &App, id: &str) -> tauri::Result<()> {
    let state = app.state::<AppState>();
    let org = {
        let mut orgs = state.orgs.lock().unwrap();
        if orgs.get(id).is_none() {
            return Ok(());
        }
        orgs.set_active(id);
        orgs.active().cloned()
    };
    let Some(org) = org else { return Ok(()) };
    if app.get_webview(&org_label(&org.id)).is_some() {
        refresh_chrome(app);
        return Ok(());
    }
    let Some(home) = Home::of(&org) else { return Ok(()) };
    // The connection follows the open org before its page opens, and the status starts over.
    select_connection(&state, Some(Config::new(home.hub.clone())));
    *state.status.lock().unwrap() = crate::hub::HubStatus::default();
    if main_window(app).is_none() {
        build_window(app)?;
        show_window(app);
    } else {
        for (label, webview) in app.webviews() {
            if label.starts_with("org-") {
                let _ = webview.close();
            }
        }
        open_org_webview(app, &org)?;
        layout(app);
    }
    refresh_chrome(app);
    Ok(())
}

/// Clears one org's cookies and storage when its page is not open: through a hidden 1x1 webview on its store.
pub(crate) fn clear_store(app: &App, org: &Org) -> tauri::Result<()> {
    let (Some(window), Some(home)) = (main_window(app), Home::of(org)) else { return Ok(()) };
    let mut builder = WebviewBuilder::new(format!("clear-{}", home.id), WebviewUrl::External("about:blank".parse().unwrap()));
    #[cfg(target_os = "macos")]
    {
        if let Some(store) = home.store {
            builder = builder.data_store_identifier(*store.as_bytes());
        }
    }
    let webview = window.add_child(builder, LogicalPosition::new(0.0, 0.0), LogicalSize::new(1.0, 1.0))?;
    let cleared = webview.clear_all_browsing_data();
    let _ = webview.close();
    cleared
}

/// Anything that is not the hub opens in the default browser, except during a sign-in: from the hub's
/// own Cloudflare Access login, or the hub's own sign-in, the whole redirect chain stays in the window
/// whatever identity provider it passes through, so the resulting cookie belongs to the app, not the
/// browser. Each webview keeps its own `signing_in` and its own hub, so a window opened for one org keeps
/// following that org after the person switches to another. Embedded frames navigate through the same
/// handler with nothing to tell them apart, so only the top-level page ends a sign-in, by committing on the
/// hub origin; and only the hub's own logins start one.
fn sign_in_hooks(app: &App, hub: Option<url::Url>, visuals: bool) -> (impl Fn(&url::Url) -> bool + Send + 'static, impl Fn(PageLoadPayload<'_>) + Send + Sync + 'static) {
    let signing_in = Arc::new(AtomicBool::new(false));
    let (opener, page_app) = (app.clone(), app.clone());
    let (committed, page_hub) = (signing_in.clone(), hub.clone());
    let navigate = move |url: &url::Url| stays_in_app(&opener, &signing_in, visuals, hub.as_ref(), url);
    // `Started` is the top-level page committing, with its address; frames never send it.
    let page = move |payload: PageLoadPayload<'_>| {
        if matches!(payload.event(), PageLoadEvent::Started) {
            top_level_window(&page_app.state::<AppState>(), &committed, visuals, page_hub.as_ref(), payload.url());
        }
    };
    (navigate, page)
}

fn stays_in_app(opener: &App, signing_in: &AtomicBool, visuals: bool, hub: Option<&url::Url>, url: &url::Url) -> bool {
    let stays = follow_window(&opener.state::<AppState>(), signing_in, visuals, hub, url);
    if !stays {
        let _ = opener.opener().open_url(url.as_str(), None::<&str>);
    }
    stays
}

pub(crate) fn follow_window(state: &AppState, signing_in: &AtomicBool, visuals: bool, hub: Option<&url::Url>, url: &url::Url) -> bool {
    let mut visual = state.visual_origin.lock().unwrap();
    let allowed = if visuals { visual.as_ref() } else { None };
    let stays = follow(hub, allowed, signing_in, url);
    if visuals && (navigation(hub, false, url) == Navigation::Start || session_exit(hub, url)) {
        *visual = None;
    }
    stays
}

pub(crate) fn top_level_window(state: &AppState, signing_in: &AtomicBool, visuals: bool, hub: Option<&url::Url>, url: &url::Url) {
    if visuals {
        let mut visual = state.visual_origin.lock().unwrap();
        // A new top-level Hub page must authenticate and send its config again. A permitted
        // visual commit changes neither its allowance nor sign-in state.
        if !visual.as_ref().is_some_and(|visual| visual.permits(hub, url)) {
            *visual = None;
        }
    }
    top_level_page(hub, signing_in, url);
}

/// Whether the window follows a navigation, from any frame; a sign-in's start sets `signing_in`.
fn follow(hub: Option<&url::Url>, visual: Option<&visual_origin::VisualOrigin>, signing_in: &AtomicBool, url: &url::Url) -> bool {
    let destination = if visual.is_some_and(|visual| visual.permits(hub, url)) { Navigation::Stay }
        else { navigation(hub, signing_in.load(Ordering::Relaxed), url) };
    match destination {
        Navigation::Start => signing_in.store(true, Ordering::Relaxed),
        Navigation::Stay => {}
        Navigation::Browser => return false,
    }
    true
}

pub(crate) fn session_exit(hub: Option<&url::Url>, url: &url::Url) -> bool {
    let own = hub.is_some_and(|hub| url.origin() == hub.origin());
    (own && (url.path().ends_with("/api/v2/logout") || url.path().ends_with("/auth/logout")))
        || ((own || url.host_str().is_some_and(|host| host.ends_with(".cloudflareaccess.com")))
            && url.path().ends_with("/cdn-cgi/access/logout"))
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

/// Where a page's new window goes: a hub file opens in a window of its own, on the page's org; other links open in
/// the default browser.
fn new_window_handler(app: &App, home: Home) -> impl Fn(url::Url, NewWindowFeatures) -> NewWindowResponse<tauri::Wry> + Send + 'static {
    let opener = app.clone();
    move |url, _| {
        match new_window_target(Some(&home.hub), &url) {
            NewWindow::Hub => {
                // Built off the web view's own callback, which may not block on a new window.
                let (app, home) = (opener.clone(), home.clone());
                tauri::async_runtime::spawn(async move { open_hub_window(&app, url, &home) });
            }
            NewWindow::Browser => { let _ = opener.opener().open_url(url.as_str(), None::<&str>); }
            NewWindow::Ignore => {}
        }
        NewWindowResponse::Deny
    }
}

/// An ordinary window on one of the hub's files or logs. It shares the org's sign-in, but like a meeting window it
/// is not `main`, so the page gets no native permissions.
fn open_hub_window(app: &App, url: url::Url, home: &Home) {
    static NEXT: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(1);
    let label = format!("hub-{}", NEXT.fetch_add(1, Ordering::Relaxed));
    #[cfg(not(target_os = "linux"))]
    let opened = url.clone();
    let (navigate, page) = sign_in_hooks(app, Some(home.hub.clone()), false);
    #[allow(unused_mut)]
    let mut builder = WebviewWindowBuilder::new(app, &label, WebviewUrl::External(url))
        .title(config::app_name())
        .inner_size(1000.0, 800.0)
        .min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        .user_agent(USER_AGENT)
        .disable_drag_drop_handler()
        .on_new_window(new_window_handler(app, home.clone()))
        .on_navigation(navigate)
        .on_page_load(move |_, payload| page(payload));
    #[cfg(target_os = "macos")]
    {
        if let Some(store) = home.store {
            builder = builder.data_store_identifier(*store.as_bytes());
        }
    }
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
    if builder.build().is_ok() {
        owned_by(app, &label, &home.id);
    }
}

/// Remembers which org a window belongs to, so removing the org closes it.
fn owned_by(app: &App, label: &str, org: &str) {
    app.state::<AppState>().owned.lock().unwrap().push((label.to_string(), org.to_string()));
}

/// Forgets a window that has closed.
pub(crate) fn forget_owned(app: &App, label: &str) {
    app.state::<AppState>().owned.lock().unwrap().retain(|(window, _)| window != label);
}

/// Takes the labels of the windows an org opened (hub files and meetings) off the list.
pub(crate) fn take_owned(app: &App, org: &str) -> Vec<String> {
    let state = app.state::<AppState>();
    let mut owned = state.owned.lock().unwrap();
    let (mine, rest): (Vec<_>, Vec<_>) = owned.drain(..).partition(|(_, id)| id == org);
    *owned = rest;
    mine.into_iter().map(|(label, _)| label).collect()
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
            Event::Nav(address) => Some(follow(Some(&hub), None, signing_in, &url::Url::parse(address).unwrap())),
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

/// One ordinary, resizable window per meeting, showing only that meeting (`&window=1`); asking again for the same
/// one brings it to the front. It belongs to the org that asked: it follows that org's address and data store.
pub(crate) fn open_meeting_window(app: &App, home: &Home, id: &str) {
    if id.is_empty() || id.len() > 64 || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return;
    }
    let label = format!("meeting-{}-{id}", home.id);
    if let Some(window) = app.get_webview_window(&label) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    let mut url = home.hub.clone();
    url.set_fragment(Some(&format!("/meetings?meeting={id}&window=1")));
    let (navigate, page) = sign_in_hooks(app, Some(home.hub.clone()), false);
    let mut builder = WebviewWindowBuilder::new(app, &label, WebviewUrl::External(url))
        .title("Meeting")
        .inner_size(1100.0, 820.0)
        .min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        .resizable(true)
        .user_agent(USER_AGENT)
        .disable_drag_drop_handler()
        .on_new_window(new_window_handler(app, home.clone()))
        .on_navigation(navigate)
        .on_page_load(move |_, payload| page(payload));
    #[cfg(target_os = "macos")]
    {
        if let Some(store) = home.store {
            builder = builder.data_store_identifier(*store.as_bytes());
        }
    }
    if builder.build().is_ok() {
        owned_by(app, &label, &home.id);
    }
}

/// The main window: the rail and the open org's page.
pub(crate) fn main_window(app: &App) -> Option<Window> {
    app.get_window("main")
}

pub(crate) fn show_window(app: &App) {
    if let Some(window) = main_window(app).or_else(|| app.get_window("server")) {
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
pub(crate) fn remember_frame(app: &App) {
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
pub(crate) fn restore_frame(app: &App) {
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
pub(crate) fn open_search(app: &App) {
    show_window(app);
    eval(app, "window.ticoSearch && window.ticoSearch()");
}

pub(crate) fn eval(app: &App, js: &str) {
    match active_webview(app) {
        Some(page) => { if let Err(e) = page.eval(js) { log::warn!("eval failed: {e}"); } }
        None => log::warn!("eval: no org page is open"),
    }
}

// Cmd+R reaches this through the View menu too.
pub(crate) fn reload(app: &App) {
    if let (Some(config), Some(page)) = (connection(app), active_webview(app)) {
        let _ = page.navigate(config.start_url());
    }
}
