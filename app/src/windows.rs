//! The main window and the web views in it: building them, the sign-in and new-window policy each one
//! follows, the window frame kept between launches, and the page's eval and reload.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
#[cfg(not(target_os = "linux"))]
use std::sync::Mutex;
use std::time::Duration;

use serde_json::Value;
use tauri::webview::{NewWindowFeatures, NewWindowResponse, PageLoadEvent, PageLoadPayload, Webview, WebviewBuilder};
use tauri::window::{Window, WindowBuilder};
use tauri::utils::config::BackgroundThrottlingPolicy;
use tauri::{LogicalPosition, LogicalSize, Manager, PhysicalPosition, PhysicalSize, Rect, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_window_state::StateFlags;
use uuid::Uuid;

use crate::bridge::bridge_script;
use crate::config::{self, Config};
use crate::orgs::Org;
use crate::visual_origin::{self, VisualOrigin};
use crate::{active_org, connection, refresh_chrome, signin, timing, App, AppState, MIN_HEIGHT, MIN_WIDTH, USER_AGENT};

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

/// The runtime capability for one org's pages. It names that org's page webviews (`org-<id>-*`, see
/// `new_page_label`) and no window: a window grant would match every child webview in "main", so one org's grant
/// would reach the others. The remote URL confines it to the org's own origin. One per org, granted once: Tauri keeps
/// every capability it is given for the rest of the run, so a grant per page would grow with every switch. No
/// dialogs here: a page's `alert` and `confirm` go through `page_dialog`, which only the page on screen may use.
pub(crate) fn server_capability(hub: &url::Url, id: &str) -> Value {
    let mut capability: Value = serde_json::from_str(include_str!("../capabilities/main.json")).unwrap();
    capability["identifier"] = Value::from(format!("org-{id}"));
    capability["windows"] = serde_json::json!([]);
    capability["webviews"] = serde_json::json!([page_pattern(id)]);
    capability["remote"] = remote_of(hub);
    capability
}

/// The glob (Tauri matches capability webviews as globs) for every page label of org `id`. Ids hold no `-`, so it
/// never matches another org's labels; glob characters in an id are matched literally.
pub(crate) fn page_pattern(id: &str) -> String {
    let mut pattern = String::from("org-");
    for ch in id.chars() {
        if matches!(ch, '*' | '?' | '[' | ']') { pattern.push('['); pattern.push(ch); pattern.push(']'); } else { pattern.push(ch); }
    }
    pattern.push_str("-*");
    pattern
}

fn remote_of(hub: &url::Url) -> Value {
    // Explicit port also confines default-port URLs: URLPattern otherwise treats an
    // omitted port as a wildcard. Host characters must stay literal in URLPattern.
    let mut host = String::new();
    for ch in hub.host_str().unwrap().chars() {
        if matches!(ch, ':' | '*' | '?' | '+' | '(' | ')' | '{' | '}' | '\\') { host.push('\\'); }
        host.push(ch);
    }
    let port = hub.port_or_known_default().unwrap();
    serde_json::json!({"urls": [format!("{}://{host}:{port}/*", hub.scheme())]})
}

#[cfg(test)]
mod capability_tests {
    use super::*;

    const ORG: &str = "o_7f3a0001";

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
            assert_eq!(capability["identifier"], "org-o_7f3a0001");
            assert_eq!(capability["windows"], serde_json::json!([]));
            assert_eq!(capability["webviews"], serde_json::json!(["org-o_7f3a0001-*"]));
            assert_eq!(capability["local"], false);
        }
        let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        assert_eq!(config["app"]["security"]["capabilities"], serde_json::json!(["server", "rail"]));
    }
    #[test]
    fn two_orgs_pages_cannot_reach_each_other() {
        let (acme, tidy) = (url::Url::parse("https://acme.example.com/").unwrap(), url::Url::parse("https://tidy.example.com/").unwrap());
        let (a, b) = (server_capability(&acme, "o_aaaaaaaa"), server_capability(&tidy, "o_bbbbbbbb"));
        assert_eq!(a["webviews"], serde_json::json!(["org-o_aaaaaaaa-*"]));
        assert_eq!(b["webviews"], serde_json::json!(["org-o_bbbbbbbb-*"]));
        let own: tauri::utils::acl::RemoteUrlPattern = a["remote"]["urls"][0].as_str().unwrap().parse().unwrap();
        let other: tauri::utils::acl::RemoteUrlPattern = b["remote"]["urls"][0].as_str().unwrap().parse().unwrap();
        assert!(own.test(&acme) && !own.test(&tidy));
        assert!(other.test(&tidy) && !other.test(&acme));
        // No core permissions: they let a page listen to app events (Tauri delivers targeted events to listeners on
        // any target) and list every window and webview, which name the other orgs.
        let permissions: Vec<&str> = a["permissions"].as_array().unwrap().iter().filter_map(Value::as_str).collect();
        assert!(permissions.iter().all(|p| !p.starts_with("core:")), "{permissions:?}");
        assert!(permissions.contains(&"allow-bridge") && permissions.contains(&"allow-page-dialog"));
        // No dialog plugin: a hidden (prefetched) page could put a dialog over the open org. `page_dialog` checks.
        assert!(permissions.iter().all(|p| !p.starts_with("dialog:")), "{permissions:?}");
    }

    #[test]
    fn page_patterns_name_one_org_and_take_glob_characters_literally() {
        assert_eq!(page_pattern("o_aaaaaaaa"), "org-o_aaaaaaaa-*");
        assert_eq!(page_pattern("o_*?[]"), "org-o_[*][?][[][]]-*");
    }

    #[test]
    fn switching_back_and_forth_grants_one_capability_per_org() {
        let (acme, tidy) = (url::Url::parse("https://acme.example.com/").unwrap(), url::Url::parse("https://tidy.example.com/").unwrap());
        let mut granted = std::collections::HashSet::new();
        for _ in 0..500 {
            for (hub, id) in [(&acme, "o_aaaaaaaa"), (&tidy, "o_bbbbbbbb")] {
                let label = new_page_label(id);
                let capability = server_capability(hub, id);
                let pattern = capability["webviews"][0].as_str().unwrap();
                // Every new page's label is one the org's single grant names.
                assert!(label.starts_with(pattern.trim_end_matches('*')) && org_of(&label) == Some(id), "{label} {pattern}");
                let identifier = capability["identifier"].as_str().unwrap();
                if first_grant(&granted, identifier) {
                    granted.insert(identifier.to_string());
                }
            }
        }
        assert_eq!(granted.len(), 2);
    }

    #[test]
    fn every_page_webview_has_a_label_of_its_own_that_names_its_org() {
        let (first, second) = (new_page_label("o_aaaaaaaa"), new_page_label("o_aaaaaaaa"));
        assert_ne!(first, second, "a later page of the same org never inherits an earlier one's grants");
        assert_eq!(org_of(&first), Some("o_aaaaaaaa"));
        assert_eq!(org_of(&second), Some("o_aaaaaaaa"));
        assert_eq!(org_of("org-o_aaaaaaaa"), Some("o_aaaaaaaa"));
        for other in ["rail", "main", "server", "hub-1", "meeting-o_aaaaaaaa-m1", "clear-o_aaaaaaaa", "org-", "tico-signin-ab"] {
            assert_eq!(org_of(other), None, "{other}");
        }
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
        keep_title_bar_reachable(app, &build_main(app)?);
    }
    if active_webview(app).is_none() {
        open_org_webview(app, &org, true)?;
        note_shown(&mut app.state::<AppState>().shown.lock().unwrap(), &org.id, KEEP_ALIVE);
    }
    sync_rail(app)?;
    layout(app);
    Ok(())
}

/// The window itself, hidden until the app shows it. The window-state plugin restores its saved frame while it is
/// built, so it never shows at the default place first. Its content is child webviews, placed by `layout`.
fn build_main(app: &App) -> tauri::Result<Window> {
    let builder = WindowBuilder::new(app, "main")
        .title(config::app_name())
        .inner_size(1240.0, 820.0)
        // Where a first launch opens; a saved frame replaces it.
        .center()
        .min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        // The hub page's own dark background (ui/styles/tokens.css --g0), behind a page that has not painted yet.
        .background_color(tauri::window::Color(0x13, 0x14, 0x13, 0xff))
        .visible(false);
    // The native title bar drags the window on every page, including sign-in pages that are
    // not ours; an overlay title bar would leave dragging to each page.
    #[cfg(target_os = "macos")]
    let builder = builder.title_bar_style(tauri::TitleBarStyle::Visible);
    builder.build()
}

/// A label for a new page webview of org `id`: `org-<id>-<n>`, never used before in this run. Ids are never reused,
/// so a label never names two orgs, and a page closing never takes a newer page's label with it. The org's one
/// capability names them all (`page_pattern`).
pub(crate) fn new_page_label(id: &str) -> String {
    static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
    format!("org-{id}-{}", NEXT.fetch_add(1, Ordering::Relaxed))
}

/// The org a page webview's label names, if it is an org page's.
pub(crate) fn org_of(label: &str) -> Option<&str> {
    let rest = label.strip_prefix("org-")?;
    Some(rest.split_once('-').map_or(rest, |(id, _)| id)).filter(|id| !id.is_empty())
}

/// Org `id`'s page, if its webview is open (at most one is).
pub(crate) fn org_page(app: &App, id: &str) -> Option<Webview> {
    app.webviews().into_iter().find(|(label, _)| org_of(label) == Some(id)).map(|(_, webview)| webview)
}

/// The open org's page, if its webview is open.
pub(crate) fn active_webview(app: &App) -> Option<Webview> {
    let org = active_org(app)?;
    org_page(app, &org.id)
}

/// The org list down the left of the window, shown with 2+ orgs.
pub(crate) const RAIL: &str = "rail";
pub(crate) const RAIL_WIDTH: f64 = 60.0;

/// The org a window belongs to: its id, the address its navigation follows, and its own data store (macOS 14+).
#[derive(Clone, Debug)]
pub(crate) struct Home {
    pub id: String,
    pub hub: url::Url,
    // Only macOS gives a page its own store.
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
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

/// The window's content size, in logical units.
fn content_size(app: &App) -> Option<(f64, f64)> {
    let window = main_window(app)?;
    let (size, scale) = (window.inner_size().ok()?, window.scale_factor().ok()?);
    Some((size.width as f64 / scale, size.height as f64 / scale))
}

/// Where an org's page goes: beside the rail, or, for a page built hidden, as large but off to the left of the
/// window, so that it never covers the open page, even for the moment before it is hidden.
fn page_bounds(app: &App, on_screen: bool) -> Rect {
    let (width, height) = content_size(app).unwrap_or((1240.0, 820.0));
    let left = if app.get_webview(RAIL).is_some() { RAIL_WIDTH } else { 0.0 };
    let page = (width - left).max(1.0);
    bounds(if on_screen { left } else { -(page + 1000.0) }, 0.0, page, height)
}

/// Places the rail and the open org's page side by side, filling the window. Logical units throughout. A prefetched
/// page keeps its bounds until it is shown.
pub(crate) fn layout(app: &App) {
    let Some((_, height)) = content_size(app) else { return };
    if let Some(rail) = app.get_webview(RAIL) {
        let _ = rail.set_bounds(bounds(0.0, 0.0, RAIL_WIDTH, height));
    }
    if let Some(page) = active_webview(app) {
        let _ = page.set_bounds(page_bounds(app, true));
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

/// The first page of an org's webview: the hub's own sign-in for an org Tico sign-in just added (its session is
/// already in the org's store, so the sign-in goes straight through to the hub's root), otherwise the usual one.
pub(crate) fn first_page(usual: url::Url, hub: &url::Url, sign_in: bool) -> url::Url {
    if !sign_in {
        return usual;
    }
    hub.join(signin::LOGIN_PATH).unwrap_or(usual)
}

/// Opens an org's page as a child of the main window, on the org's own data store (macOS 14+). A page built for a
/// prefetch (`on_screen` false) loads hidden, off to the side, until a switch shows it. Answers whether the page
/// started on the hub's sign-in.
fn open_org_webview(app: &App, org: &Org, on_screen: bool) -> tauri::Result<bool> {
    let (Some(window), Some(home)) = (main_window(app), Home::of(org)) else { return Ok(false) };
    let label = new_page_label(&home.id);
    grant(app, server_capability(&home.hub, &home.id))?;
    timing::mark(app, &home.id, "granted");
    let config = Config::new(home.hub.clone());
    // A prefetch that is never shown leaves the flag for the page that is.
    let sign_in = {
        let state = app.state::<AppState>();
        let mut list = state.orgs.lock().unwrap();
        if on_screen { list.take_sign_in(&home.id) } else { list.get(&home.id).is_some_and(|org| org.sign_in) }
    };
    let start = first_page(config.start_url(), &home.hub, sign_in);
    let (navigate, page) = sign_in_hooks(app, Some(home.hub.clone()), Some(home.id.clone()));
    #[cfg_attr(not(target_os = "macos"), allow(unused_mut))]
    let mut builder = WebviewBuilder::new(&label, WebviewUrl::External(start))
        .user_agent(USER_AGENT)
        // Files dropped on a page reach it (chat composers, task files): Tauri's own drop handler would swallow them on macOS.
        .disable_drag_drop_handler()
        // Web inspector in debug builds, and in a release built with `--features devtools`.
        .devtools(cfg!(any(debug_assertions, feature = "devtools")))
        // A hidden WKWebView is an inactive one, and WebKit's default suspends it (macOS 14+): a prefetched page would
        // stop loading until shown. Throttling slows its timers, which the hub's boot runs on, so neither is used.
        .background_throttling(BackgroundThrottlingPolicy::Disabled)
        .initialization_script(&bridge_script(app))
        .on_new_window(new_window_handler(app, home.clone(), true))
        .on_navigation(navigate)
        .on_page_load(move |_, payload| page(payload));
    #[cfg(target_os = "macos")]
    {
        if let Some(store) = home.store {
            builder = builder.data_store_identifier(*store.as_bytes());
        }
    }
    {
        // When this page's webview was built, for its first `bootTiming` report (bridge.rs). A page closed before it
        // reports leaves its entry, so old ones go here.
        let state = app.state::<AppState>();
        let mut opened = state.page_opened.lock().unwrap();
        opened.retain(|_, at| at.elapsed() < std::time::Duration::from_secs(120));
        opened.insert(label.clone(), std::time::Instant::now());
    }
    let frame = page_bounds(app, on_screen);
    let webview = window.add_child(builder, frame.position, frame.size)?;
    if !on_screen {
        let _ = webview.hide();
    }
    timing::mark(app, &home.id, "created");
    // The page now holds its own store, so a store Tico sign-in held for it can go.
    #[cfg(target_os = "macos")]
    if let Some(store) = home.store {
        crate::signin::release_store(app, store);
    }
    Ok(sign_in)
}

/// Adds an org's capability, once a run: one per org however many pages it opens.
fn grant(app: &App, capability: Value) -> tauri::Result<()> {
    let state = app.state::<AppState>();
    let mut granted = state.granted.lock().unwrap();
    let identifier = capability["identifier"].as_str().unwrap_or_default();
    if first_grant(&granted, identifier) {
        app.add_capability(capability.to_string())?;
        granted.insert(identifier.to_string());
    }
    Ok(())
}

/// Whether a capability is new to this run. Tauri keeps (and scans, on every call) each one it is given.
pub(crate) fn first_grant(granted: &std::collections::HashSet<String>, identifier: &str) -> bool {
    !granted.contains(identifier)
}

/// Whether org `id`'s page is the one on screen: the last shown, and neither a prefetch waiting to be shown nor a page
/// kept hidden after a switch away (the last shown once the open org was removed, until the switch that follows).
pub(crate) fn on_screen(shown: &[String], prefetched: Option<&str>, kept: &Kept, id: &str) -> bool {
    shown.last().is_some_and(|last| last == id) && prefetched != Some(id) && !kept.contains(id)
}

/// Whether org `id`'s page is the one on screen now.
pub(crate) fn page_on_screen(app: &App, id: &str) -> bool {
    let state = app.state::<AppState>();
    let prefetched = state.prefetch.lock().unwrap().org.clone();
    let shown = state.shown.lock().unwrap();
    let kept = state.kept.lock().unwrap();
    on_screen(&shown, prefetched.as_deref(), &kept, id)
}

/// How many org pages stay open: the open one and up to two kept hidden after a switch away (`Kept`), so clicking
/// across many orgs never piles pages up: past the cap, the least recently shown closes at once. A prefetched page
/// (see `prefetch`) is apart from these.
pub(crate) const KEEP_ALIVE: usize = 3;

/// How long a page stays open, hidden, after a switch away from it: a switch back within this shows it as it was,
/// with no reload. Each time it goes hidden it gets the full time again.
pub(crate) const KEEP_FOR: Duration = Duration::from_secs(30);

/// The pages kept open, hidden, after a switch away from them, until `KEEP_FOR` after they went hidden.
#[derive(Default)]
pub(crate) struct Kept {
    pages: Vec<KeptPage>,
    // Moves on at every hiding: a page's close timer goes ahead only if its ticket is still the page's.
    serial: u64,
}

struct KeptPage {
    org: String,
    ticket: u64,
    // The visual allowance the page had when it was open: it follows the page while hidden (its navigation, and
    // `visualOrigin` from the page itself) and becomes the open one again when the page is shown.
    visual: Option<VisualOrigin>,
}

impl Kept {
    /// `id`'s page went hidden with its visual allowance: the ticket for its close.
    pub(crate) fn hide(&mut self, id: &str, visual: Option<VisualOrigin>) -> u64 {
        self.serial += 1;
        self.pages.retain(|page| page.org != id);
        self.pages.push(KeptPage { org: id.to_string(), ticket: self.serial, visual });
        self.serial
    }

    /// `id`'s page is shown again: its close is off. Answers its allowance, `None` if the page was not kept.
    pub(crate) fn show(&mut self, id: &str) -> Option<Option<VisualOrigin>> {
        let index = self.pages.iter().position(|page| page.org == id)?;
        Some(self.pages.remove(index).visual)
    }

    pub(crate) fn contains(&self, id: &str) -> bool {
        self.pages.iter().any(|page| page.org == id)
    }

    /// `id`'s close with `ticket` is due: whether the page is still hidden since that hiding (it goes now), not shown
    /// or hidden again meanwhile.
    pub(crate) fn expire(&mut self, id: &str, ticket: u64) -> bool {
        let before = self.pages.len();
        self.pages.retain(|page| page.org != id || page.ticket != ticket);
        self.pages.len() != before
    }

    /// `id`'s page closes some other way (past the cap, or its org going): its close is off.
    pub(crate) fn forget(&mut self, id: &str) {
        self.pages.retain(|page| page.org != id);
    }

    /// A kept page sent its own allowance (`visualOrigin`): whether `id` is a kept page, which takes it.
    pub(crate) fn set_visual(&mut self, id: &str, visual: Option<VisualOrigin>) -> bool {
        let Some(page) = self.pages.iter_mut().find(|page| page.org == id) else { return false };
        page.visual = visual;
        true
    }

    /// The allowance of the kept page on `hub`, if one is.
    fn visual_for(&mut self, hub: Option<&url::Url>) -> Option<&mut Option<VisualOrigin>> {
        self.pages.iter_mut().map(|page| &mut page.visual).find(|visual| visual.as_ref().is_some_and(|visual| visual.serves(hub)))
    }
}

/// The bookkeeping of a switch to `id` from `previous` once `id`'s page is on screen (`Kept::show` already took `id`
/// out of the kept pages): `previous`'s page goes hidden, kept with the allowance it had (`left`), and the least
/// recently shown pages beyond `KEEP_ALIVE` go. Answers the pages to close now, and the ticket for the close of the
/// page that went hidden.
pub(crate) fn note_switch(shown: &mut Vec<String>, kept: &mut Kept, id: &str, previous: Option<&str>, left: Option<VisualOrigin>) -> (Vec<String>, Option<u64>) {
    let gone: Vec<String> = note_shown(shown, id, KEEP_ALIVE).into_iter().filter(|other| other != id).collect();
    for other in &gone {
        kept.forget(other);
    }
    let hidden = previous.filter(|previous| *previous != id && shown.iter().any(|shown| shown == previous))
        .map(|previous| kept.hide(previous, left));
    (gone, hidden)
}

/// Records that `id`'s page is on screen, most recent last, and returns the orgs whose pages go: the least recently
/// shown beyond `keep`.
pub(crate) fn note_shown(order: &mut Vec<String>, id: &str, keep: usize) -> Vec<String> {
    order.retain(|shown| shown != id);
    order.push(id.to_string());
    let over = order.len().saturating_sub(keep.max(1));
    order.drain(..over).collect()
}

/// Makes another org the open one: its page replaces the old one in the main window. A page already built (a
/// prefetch, or a page kept hidden since a switch away from it) is shown at once, as it is: a kept page with no
/// reload. Otherwise a new one opens. The old page goes hidden once the new one is on screen, and closes `KEEP_FOR`
/// later unless it is shown again first (`Kept`), or at once past `KEEP_ALIVE`. No restart and no new sign-in: the
/// org's own store keeps its sign-in. The org becomes the open one only once its page exists: if the page cannot
/// open, the old org and its page stay.
pub(crate) fn switch_to(app: &App, id: &str) -> tauri::Result<()> {
    timing::begin(app, id);
    let state = app.state::<AppState>();
    let _changing = state.changing.lock().unwrap();
    // A dialog the open page has up is answered before its page goes (see bridge::page_dialog).
    crate::bridge::DIALOGS.wait_closed();
    timing::mark(app, id, "lock");
    let (org, was_open, previous) = {
        let orgs = state.orgs.lock().unwrap();
        let Some(org) = orgs.get(id).cloned() else { return Ok(()) };
        let previous = orgs.active().cloned();
        (org, previous.as_ref().is_some_and(|previous| previous.id == id), previous)
    };
    let Some(home) = Home::of(&org) else {
        log::warn!("could not open an org: its address is not usable");
        return Ok(());
    };
    let live = org_page(app, &org.id);
    let kept = live.is_some() && state.kept.lock().unwrap().contains(id);
    let showing = {
        let prefetched = state.prefetch.lock().unwrap().org.clone();
        let shown = state.shown.lock().unwrap();
        on_screen(&shown, prefetched.as_deref(), &state.kept.lock().unwrap(), id)
    };
    if kept {
        timing::mark(app, id, "kept");
    }
    // Open and on screen: nothing to show. Open but hidden (the prefetch, or a kept page, became the open org when
    // the open one was removed) goes on to be shown.
    if was_open && live.is_some() && showing {
        timing::finish(app, id, "already open");
        refresh_chrome(app);
        return Ok(());
    }
    if main_window(app).is_none() {
        // The window opens on the open org, so the org is made the open one first, and put back if that fails.
        state.orgs.lock().unwrap().set_active(id);
        if let Err(e) = build_window(app) {
            log::warn!("could not open the window for an org: {e}");
            if let Some(previous) = &previous {
                state.orgs.lock().unwrap().set_active(&previous.id);
            }
            return Err(e);
        }
        show_when_painted(app);
    } else if let Some(page) = &live {
        layout_page(app, page);
        let _ = page.show();
        let _ = page.set_focus();
        // The flag is used up only by a page that went to the hub's sign-in with it: a prefetch built on it. A kept
        // page used it up when it was first shown (it is never the prefetch: see `prefetch`).
        if state.prefetch.lock().unwrap().started_on_sign_in(id) {
            state.orgs.lock().unwrap().take_sign_in(id);
        }
    } else if let Err(e) = open_org_webview(app, &org, true) {
        log::warn!("could not open an org's page, so the open org stays: {e}");
        return Err(e);
    }
    state.orgs.lock().unwrap().set_active(id);
    state.prefetch.lock().unwrap().take(id);
    // A kept page's close is off, and its allowance comes back with it.
    let kept_visual = state.kept.lock().unwrap().show(id).flatten();
    // The connection follows the open org, and the status starts over.
    let left = select_org(&state, Config::new(home.hub.clone()), live.is_some(), kept_visual);
    *state.status.lock().unwrap() = crate::hub::HubStatus::default();
    layout(app);
    if kept {
        // Nothing left to time: the app on it has been up since it was first shown.
        timing::finish(app, id, "shown");
    } else {
        timing::shown(app, id);
    }
    // The page left goes hidden with its own allowance (only ever its own: the open page's is for the open hub).
    let previous = previous.filter(|previous| previous.id != id);
    let left = left.filter(|visual| visual.serves(previous.as_ref().and_then(Org::hub).as_ref()));
    let (gone, hidden) = {
        let mut shown = state.shown.lock().unwrap();
        note_switch(&mut shown, &mut state.kept.lock().unwrap(), id, previous.as_ref().map(|org| org.id.as_str()), left)
    };
    if let (Some(previous), Some(ticket)) = (previous, hidden) {
        if let Some(page) = org_page(app, &previous.id) {
            hide_page(&page);
        }
        close_later(app, previous.id, ticket);
    }
    for other in &gone {
        if let Some(page) = org_page(app, other) {
            let _ = page.close();
        }
    }
    refresh_chrome(app);
    Ok(())
}

/// Media a hidden page was playing stops: the page is no longer on screen to show it.
const PAUSE_MEDIA: &str = "document.querySelectorAll('video, audio').forEach(m => { try { m.pause(); } catch (e) {} });";

/// Hides a page kept after a switch away.
fn hide_page(page: &Webview) {
    let _ = page.eval(PAUSE_MEDIA);
    let _ = page.hide();
}

/// Closes org `id`'s kept page `KEEP_FOR` after it went hidden (`ticket`), unless it was shown (or hidden again,
/// which starts its own wait) meanwhile, or closed some other way.
fn close_later(app: &App, id: String, ticket: u64) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(KEEP_FOR).await;
        let state = app.state::<AppState>();
        let _changing = state.changing.lock().unwrap();
        if !state.kept.lock().unwrap().expire(&id, ticket) || active_org(&app).is_some_and(|open| open.id == id) {
            return;
        }
        state.shown.lock().unwrap().retain(|shown| shown != &id);
        if let Some(page) = org_page(&app, &id) {
            let _ = page.close();
            log::info!("org page {id} closed: hidden for {} s", KEEP_FOR.as_secs());
        }
    });
}

/// Numbers a rail click, in the order the clicks came.
pub(crate) fn next_click() -> u64 {
    static CLICKS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
    CLICKS.fetch_add(1, Ordering::Relaxed)
}

/// The switch a rail click asks for, unless a later click already ran: switches wait on each other, and the last
/// click is the one that must win (A, B, A ends on A). Clicks run one at a time, in number order as far as they
/// overtake each other.
pub(crate) fn switch_for_click(app: &App, id: &str, click: u64) -> tauri::Result<()> {
    static LAST: std::sync::Mutex<u64> = std::sync::Mutex::new(0);
    let mut last = LAST.lock().unwrap();
    if !newer_click(&mut last, click) {
        timing::finish(app, id, "a later click came first");
        return Ok(());
    }
    switch_to(app, id)
}

/// Whether `click` comes after every click that ran, noting it as the last if so.
pub(crate) fn newer_click(last: &mut u64, click: u64) -> bool {
    if click <= *last {
        return false;
    }
    *last = click;
    true
}

/// Puts a page in the open page's place, beside the rail.
fn layout_page(app: &App, page: &Webview) {
    let _ = page.set_bounds(page_bounds(app, true));
}

/// The open org's server becomes the connection. Its page's visual allowance comes along when the page was already
/// built: the one a kept page had (`kept`), or the one a prefetch sent while hidden; a new page sends its own.
/// Answers the allowance of the page that was open, for it to keep while hidden.
pub(crate) fn select_org(state: &AppState, config: Config, built: bool, kept: Option<VisualOrigin>) -> Option<VisualOrigin> {
    let mut connection = state.connection.lock().unwrap();
    let mut visual = state.visual_origin.lock().unwrap();
    let mut parked = state.parked_visual.lock().unwrap();
    let left = visual.take();
    if built {
        if let Some(kept) = kept.filter(|kept| kept.serves(Some(&config.hub))) {
            *visual = Some(kept);
        } else if parked.as_ref().is_some_and(|parked| parked.serves(Some(&config.hub))) {
            *visual = parked.take();
        }
    }
    *connection = Some(config);
    left
}

/// The page built ahead of a click: at most one, for the org the pointer last rested on in the rail.
#[derive(Default)]
pub(crate) struct Prefetch {
    pub(crate) org: Option<String>,
    // Whether that org's page started on the hub's sign-in (its `sign_in` flag, which a prefetch leaves set).
    on_sign_in: bool,
    // Moves on at every rest, leave and switch: a build or a close waiting for its turn goes ahead only if nothing
    // came in meanwhile.
    serial: u64,
}

impl Prefetch {
    /// The pointer rested on an org: any pending close, and any older rest still waiting to build, is called off.
    /// The ticket lets this rest's build check that it is still the latest.
    pub(crate) fn rest(&mut self) -> u64 {
        self.serial += 1;
        self.serial
    }

    pub(crate) fn is_latest(&self, ticket: u64) -> bool {
        self.serial == ticket
    }

    /// The rest with `ticket` builds `id`'s page: `None` when a leave or another rest came in since (nothing to
    /// build), otherwise the org whose prefetched page this one replaces. Checked and recorded in one step, so a
    /// leave from here on finds the org recorded and schedules the page's close (`left`).
    pub(crate) fn claim(&mut self, ticket: u64, id: &str) -> Option<Option<String>> {
        if !self.is_latest(ticket) {
            return None;
        }
        self.on_sign_in = false;
        Some(self.org.replace(id.to_string()))
    }

    /// `id`'s prefetched page was built: on the hub's sign-in or not.
    pub(crate) fn built(&mut self, id: &str, on_sign_in: bool) {
        if self.org.as_deref() == Some(id) {
            self.on_sign_in = on_sign_in;
        }
    }

    /// Whether `id` is the prefetch and its page started on the hub's sign-in.
    pub(crate) fn started_on_sign_in(&self, id: &str) -> bool {
        self.org.as_deref() == Some(id) && self.on_sign_in
    }

    /// The pointer left: a rest still waiting to build is called off, and a page already built gets the ticket for its
    /// close.
    pub(crate) fn left(&mut self) -> Option<u64> {
        self.serial += 1;
        self.org.as_ref().map(|_| self.serial)
    }

    /// A switch to `id` (or `id` going away) takes its prefetched page, if it has one.
    pub(crate) fn take(&mut self, id: &str) -> bool {
        if self.org.as_deref() != Some(id) {
            return false;
        }
        self.org = None;
        self.on_sign_in = false;
        self.serial += 1;
        true
    }
}

/// How long a prefetched page waits for its click once the pointer leaves its org.
const PREFETCH_FOR: Duration = Duration::from_secs(10);

/// The pointer rested on an org in the rail: its page is built hidden, so a click shows it at once. It replaces the
/// page built for another org. Nothing for the open org, and nothing before the main window exists. The rest is
/// recorded before waiting for a switch in progress, so a leave (or another rest) meanwhile calls the build off.
pub(crate) fn prefetch(app: &App, id: &str) -> tauri::Result<()> {
    let state = app.state::<AppState>();
    let ticket = state.prefetch.lock().unwrap().rest();
    let _changing = state.changing.lock().unwrap();
    let org = {
        let list = state.orgs.lock().unwrap();
        list.get(id).filter(|_| list.active().map(|open| open.id.as_str()) != Some(id)).cloned()
    };
    // A page kept hidden since a switch away is shown as it is on a click: nothing to build, and it never becomes the
    // prefetch, so it never gets a second page and a leave's close never reaches it (its own `close_later` does).
    let kept = state.kept.lock().unwrap().contains(id);
    let Some(org) = org.filter(|_| !kept && main_window(app).is_some()) else {
        // Nothing to build (a rest on the open org or a kept one, say). The rest still called off the close of the
        // page built for another org, so for that page it counts as a leave: its close is scheduled again.
        leave(app);
        return Ok(());
    };
    let built = org_page(app, id).is_some();
    let replaced = {
        let mut prefetch = state.prefetch.lock().unwrap();
        if prefetch.org.as_deref() == Some(id) && built {
            return Ok(());
        }
        // The ticket is checked and the org recorded under one lock: a leave before this calls the build off, and a
        // leave after it (during the build, say) finds the org and schedules the page's close.
        let Some(replaced) = prefetch.claim(ticket, id) else { return Ok(()) };
        replaced
    };
    if let Some(old) = replaced.filter(|old| old != id) {
        close_unused(app, &old);
    }
    if built {
        return Ok(());
    }
    timing::hover(app, id);
    let opened = open_org_webview(app, &org, false);
    let mut prefetch = state.prefetch.lock().unwrap();
    match opened {
        Ok(on_sign_in) => prefetch.built(id, on_sign_in),
        // No page to wait for its click.
        Err(_) => if prefetch.org.as_deref() == Some(id) {
            prefetch.org = None;
        },
    }
    opened.map(|_| ())
}

/// The pointer left the org it rested on: its prefetched page closes unless a click (or another rest on it) comes
/// within `PREFETCH_FOR`.
pub(crate) fn leave(app: &App) {
    let Some(serial) = app.state::<AppState>().prefetch.lock().unwrap().left() else { return };
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(PREFETCH_FOR).await;
        let state = app.state::<AppState>();
        let _changing = state.changing.lock().unwrap();
        let unused = {
            let mut prefetch = state.prefetch.lock().unwrap();
            if prefetch.serial != serial {
                return;
            }
            prefetch.org.take()
        };
        if let Some(id) = unused {
            close_unused(&app, &id);
        }
    });
}

/// Closes a prefetched page that was never shown. A page in `shown` (the open one, or one kept hidden since a switch
/// away) is never the prefetch's to close: the switch away or its own `close_later` closes it. Callers hold `changing`.
fn close_unused(app: &App, id: &str) {
    let state = app.state::<AppState>();
    let open = state.orgs.lock().unwrap().active().is_some_and(|org| org.id == id);
    if open || state.shown.lock().unwrap().iter().any(|shown| shown == id) {
        return;
    }
    *state.parked_visual.lock().unwrap() = None;
    timing::drop_prefetch(app, id);
    if let Some(page) = org_page(app, id) {
        let _ = page.close();
    }
}

/// An org went: its page is no longer counted as shown, kept or prefetched, and a kept page's pending close is off.
/// Callers hold `changing` and close the page at once.
pub(crate) fn forget_page(app: &App, id: &str) {
    let state = app.state::<AppState>();
    let mut prefetch = state.prefetch.lock().unwrap();
    if forget_org(&mut state.shown.lock().unwrap(), &mut state.kept.lock().unwrap(), &mut prefetch, id) {
        *state.parked_visual.lock().unwrap() = None;
    }
}

/// `forget_page`'s bookkeeping: answers whether `id` was the prefetch, whose allowance then goes too.
pub(crate) fn forget_org(shown: &mut Vec<String>, kept: &mut Kept, prefetch: &mut Prefetch, id: &str) -> bool {
    shown.retain(|shown| shown != id);
    kept.forget(id);
    prefetch.take(id)
}

/// Loads org `id`'s open page again after Tico sign-in renewed its session: on the hub's sign-in when the server
/// has one (`login`, decided when the session was renewed), so it goes straight in, otherwise on its usual first page.
/// Going to the sign-in uses up the org's flag.
pub(crate) fn renew_page(app: &App, id: &str, login: bool) {
    // Under `changing`, as switches are: no switch closes the page between finding it and loading it again.
    let state = app.state::<AppState>();
    let _changing = state.changing.lock().unwrap();
    let Some(page) = org_page(app, id) else { return };
    let org = app.state::<AppState>().orgs.lock().unwrap().get(id).cloned();
    let Some(home) = org.as_ref().and_then(Home::of) else { return };
    if page.navigate(first_page(Config::new(home.hub.clone()).start_url(), &home.hub, login)).is_ok() && login {
        app.state::<AppState>().orgs.lock().unwrap().take_sign_in(id);
    }
}

/// Whether the main window is waiting for its first org page to paint before it shows.
static AWAITING_PAINT: AtomicBool = AtomicBool::new(false);
/// The longest the window waits for that.
const PAINT_WAIT: Duration = Duration::from_secs(3);

/// Shows the main window once the open org's page has loaded, so it never shows blank first; after `PAINT_WAIT` it
/// shows anyway. With no org page (the setup window) it shows at once.
pub(crate) fn show_when_painted(app: &App) {
    if main_window(app).is_none() || active_webview(app).is_none() {
        return show_window(app);
    }
    AWAITING_PAINT.store(true, Ordering::SeqCst);
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(PAINT_WAIT).await;
        if AWAITING_PAINT.swap(false, Ordering::SeqCst) {
            log::info!("the first org page took over {} s to load, so the window shows without it", PAINT_WAIT.as_secs());
            show_window(&app);
        }
    });
}

/// The open org's page finished a load: the window shows, if it was waiting for that.
fn painted(app: &App) {
    if AWAITING_PAINT.swap(false, Ordering::SeqCst) {
        show_window(app);
    }
}

#[cfg(test)]
mod page_tests {
    use super::*;

    fn order(ids: &[&str]) -> Vec<String> {
        ids.iter().map(|id| id.to_string()).collect()
    }

    #[test]
    fn pages_go_least_recently_shown_first_beyond_the_cap() {
        let mut shown = Vec::new();
        assert!(note_shown(&mut shown, "a", 2).is_empty());
        assert!(note_shown(&mut shown, "b", 2).is_empty());
        assert_eq!(note_shown(&mut shown, "a", 2), Vec::<String>::new(), "showing a kept page again closes nothing");
        assert_eq!(shown, order(&["b", "a"]));
        assert_eq!(note_shown(&mut shown, "c", 2), order(&["b"]));
        assert_eq!(shown, order(&["a", "c"]));
        // The cap in use: the open page and two kept ones.
        assert!(note_shown(&mut shown, "d", KEEP_ALIVE).is_empty());
        assert_eq!(note_shown(&mut shown, "e", KEEP_ALIVE), order(&["a"]));
        assert_eq!(shown, order(&["c", "d", "e"]));
        assert!(note_shown(&mut shown, "e", KEEP_ALIVE).is_empty(), "the open page never closes itself");
        assert_eq!(note_shown(&mut shown, "f", 0), order(&["c", "d", "e"]), "a cap below one still keeps the open page");
        assert_eq!(shown, order(&["f"]));
    }

    #[test]
    fn an_open_org_is_on_screen_only_when_it_was_shown_last_and_is_neither_a_prefetch_nor_kept() {
        let none = Kept::default();
        let shown = order(&["a"]);
        assert!(on_screen(&shown, None, &none, "a"));
        assert!(!on_screen(&shown, None, &none, "b"));
        assert!(!on_screen(&shown, Some("a"), &none, "a"));
        // Open A, prefetch B, remove A: B is the open org but was never shown, so it must still be shown.
        assert!(!on_screen(&[], Some("b"), &none, "b"));
        assert!(!on_screen(&[], None, &none, "b"));
        // B kept, then A (open) removed: B is shown last but hidden, so the switch to it must still show it.
        let mut kept = Kept::default();
        kept.hide("b", None);
        assert!(!on_screen(&order(&["b"]), None, &kept, "b"));
        assert!(kept.show("b").is_some());
        assert!(on_screen(&order(&["b"]), None, &kept, "b"));
    }

    /// A switch as `switch_to` keeps its books: `id` is shown (its close off), and the open org's page goes hidden.
    fn switch(shown: &mut Vec<String>, kept: &mut Kept, id: &str) -> (Vec<String>, Option<u64>) {
        let previous = shown.last().cloned();
        kept.show(id);
        note_switch(shown, kept, id, previous.as_deref(), None)
    }

    #[test]
    fn a_switch_keeps_the_page_left_hidden_and_closes_only_past_the_cap() {
        let (mut shown, mut kept) = (Vec::new(), Kept::default());
        assert_eq!(switch(&mut shown, &mut kept, "a"), (vec![], None), "the first page hides nothing");
        // A to B and back: A is kept, then shown again as it was, and B is kept.
        let (gone, hidden) = switch(&mut shown, &mut kept, "b");
        assert!(gone.is_empty() && hidden.is_some() && kept.contains("a"));
        let (gone, hidden) = switch(&mut shown, &mut kept, "a");
        assert!(gone.is_empty() && hidden.is_some());
        assert!(!kept.contains("a") && kept.contains("b"));
        assert_eq!(shown, order(&["b", "a"]));
        // C, then D: two pages stay hidden besides the open one, and the least recently shown (B) goes first.
        assert!(switch(&mut shown, &mut kept, "c").0.is_empty());
        assert_eq!(shown, order(&["b", "a", "c"]));
        let (gone, hidden) = switch(&mut shown, &mut kept, "d");
        assert_eq!(gone, order(&["b"]));
        assert!(hidden.is_some());
        assert_eq!(shown, order(&["a", "c", "d"]));
        assert!(!kept.contains("b") && kept.contains("a") && kept.contains("c") && !kept.contains("d"));
        // Rapid clicks across many orgs never hold more than the cap.
        for id in ["e", "f", "g", "h", "a", "b", "c"] {
            switch(&mut shown, &mut kept, id);
            assert!(shown.len() <= KEEP_ALIVE && kept.pages.len() < KEEP_ALIVE, "{shown:?}");
            assert!(shown[..shown.len() - 1].iter().all(|other| kept.contains(other)), "every page but the open one is kept");
        }
        // A switch to the org already open hides nothing.
        assert_eq!(switch(&mut shown, &mut kept, "c"), (vec![], None));
        // No org was open (the last one went): nothing to hide.
        assert_eq!(note_switch(&mut Vec::new(), &mut Kept::default(), "a", None, None), (vec![], None));
    }

    #[test]
    fn a_close_that_comes_after_the_page_was_shown_again_closes_nothing() {
        let mut kept = Kept::default();
        let first = kept.hide("a", None);
        // Shown again within the wait: the close that comes later does nothing.
        kept.show("a");
        assert!(!kept.expire("a", first));
        // Hidden again: a fresh wait. The first hiding's close still does nothing; the second's closes it.
        let second = kept.hide("a", None);
        assert_ne!(first, second);
        assert!(!kept.expire("a", first) && kept.contains("a"));
        let other = kept.hide("b", None);
        assert!(!kept.expire("b", second), "a ticket is one page's only");
        assert!(kept.expire("a", second) && !kept.contains("a"));
        assert!(!kept.expire("a", second), "it closes once");
        assert!(kept.expire("b", other));
    }

    #[test]
    fn removing_an_org_forgets_its_kept_page_and_calls_off_its_close() {
        let (mut shown, mut kept, mut prefetch) = (Vec::new(), Kept::default(), Prefetch::default());
        switch(&mut shown, &mut kept, "a");
        let (_, ticket) = switch(&mut shown, &mut kept, "b");
        let ticket = ticket.unwrap();
        assert!(!forget_org(&mut shown, &mut kept, &mut prefetch, "a"), "A was kept, not the prefetch");
        assert_eq!(shown, order(&["b"]));
        assert!(!kept.contains("a") && !kept.expire("a", ticket), "its close is off; the caller closes the page at once");
        // The prefetch goes the same way, and says so, so its parked allowance goes too.
        prefetch.org = Some("c".into());
        assert!(forget_org(&mut shown, &mut kept, &mut prefetch, "c"));
    }

    #[test]
    fn a_kept_page_keeps_its_own_visual_allowance_and_gets_it_back_when_shown() {
        let (acme, tidy) = (url::Url::parse("https://acme.example.com/").unwrap(), url::Url::parse("https://tidy.example.com/").unwrap());
        let doc = crate::orgs::Document { version: 1, active: Some("o_aaaaaaaa".into()), orgs: vec![] };
        let state = AppState::new(crate::orgs::Orgs::new(doc, None), Some(Config::new(acme.clone())));
        let allowance = |hub: &url::Url| VisualOrigin::new(hub, "https://visuals.example.net").unwrap();
        *state.visual_origin.lock().unwrap() = Some(allowance(&acme));
        // A (open, with an allowance) to B (a new page): A's allowance is handed back for A to keep, B has none yet.
        let left = select_org(&state, Config::new(tidy.clone()), false, None);
        assert!(left.as_ref().unwrap().serves(Some(&acme)) && state.visual_origin.lock().unwrap().is_none());
        state.kept.lock().unwrap().hide("o_aaaaaaaa", left);
        *state.visual_origin.lock().unwrap() = Some(allowance(&tidy));
        // While A is hidden, its navigation uses its own allowance, and its new top-level page clears only its own.
        let signing_in = AtomicBool::new(false);
        let frame = url::Url::parse("https://visuals.example.net/visuals/WyJodW1hbjphIiwiZjEiLDEsIiIsMjAwMDAwMDAwMF0.ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn-_0").unwrap();
        assert!(follow_window(&state, &signing_in, true, Some(&acme), &frame), "A's frame follows A's own allowance");
        assert!(state.kept.lock().unwrap().pages[0].visual.is_some());
        top_level_window(&state, &signing_in, true, Some(&acme), &acme);
        assert!(state.kept.lock().unwrap().pages[0].visual.is_none(), "a new top-level page sends its config again");
        assert!(!follow_window(&state, &signing_in, true, Some(&acme), &frame), "and until then has none");
        assert!(follow_window(&state, &signing_in, true, Some(&tidy), &frame), "the open page keeps its own");
        assert!(state.visual_origin.lock().unwrap().is_some(), "the open page's allowance is untouched");
        assert!(state.kept.lock().unwrap().set_visual("o_aaaaaaaa", Some(allowance(&acme))), "the kept page sends it again");
        assert!(!state.kept.lock().unwrap().set_visual("o_cccccccc", Some(allowance(&acme))), "only a kept page keeps one");
        // Back to A: its allowance comes back with it, and B's is handed back for B to keep.
        let kept = state.kept.lock().unwrap().show("o_aaaaaaaa").flatten();
        let left = select_org(&state, Config::new(acme.clone()), true, kept);
        assert!(left.unwrap().serves(Some(&tidy)));
        assert!(state.visual_origin.lock().unwrap().as_ref().unwrap().serves(Some(&acme)));
        // A kept allowance for another hub is never taken.
        let left = select_org(&state, Config::new(tidy.clone()), true, Some(allowance(&acme)));
        assert!(left.is_some() && state.visual_origin.lock().unwrap().is_none());
    }

    #[test]
    fn a_leave_or_newer_rest_calls_off_a_build_still_waiting_and_a_pending_close() {
        let mut prefetch = Prefetch::default();
        let rest = prefetch.rest();
        assert!(prefetch.is_latest(rest));
        // Left before the build got its turn: nothing to close, and the build is off.
        assert_eq!(prefetch.left(), None);
        assert!(!prefetch.is_latest(rest));
        let older = prefetch.rest();
        let newer = prefetch.rest();
        assert!(!prefetch.is_latest(older) && prefetch.is_latest(newer));
        prefetch.org = Some("b".into());
        let close = prefetch.left().unwrap();
        assert!(prefetch.is_latest(close));
        prefetch.rest();
        assert!(!prefetch.is_latest(close), "back on it: the close is off");
        let close = prefetch.left().unwrap();
        assert!(!prefetch.take("c"));
        assert!(prefetch.take("b") && prefetch.org.is_none());
        assert!(!prefetch.is_latest(close), "clicked: the close is off");
    }

    #[test]
    fn the_last_rail_click_wins_whichever_order_the_switches_run_in() {
        // A (1), B (2), A (3): the switches race for their turn, and 3 runs before 2.
        let mut last = 0;
        assert!(newer_click(&mut last, 1));
        assert!(newer_click(&mut last, 3));
        assert!(!newer_click(&mut last, 2), "B ran after the later A: it does nothing");
        assert!(newer_click(&mut last, 4));
    }

    #[test]
    fn a_rest_on_the_open_org_counts_as_a_leave_for_the_prefetched_page() {
        // A open; rest on B (built), leave B, rest on A. The rest on A calls off B's close, so `prefetch` treats it
        // as a leave, which schedules B's close again.
        let mut prefetch = Prefetch::default();
        let rest = prefetch.rest();
        prefetch.claim(rest, "b").unwrap();
        let close = prefetch.left().unwrap();
        prefetch.rest();
        assert!(!prefetch.is_latest(close));
        let close = prefetch.left().expect("B still gets a close");
        assert!(prefetch.is_latest(close));
        assert_eq!(prefetch.org.as_deref(), Some("b"));
    }

    #[test]
    fn only_a_prefetch_built_on_the_hub_sign_in_uses_up_the_flag_when_shown() {
        let mut prefetch = Prefetch::default();
        let rest = prefetch.rest();
        prefetch.claim(rest, "b").unwrap();
        assert!(!prefetch.started_on_sign_in("b"), "not built yet");
        prefetch.built("b", true);
        assert!(prefetch.started_on_sign_in("b") && !prefetch.started_on_sign_in("c"));
        prefetch.built("c", false);
        assert!(prefetch.started_on_sign_in("b"), "another org's build changes nothing");
        let rest = prefetch.rest();
        prefetch.claim(rest, "b").unwrap();
        assert!(!prefetch.started_on_sign_in("b"), "a new claim starts over");
        prefetch.built("b", true);
        assert!(prefetch.take("b") && !prefetch.started_on_sign_in("b"));
    }

    #[test]
    fn a_leave_while_the_build_waits_or_runs_is_never_lost() {
        // A leave while the rest waits (for `changing`, or for the org list): the claim fails, nothing is built.
        let mut prefetch = Prefetch::default();
        let rest = prefetch.rest();
        assert_eq!(prefetch.left(), None);
        assert_eq!(prefetch.claim(rest, "b"), None);
        assert_eq!(prefetch.org, None);
        // A leave once the claim is made (the build is under way): the org is recorded, so the close is scheduled.
        let rest = prefetch.rest();
        assert_eq!(prefetch.claim(rest, "b"), Some(None));
        let close = prefetch.left().expect("the built page gets a close");
        assert!(prefetch.is_latest(close));
        // A rest on another org replaces this one, and hands back the page to close.
        let rest = prefetch.rest();
        assert_eq!(prefetch.claim(rest, "c"), Some(Some("b".into())));
        assert!(!prefetch.is_latest(close));
    }

    #[test]
    fn a_store_clearing_window_is_never_an_org_page() {
        let (first, second) = (clear_label("o_aaaaaaaa"), clear_label("o_aaaaaaaa"));
        assert_ne!(first, second);
        for label in [&first, &second] {
            assert_eq!(org_of(label), None, "{label} would get the org's capability");
            assert!(!label.starts_with(page_pattern("o_aaaaaaaa").trim_end_matches('*')));
        }
    }

    #[test]
    fn a_tico_sign_in_org_opens_on_the_hub_sign_in_once() {
        let hub = url::Url::parse("https://team.example.com/").unwrap();
        assert_eq!(first_page(hub.clone(), &hub, true).as_str(), "https://team.example.com/auth/login");
        assert_eq!(first_page(hub.clone(), &hub, false), hub);
        let prefixed = url::Url::parse("https://team.example.com/tico/").unwrap();
        assert_eq!(first_page(prefixed.clone(), &prefixed, true).as_str(), "https://team.example.com/tico/auth/login");
        let local = url::Url::parse("http://localhost:8765/api/v2/local-signin?token=t&next=%2F").unwrap();
        assert_eq!(first_page(local.clone(), &url::Url::parse("http://localhost:8765/").unwrap(), false), local);
    }
}

/// Clears one org's cookies and storage when its page is not open: through a hidden window of its own on the org's
/// store, so it works whatever windows are left (removing the last org closes the main window first).
#[cfg(target_os = "macos")]
pub(crate) async fn clear_store(app: &App, org: &Org) -> tauri::Result<()> {
    let Some(home) = Home::of(org) else { return Ok(()) };
    let mut builder = WebviewWindowBuilder::new(app, clear_label(&home.id), WebviewUrl::External("about:blank".parse().unwrap()))
        .visible(false).focused(false).skip_taskbar(true).inner_size(1.0, 1.0);
    if let Some(store) = home.store {
        builder = builder.data_store_identifier(*store.as_bytes());
    }
    let window = builder.build()?;
    let cleared = window.clear_all_browsing_data();
    // WebKit clears on its own time: the window that asked stays a moment so the clearing is not cut short.
    tokio::time::sleep(Duration::from_secs(2)).await;
    let _ = window.close();
    cleared
}

/// A label for a store-clearing window: never an org page's (`org_of` names no org for it), so no capability reaches
/// it, and new each time.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub(crate) fn clear_label(id: &str) -> String {
    static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
    format!("clear-{id}-{}", NEXT.fetch_add(1, Ordering::Relaxed))
}

/// Anything that is not the hub opens in the default browser, except during a sign-in: from the hub's
/// own Cloudflare Access login, or the hub's own sign-in, the whole redirect chain stays in the window
/// whatever identity provider it passes through, so the resulting cookie belongs to the app, not the
/// browser. Each webview keeps its own `signing_in` and its own hub, so a window opened for one org keeps
/// following that org after the person switches to another. Embedded frames navigate through the same
/// handler with nothing to tell them apart, so only the top-level page ends a sign-in, by committing on the
/// hub origin; and only the hub's own logins start one. `page_org` is the org of an org page (the main window's
/// children, which also follow visual links); other windows have none.
fn sign_in_hooks(app: &App, hub: Option<url::Url>, page_org: Option<String>) -> (impl Fn(&url::Url) -> bool + Send + 'static, impl Fn(PageLoadPayload<'_>) + Send + Sync + 'static) {
    let signing_in = Arc::new(AtomicBool::new(false));
    let visuals = page_org.is_some();
    let (opener, page_app) = (app.clone(), app.clone());
    let (committed, page_hub, loaded_org) = (signing_in.clone(), hub.clone(), page_org.clone());
    let navigate = move |url: &url::Url| {
        if let Some(org) = &page_org {
            timing::mark(&opener, org, &format!("nav {}", url.host_str().unwrap_or(url.scheme())));
        }
        stays_in_app(&opener, &signing_in, page_org.as_deref(), hub.as_ref(), url)
    };
    // `Started` is the top-level page committing, with its address; frames never send it.
    let page = move |payload: PageLoadPayload<'_>| {
        let url = payload.url();
        let host = url.host_str().unwrap_or(url.scheme());
        match payload.event() {
            PageLoadEvent::Started => {
                if let Some(org) = &loaded_org {
                    timing::mark(&page_app, org, &format!("load started {host}"));
                }
                top_level_window(&page_app.state::<AppState>(), &committed, visuals, page_hub.as_ref(), url);
            }
            PageLoadEvent::Finished => {
                if let Some(org) = &loaded_org {
                    timing::loaded(&page_app, org, &format!("load finished {host}"));
                    if active_org(&page_app).is_some_and(|open| &open.id == org) {
                        painted(&page_app);
                    }
                }
            }
        }
    };
    (navigate, page)
}

/// Whether a window follows a navigation; one it does not follow opens in the default browser, except from a page
/// that is not on screen (a prefetch), which opens nothing.
fn stays_in_app(opener: &App, signing_in: &AtomicBool, page_org: Option<&str>, hub: Option<&url::Url>, url: &url::Url) -> bool {
    let stays = follow_window(&opener.state::<AppState>(), signing_in, page_org.is_some(), hub, url);
    let on_screen = page_org.is_none_or(|org| active_org(opener).is_some_and(|open| open.id == org));
    if !stays && on_screen {
        let _ = opener.opener().open_url(url.as_str(), None::<&str>);
    }
    stays
}

/// The visual allowance of the page on `hub`: the open page's, the prefetched page's while it is hidden, or a kept
/// page's while it is hidden. No page ever reads or clears another's.
fn own_visual<'a>(open: &'a mut Option<VisualOrigin>, parked: &'a mut Option<VisualOrigin>, kept: &'a mut Kept, hub: Option<&url::Url>) -> Option<&'a mut Option<VisualOrigin>> {
    if open.as_ref().is_some_and(|visual| visual.serves(hub)) {
        Some(open)
    } else if parked.as_ref().is_some_and(|visual| visual.serves(hub)) {
        Some(parked)
    } else {
        kept.visual_for(hub)
    }
}

pub(crate) fn follow_window(state: &AppState, signing_in: &AtomicBool, visuals: bool, hub: Option<&url::Url>, url: &url::Url) -> bool {
    let mut visual = state.visual_origin.lock().unwrap();
    let mut parked = state.parked_visual.lock().unwrap();
    let mut kept = state.kept.lock().unwrap();
    let own = own_visual(&mut visual, &mut parked, &mut kept, hub);
    let allowed = if visuals { own.as_ref().and_then(|own| own.as_ref()) } else { None };
    let stays = follow(hub, allowed, signing_in, url);
    if visuals && (navigation(hub, false, url) == Navigation::Start || session_exit(hub, url)) {
        if let Some(own) = own {
            *own = None;
        }
    }
    stays
}

pub(crate) fn top_level_window(state: &AppState, signing_in: &AtomicBool, visuals: bool, hub: Option<&url::Url>, url: &url::Url) {
    if visuals {
        let mut visual = state.visual_origin.lock().unwrap();
        let mut parked = state.parked_visual.lock().unwrap();
        let mut kept = state.kept.lock().unwrap();
        // A new top-level Hub page must authenticate and send its config again. A permitted
        // visual commit changes neither its allowance nor sign-in state.
        if let Some(own) = own_visual(&mut visual, &mut parked, &mut kept, hub) {
            if !own.as_ref().is_some_and(|visual| visual.permits(hub, url)) {
                *own = None;
            }
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
/// the default browser. An org page (`org_page`) that is not on screen (a prefetch) opens nothing.
fn new_window_handler(app: &App, home: Home, org_page: bool) -> impl Fn(url::Url, NewWindowFeatures) -> NewWindowResponse<tauri::Wry> + Send + 'static {
    let opener = app.clone();
    move |url, _| {
        if org_page && !active_org(&opener).is_some_and(|open| open.id == home.id) {
            return NewWindowResponse::Deny;
        }
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
    let (navigate, page) = sign_in_hooks(app, Some(home.hub.clone()), None);
    #[allow(unused_mut)]
    let mut builder = WebviewWindowBuilder::new(app, &label, WebviewUrl::External(url))
        .title(config::app_name())
        .inner_size(1000.0, 800.0)
        .min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        .user_agent(USER_AGENT)
        .disable_drag_drop_handler()
        .on_new_window(new_window_handler(app, home.clone(), false))
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

/// Remembers which org a window belongs to, so removing the org closes it. A window built after its org was removed
/// closes at once: the check and the record happen under the org list's lock, which removal takes first.
fn owned_by(app: &App, label: &str, org: &str) {
    let state = app.state::<AppState>();
    let list = state.orgs.lock().unwrap();
    if list.get(org).is_some() {
        state.owned.lock().unwrap().push((label.to_string(), org.to_string()));
    } else if let Some(window) = app.get_webview_window(label) {
        let _ = window.close();
    }
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
    let (navigate, page) = sign_in_hooks(app, Some(home.hub.clone()), None);
    #[cfg_attr(not(target_os = "macos"), allow(unused_mut))]
    let mut builder = WebviewWindowBuilder::new(app, &label, WebviewUrl::External(url))
        .title("Meeting")
        .inner_size(1100.0, 820.0)
        .min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        .resizable(true)
        .user_agent(USER_AGENT)
        .disable_drag_drop_handler()
        .on_new_window(new_window_handler(app, home.clone(), false))
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

/// What the window-state plugin keeps of the main window between launches: its size and place. Visibility is left
/// out: the window is built hidden and `show_window` shows it once the app is ready. Maximized and full screen are
/// left out too: on quit the plugin refreshes only single-webview windows, and the main window holds several, so
/// those two would be saved only when the window was closed first.
pub(crate) fn frame_flags() -> StateFlags {
    StateFlags::SIZE | StateFlags::POSITION
}

/// Frames are compared in points on macOS, where one point space spans every display whatever its scale, and in
/// physical pixels elsewhere, where the virtual screen is the consistent space.
const POINTS: bool = cfg!(target_os = "macos");

fn unit(scale: f64) -> f64 {
    if POINTS && scale > 0.0 { scale } else { 1.0 }
}

/// A frame is usable when enough of its title bar lies on a connected display for the person to grab it. Frame and
/// work areas are (x, y, width, height) in the same units.
fn frame_reachable(frame: (f64, f64, f64, f64), areas: &[(f64, f64, f64, f64)]) -> bool {
    let (x, y, w, _) = frame;
    areas.iter().any(|&(ax, ay, aw, ah)| {
        let (left, right) = (x.max(ax), (x + w).min(ax + aw));
        let (top, bottom) = (y.max(ay), (y + 24.0).min(ay + ah));
        right - left >= 80f64.min(w) && bottom > top
    })
}

/// Room left above a window's content for its title bar, in points (logical pixels).
const TITLE_BAR: f64 = 28.0;

/// The plugin's saved frame for the main window, from its own file: outer position and content size, in physical
/// pixels. None when there is none yet.
fn saved_frame(app: &App) -> Option<(f64, f64, f64, f64)> {
    use tauri_plugin_window_state::AppHandleExt;
    let path = app.path().app_config_dir().ok()?.join(app.filename());
    let states: Value = serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()?;
    let main = states.get("main")?;
    let n = |key: &str| main.get(key).and_then(Value::as_f64);
    let frame = (n("x")?, n("y")?, n("width")?, n("height")?);
    (frame.2 > 0.0 && frame.3 > 0.0).then_some(frame)
}

/// The default frame, fitted to a work area (x, y, width, height in points) and centred in it: 1240 by 820 where
/// that fits, never taller than the area leaves room for, so its title bar is always on the display.
fn fallback_frame((ax, ay, aw, ah): (f64, f64, f64, f64)) -> (f64, f64, f64, f64) {
    let (w, h) = (1240f64.min(aw), 820f64.min(ah - TITLE_BAR).max(1.0));
    (ax + (aw - w) / 2.0, ay + ((ah - h - TITLE_BAR) / 2.0).max(0.0), w, h)
}

/// The plugin brings back a saved frame when any corner lies on a display, which can leave the title bar off screen
/// after a display is unplugged. Such a frame gives way to the default one on the primary display. The saved frame is
/// read from the plugin's file because on macOS the plugin's move and resize are queued, so the window does not have
/// that frame yet; these are queued after the plugin's, so they are the ones that land.
fn keep_title_bar_reachable(app: &App, window: &Window) {
    let Some((x, y, w, h)) = saved_frame(app) else { return };
    let u = unit(window.scale_factor().unwrap_or(1.0));
    let monitors = window.available_monitors().unwrap_or_default();
    let areas: Vec<_> = monitors.iter().map(|m| {
        let (area, u) = (m.work_area(), unit(m.scale_factor()));
        (area.position.x as f64 / u, area.position.y as f64 / u, area.size.width as f64 / u, area.size.height as f64 / u)
    }).collect();
    if areas.is_empty() || frame_reachable((x / u, y / u, w / u, h / u + TITLE_BAR), &areas) {
        return;
    }
    let Some(monitor) = window.primary_monitor().ok().flatten().or_else(|| monitors.into_iter().next()) else { return };
    // Fitted in the primary display's points (logical pixels).
    let (area, scale) = (monitor.work_area(), monitor.scale_factor().max(f64::EPSILON));
    let (fx, fy, fw, fh) = fallback_frame((area.position.x as f64 / scale, area.position.y as f64 / scale,
                                           area.size.width as f64 / scale, area.size.height as f64 / scale));
    if POINTS {
        // macOS points are one space across every display.
        let _ = window.set_size(LogicalSize::new(fw, fh));
        let _ = window.set_position(LogicalPosition::new(fx, fy));
    } else {
        // Elsewhere a logical value is converted with the window's own scale, which is the display the plugin put it
        // on, not the primary one. Physical pixels on the primary display land where they are meant to. Moved first:
        // Windows rescales a window that changes display, so the size is set once it is on the primary one.
        let _ = window.set_position(PhysicalPosition::new((fx * scale).round() as i32, (fy * scale).round() as i32));
        let _ = window.set_size(PhysicalSize::new((fw * scale).round() as u32, (fh * scale).round() as u32));
    }
}

/// The window-state plugin tracks only the main window. Org pages, meetings and the setup window keep no frame.
pub(crate) fn tracks_frame(label: &str) -> bool {
    label == "main"
}

#[cfg(test)]
mod window_state_tests {
    use super::*;

    #[test]
    fn only_the_main_window_keeps_its_frame() {
        assert!(tracks_frame("main"));
        for label in ["server", "hub-3", "meeting-o_7f3a0001-abc123"] {
            assert!(!tracks_frame(label), "{label} must not keep a frame");
        }
    }

    #[test]
    fn the_app_decides_when_the_main_window_shows() {
        let flags = frame_flags();
        assert!(!flags.contains(StateFlags::VISIBLE));
        assert_eq!(flags.bits(), (StateFlags::SIZE | StateFlags::POSITION).bits());
    }

    #[test]
    fn a_saved_frame_is_kept_only_where_its_title_bar_can_be_grabbed() {
        let laptop = (0.0, 25.0, 1512.0, 920.0);
        let external = (1512.0, -200.0, 2560.0, 1415.0);
        assert!(frame_reachable((100.0, 100.0, 1240.0, 820.0), &[laptop]));
        assert!(frame_reachable((1700.0, 0.0, 1240.0, 820.0), &[laptop, external]));
        // The external display was unplugged.
        assert!(!frame_reachable((1700.0, 0.0, 1240.0, 820.0), &[laptop]));
        // Title bar above the work area (its lower corners still on the laptop), or almost entirely off the right edge.
        assert!(!frame_reachable((100.0, -500.0, 1000.0, 820.0), &[laptop]));
        assert!(!frame_reachable((1480.0, 100.0, 1240.0, 820.0), &[laptop]));
        assert!(frame_reachable((1400.0, 100.0, 1240.0, 820.0), &[laptop]));
        assert!(!frame_reachable((100.0, 100.0, 1240.0, 820.0), &[]));
    }

    #[test]
    fn the_fallback_frame_fits_a_small_display_with_its_title_bar_on_it() {
        // 1366x768 with a taskbar: the work area is shorter than the default window.
        let small = (0.0, 0.0, 1366.0, 728.0);
        let (x, y, w, h) = fallback_frame(small);
        assert!(h + TITLE_BAR <= 728.0 && w <= 1366.0);
        assert!(frame_reachable((x, y, w, h + TITLE_BAR), &[small]));
        // A large display gets the default size, centred.
        let (x, y, w, h) = fallback_frame((0.0, 25.0, 2560.0, 1415.0));
        assert_eq!((w, h), (1240.0, 820.0));
        assert_eq!(x, 660.0);
        assert!(y > 25.0 && frame_reachable((x, y, w, h), &[(0.0, 25.0, 2560.0, 1415.0)]));
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
