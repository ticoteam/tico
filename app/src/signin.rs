//! Sign in to Tico: one sign-in, then the person picks orgs from their account, instead of typing each address.
//!
//! The sign-in window shows Tico Cloud's portal page (`{PORTAL}/desktop/orgs`) on a data store of its own, made fresh
//! for each sign-in, so it starts signed out and shares nothing with any org. It gets no IPC: no capability names it.
//! When the person submits, the portal navigates to `{PORTAL}/desktop/orgs/done?issuer=<origin>&org=<server>...`. That
//! navigation is cancelled and read here: each picked server becomes an org on a store of its own, and on macOS the
//! sign-in's cookies for the issuer's host (only that host: never Google's or the portal's) are copied into each new
//! store before its page loads. The org's server then redirects to the issuer, which finds that session and sends the
//! page back signed in. The sign-in window and its store go once it is done or closed.


use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
#[cfg(target_os = "macos")]
use std::time::Duration;

use tauri::webview::{NewWindowFeatures, NewWindowResponse};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;
use url::Url;
use uuid::Uuid;

use crate::config;
use crate::orgs::Org;
use crate::windows::{show_window, switch_to};
use crate::{orgs, App, AppState, MIN_HEIGHT, MIN_WIDTH, USER_AGENT};

/// The start of every sign-in window's label: `tico-signin-` and its store id. No capability names one, so its pages
/// get no IPC. Each sign-in has a label of its own, so a sign-in that is finishing never touches a later one's window.
pub(crate) const SIGNIN: &str = "tico-signin-";
const DEFAULT_PORTAL: &str = "https://app.tico.dev";
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
/// Store ids that may be on disk with nobody to remove them: a sign-in window's, and new orgs' stores until the org is
/// saved. A run that quits (Cmd-Q) before they go leaves them here, and the next launch removes them.
const PENDING_FILE: &str = "signin-stores.txt";
/// How long the cookie copy may take before the orgs are added anyway (their pages then ask to sign in again).
#[cfg(target_os = "macos")]
const COPY_TIMEOUT: Duration = Duration::from_secs(10);

/// One sign-in window: its label, its own data store (macOS), whether its picks are being handled (so a second submit
/// is ignored), and whether its window has gone (so its picks are dropped).
struct Session {
    label: String,
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    store: Uuid,
    handled: AtomicBool,
    cancelled: AtomicBool,
}

/// The sign-in windows that are open, by label.
static SESSIONS: Mutex<Option<HashMap<String, Arc<Session>>>> = Mutex::new(None);

fn sessions<T>(f: impl FnOnce(&mut HashMap<String, Arc<Session>>) -> T) -> T {
    f(SESSIONS.lock().unwrap().get_or_insert_with(HashMap::new))
}

/// Tico Cloud's portal: `TICO_PORTAL_URL` (tests, development) or app.tico.dev. Read once.
pub(crate) fn portal() -> &'static Url {
    static PORTAL: OnceLock<Url> = OnceLock::new();
    PORTAL.get_or_init(|| portal_from(std::env::var("TICO_PORTAL_URL").ok().as_deref()))
}

/// The portal for a `TICO_PORTAL_URL` value: HTTPS, or HTTP on loopback, like a typed server address.
fn portal_from(env: Option<&str>) -> Url {
    if let Some(raw) = env.filter(|raw| !raw.trim().is_empty()) {
        match config::validate_url(raw) {
            Ok(portal) => return portal,
            Err(e) => log::warn!("TICO_PORTAL_URL is not used: {e}"),
        }
    }
    config::validate_url(DEFAULT_PORTAL).unwrap()
}

/// The portal page that signs the person in and lists their orgs.
pub(crate) fn orgs_page(portal: &Url) -> Url {
    portal.join("desktop/orgs").unwrap()
}

/// Whether a navigation is the portal's done page: the portal's own origin, and exactly its path.
pub(crate) fn is_done(portal: &Url, url: &Url) -> bool {
    let done = portal.join("desktop/orgs/done").unwrap();
    url.origin() == portal.origin() && url.path() == done.path()
}

/// What the person picked on the portal.
#[derive(Debug, PartialEq)]
pub(crate) struct Picks {
    /// The Tico accounts origin the picked servers trust for sign-in.
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    pub issuer: Url,
    /// The picked servers, each valid as a typed address and on an origin of its own, in the portal's order.
    pub orgs: Vec<Url>,
}

/// The picks a done page carries. None without exactly one usable issuer. Each `org` value must pass the same check
/// as an address typed in Add organization; the others are dropped, and so are repeats of an origin.
pub(crate) fn picks(portal: &Url, url: &Url) -> Option<Picks> {
    let issuers: Vec<String> = url.query_pairs().filter(|(key, _)| key == "issuer").map(|(_, value)| value.into_owned()).collect();
    let [raw] = issuers.as_slice() else { return None };
    let issuer = issuer(raw, portal)?;
    let mut seen = HashSet::new();
    let orgs = url.query_pairs()
        .filter(|(key, _)| key == "org")
        .filter_map(|(_, value)| config::validate_url(&value).ok())
        .filter(|hub| seen.insert(hub.origin().ascii_serialization()))
        .collect();
    Some(Picks { issuer, orgs })
}

fn loopback(host: &str) -> bool {
    matches!(host, "localhost" | "127.0.0.1")
}

/// An issuer is an origin: HTTPS, or HTTP on loopback (as for a typed address), with no path. Its cookies are the ones
/// copied, so it may not be Google, nor the portal's own host (loopback aside, where a test runs both).
fn issuer(raw: &str, portal: &Url) -> Option<Url> {
    let issuer = config::validate_url(raw).ok()?;
    let host = issuer.host_str()?.to_ascii_lowercase();
    if issuer.path() != "/" || host == "google.com" || host.ends_with(".google.com") {
        return None;
    }
    if !loopback(&host) && portal.host_str().is_some_and(|portal| portal.eq_ignore_ascii_case(&host)) {
        return None;
    }
    Some(issuer)
}

/// Whether a cookie belongs to the issuer: its domain, less one leading dot, is the issuer's host. A parent domain or
/// a subdomain is someone else's.
pub(crate) fn issuer_cookie(domain: &str, host: &str) -> bool {
    !host.is_empty() && domain.strip_prefix('.').unwrap_or(domain).eq_ignore_ascii_case(host)
}

/// "Sign in to Tico" on the setup page: opens the sign-in window, or brings it forward when it is open.
#[tauri::command]
pub(crate) async fn tico_signin(app: App) -> Result<(), String> {
    if !orgs::multi_org_build() {
        return Err("This app uses its built-in server.".into());
    }
    open_window(&app).map_err(|e| e.to_string())
}

/// Whether a window label is a sign-in window's.
pub(crate) fn is_signin(label: &str) -> bool {
    label.starts_with(SIGNIN)
}

fn open_window(app: &App) -> tauri::Result<()> {
    if let Some(window) = app.webview_windows().into_iter().find(|(label, _)| is_signin(label)).map(|(_, window)| window) {
        let _ = window.unminimize();
        window.show()?;
        window.set_focus()?;
        return Ok(());
    }
    let portal = portal().clone();
    let store = Uuid::new_v4();
    let session = Arc::new(Session {
        label: format!("{SIGNIN}{}", store.simple()),
        store,
        handled: AtomicBool::new(false),
        cancelled: AtomicBool::new(false),
    });
    let (handler, mine) = (app.clone(), session.clone());
    #[cfg_attr(not(target_os = "macos"), allow(unused_mut))]
    let mut builder = WebviewWindowBuilder::new(app, &session.label, WebviewUrl::External(orgs_page(&portal)))
        .title("Sign in to Tico")
        .inner_size(480.0, 680.0)
        .min_inner_size(MIN_WIDTH as f64, MIN_HEIGHT as f64)
        .user_agent(USER_AGENT)
        .on_new_window(new_window_handler(app))
        // Every navigation stays (sign-in goes through the accounts site and Google) except the done page, which
        // never loads: its picks are read here.
        .on_navigation(move |url| {
            if !is_done(&portal, url) {
                return true;
            }
            if !mine.handled.swap(true, Ordering::SeqCst) {
                let (app, session, picks) = (handler.clone(), mine.clone(), picks(&portal, url));
                // Off the navigation callback, which runs on the main thread: reading and copying cookies is waited on.
                tauri::async_runtime::spawn(async move { finish(&app, &session, picks).await });
            }
            false
        });
    #[cfg(target_os = "macos")]
    {
        // Noted before the store exists, so a quit while the window is open still lets the next launch remove it.
        note_pending(app, &[store], &[]);
        builder = builder.data_store_identifier(*store.as_bytes());
    }
    sessions(|open| open.insert(session.label.clone(), session.clone()));
    if let Err(e) = builder.build() {
        sessions(|open| open.remove(&session.label));
        return Err(e);
    }
    Ok(())
}

/// A link that asks for a new window (terms, help) opens in the default browser; the sign-in window opens none.
fn new_window_handler(app: &App) -> impl Fn(Url, NewWindowFeatures) -> NewWindowResponse<tauri::Wry> + Send + 'static {
    let opener = app.clone();
    move |url, _| {
        if matches!(url.scheme(), "http" | "https") {
            let _ = opener.opener().open_url(url.as_str(), None::<&str>);
        }
        NewWindowResponse::Deny
    }
}

/// A sign-in window closed (done, cancelled or closed by the person): a finish still running for it drops its picks,
/// and its data store goes.
pub(crate) fn closed(app: &App, label: &str) {
    if !is_signin(label) {
        return;
    }
    let Some(session) = sessions(|open| open.remove(label)) else { return };
    session.cancelled.store(true, Ordering::SeqCst);
    #[cfg(target_os = "macos")]
    {
        let app = app.clone();
        tauri::async_runtime::spawn(async move { discard(&app, &[session.store]).await });
    }
    #[cfg(not(target_os = "macos"))]
    let _ = app;
}

/// Closes every sign-in window (Cancel on the setup page).
pub(crate) fn close(app: &App) {
    for (label, window) in app.webview_windows() {
        if is_signin(&label) {
            let _ = window.close();
        }
    }
}

/// Removes stores nobody uses (a sign-in's, or ones prepared for orgs that were not added), and forgets them once gone.
/// A page that just closed holds its store for a moment, so removal is tried a few times.
#[cfg_attr(not(target_os = "macos"), allow(unused_variables))]
async fn discard(app: &App, stores: &[Uuid]) {
    #[cfg(target_os = "macos")]
    for store in stores {
        if !delete_store(app, *store).await {
            log::warn!("could not delete a data store that is no longer used; the next launch tries again");
        }
    }
}

/// A removed org's store is to be deleted: noted first, so that the next launch deletes it if this run cannot.
#[cfg(target_os = "macos")]
pub(crate) fn note_removed_store(app: &App, store: Uuid) {
    note_pending(app, &[store], &[]);
}

/// Deletes a store nobody uses and forgets it, trying a few times; false (and still noted) when it is still in use.
#[cfg(target_os = "macos")]
pub(crate) async fn delete_store(app: &App, store: Uuid) -> bool {
    release_store(app, store);
    let deleted = remove_store(app, store).await;
    if deleted {
        note_pending(app, &[], &[store]);
    }
    deleted
}

#[cfg(target_os = "macos")]
async fn remove_store(app: &App, store: Uuid) -> bool {
    for _ in 0..8 {
        if app.remove_data_store(*store.as_bytes()).await.is_ok() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    false
}

/// Whether a server has the hub's own sign-in (`/auth/login`, TICO_AUTH_PROXY=oidc), so an org added through Tico
/// sign-in opens there and goes straight in on the copied session instead of showing the hub's sign-in page. Asked
/// with a GET, as the hub serves and exempts only GET there; no redirect is followed, no cookie is sent and the body
/// is never read (the hub's answer starts a sign-in only by a signed cookie, which is dropped).
async fn has_login(hub: Url, issuer: Url) -> bool {
    let Ok(http) = reqwest::Client::builder().timeout(std::time::Duration::from_secs(5))
        .redirect(reqwest::redirect::Policy::none()).no_proxy().build() else { return false };
    let Ok(login) = hub.join(LOGIN_PATH) else { return false };
    let Ok(response) = http.get(login.clone()).send().await else { return false };
    let location = response.headers().get(reqwest::header::LOCATION).and_then(|value| value.to_str().ok())
        .and_then(|value| login.join(value).ok());
    login_answer(response.status().as_u16(), location.as_ref(), &issuer)
}

/// The hub's sign-in path, relative to its address (backend/oidc.py LOGIN_PATH).
pub(crate) const LOGIN_PATH: &str = "auth/login";

/// What the answer to `GET /auth/login` says. Only a redirect into a sign-in means yes: to the Tico issuer that just
/// signed the person in, or to the hub's own `/auth/login` at its public address. Anything else (404 with OIDC off,
/// Cloudflare Access in front, an error page, no answer) opens the root, as before.
fn login_answer(status: u16, location: Option<&Url>, issuer: &Url) -> bool {
    let Some(location) = location.filter(|_| (300..400).contains(&status)) else { return false };
    let host = location.host_str().unwrap_or_default();
    if host.ends_with(".cloudflareaccess.com") || !matches!(location.scheme(), "https" | "http") {
        return false;
    }
    (!host.is_empty() && issuer.host_str() == Some(host)) || location.path().ends_with("/auth/login")
}

/// Adds the picked servers as orgs, signed in through the issuer's session, and opens the first new one (or, when
/// every pick is already an org, the first of those). A pick that is an org already gets the fresh session too, so an
/// expired one signs in again. Only this sign-in's own window is used or closed, and if that window goes before the
/// orgs are added, nothing is added or renewed.
async fn finish(app: &App, session: &Session, picks: Option<Picks>) {
    let done = || {
        if let Some(window) = app.get_webview_window(&session.label) {
            let _ = window.close();
        }
    };
    let Some(picks) = picks.filter(|picks| !picks.orgs.is_empty()) else {
        log::warn!("Tico sign-in finished with no usable organization");
        return done();
    };
    // A store for each pick that is not an org yet, so the session is in the store before the org exists. Whether a
    // pick is new is decided again when the orgs are added.
    let fresh: Vec<(Url, Uuid)> = {
        let state = app.state::<AppState>();
        let list = state.orgs.lock().unwrap();
        picks.orgs.iter().filter(|hub| list.find_hub(hub).is_none()).map(|hub| (hub.clone(), Uuid::new_v4())).collect()
    };
    let prepared: Vec<Uuid> = fresh.iter().map(|(_, store)| *store).collect();
    // Whether each new server has the hub's own sign-in, asked while the session is copied.
    let probes: Vec<_> = fresh.iter().map(|(hub, _)| tauri::async_runtime::spawn(has_login(hub.clone(), picks.issuer.clone()))).collect();
    // The picks that are orgs already, asked the same.
    let known: Vec<(String, Url)> = {
        let state = app.state::<AppState>();
        let list = state.orgs.lock().unwrap();
        picks.orgs.iter().filter_map(|hub| list.find_hub(hub).map(|org| (org.id.clone(), hub.clone()))).collect()
    };
    let known_probes: Vec<_> = known.iter().map(|(_, hub)| tauri::async_runtime::spawn(has_login(hub.clone(), picks.issuer.clone()))).collect();
    #[cfg(target_os = "macos")]
    if !fresh.is_empty() {
        note_pending(app, &prepared, &[]);
        let host = picks.issuer.host_str().unwrap_or_default().to_string();
        match app.get_webview_window(&session.label) {
            Some(window) => match copy_cookies(&window, host, prepared.iter().map(|store| (Some(*store), None)).collect()).await {
                Some(count) => log::info!("Tico sign-in: {count} session cookie(s) copied to {} new organization(s)", fresh.len()),
                None => log::warn!("Tico sign-in: the session could not be copied; the new organizations ask to sign in"),
            },
            None => log::warn!("Tico sign-in: the window closed before its session was copied"),
        }
    }
    let mut fresh_flagged = Vec::with_capacity(fresh.len());
    for ((hub, store), probe) in fresh.into_iter().zip(probes) {
        fresh_flagged.push((hub, store, probe.await.unwrap_or(false)));
    }
    let fresh = fresh_flagged;
    // Added under the list's lock, after checking once more that this sign-in was not cancelled meanwhile, and leaving
    // out any server that became an org meanwhile (added by address, say).
    let committed = {
        let state = app.state::<AppState>();
        let mut list = state.orgs.lock().unwrap();
        if session.cancelled.load(Ordering::SeqCst) {
            None
        } else {
            Some(list.add_all(&fresh).map(|added| {
                let open = added.first().map(|org| org.id.clone())
                    .or_else(|| picks.orgs.iter().find_map(|hub| list.find_hub(hub).map(|org| org.id.clone())));
                (added, open)
            }))
        }
    };
    match committed {
        None => {
            log::info!("Tico sign-in was closed before its organizations were added; nothing was added");
            discard(app, &prepared).await;
        }
        Some(Err(e)) => {
            discard(app, &prepared).await;
            app.dialog().message(e).title("Add organization").show(|_| {});
            done();
        }
        Some(Ok((added, open))) => {
            let (kept, unused) = split_stores(&prepared, &added);
            // A saved org owns its store from here on: removing the org removes it.
            #[cfg(target_os = "macos")]
            note_pending(app, &[], &kept);
            #[cfg(not(target_os = "macos"))]
            let _ = kept;
            discard(app, &unused).await;
            let mut probed = Vec::with_capacity(known.len());
            for ((id, hub), probe) in known.into_iter().zip(known_probes) {
                probed.push((id, hub, probe.await.unwrap_or(false)));
            }
            let renewed = renew(app, session, &picks.issuer, &probed).await;
            // Pages open now load again on the new session; one the switch below opens starts on it anyway. Where each
            // goes is decided here, before the switch can use up an org's flag.
            let open_pages: Vec<(String, bool)> = renewed.into_iter().filter(|(id, _)| crate::windows::org_page(app, id).is_some()).collect();
            if let Some(id) = open {
                if let Err(e) = switch_to(app, &id) {
                    log::warn!("could not open the organization: {e}");
                }
                show_window(app);
                if let Some(setup) = app.get_webview_window("server") {
                    let _ = setup.close();
                }
            }
            for (id, login) in &open_pages {
                crate::windows::renew_page(app, id, *login);
            }
            done();
        }
    }
}

/// Held while a session is renewed into orgs already added (`renew`) and while an org is removed (main.rs
/// `remove_org`, up to the moment its page is closed and its store noted): a removal waits for a renewal under way, so
/// no fresh session is written into a store whose org is going.
pub(crate) static RENEWING: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

/// Puts this sign-in's session into the stores of the picks that were orgs already, each into that org's own store
/// (the default store for the org moved over from server.txt), and has each open on the hub's sign-in next where it
/// has one. The orgs whose session was renewed, and whether each has the hub's sign-in.
#[cfg_attr(not(target_os = "macos"), allow(unused_variables))]
async fn renew(app: &App, session: &Session, issuer: &Url, known: &[(String, Url, bool)]) -> Vec<(String, bool)> {
    let _renewing = RENEWING.lock().await;
    let targets = {
        let state = app.state::<AppState>();
        let list = state.orgs.lock().unwrap();
        renew_targets(list.orgs(), known)
    };
    if targets.is_empty() {
        return Vec::new();
    }
    #[cfg(target_os = "macos")]
    {
        let host = issuer.host_str().unwrap_or_default().to_string();
        let Some(window) = app.get_webview_window(&session.label) else { return Vec::new() };
        // Noted before any write, so that a store whose org goes meanwhile (or a quit) is still cleaned at launch; a
        // store an org still owns is never removed there, and is forgotten again below.
        let stores: Vec<Uuid> = targets.iter().filter_map(|(_, store, _)| *store).collect();
        note_pending(app, &stores, &[]);
        let copied = copy_cookies(&window, host, targets.iter().map(|(id, store, _)| (*store, Some(id.clone()))).collect()).await;
        let state = app.state::<AppState>();
        let mut list = state.orgs.lock().unwrap();
        let owned: Vec<Uuid> = stores.iter().copied().filter(|store| list.orgs().iter().any(|org| org.store_id() == Some(*store))).collect();
        note_pending(app, &[], &owned);
        if copied.is_none() {
            log::warn!("Tico sign-in: the session could not be copied to the organizations already added");
            return Vec::new();
        }
        log::info!("Tico sign-in: the session was copied to {} organization(s) already added", targets.len());
        let renewed: Vec<(String, bool)> = targets.into_iter()
            .filter(|(id, store, _)| owns_store(list.orgs(), id, *store))
            .map(|(id, _, login)| (id, login)).collect();
        for (id, login) in &renewed {
            if *login {
                list.mark_sign_in(id);
            }
        }
        renewed
    }
    // Only macOS gives an org a store of its own, which the session is copied into.
    #[cfg(not(target_os = "macos"))]
    Vec::new()
}

/// Whether org `id` is still in the list on `store` (`None`: the default store). Checked again as each renewed
/// session is written, so an org removed meanwhile gets nothing.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn owns_store(orgs: &[Org], id: &str, store: Option<Uuid>) -> bool {
    orgs.iter().any(|org| org.id == id && org.store_id() == store)
}

/// Where a renewed session goes: each pick `(org id, server, has the hub's sign-in)` that is still that org, at that
/// server, with the org's own store (`None`: the default store, which only that org uses).
fn renew_targets(orgs: &[Org], known: &[(String, Url, bool)]) -> Vec<(String, Option<Uuid>, bool)> {
    known.iter().filter_map(|(id, hub, login)| {
        let org = orgs.iter().find(|org| &org.id == id)?;
        let same = org.hub().is_some_and(|at| at.origin() == hub.origin());
        same.then(|| (id.clone(), org.store_id(), *login))
    }).collect()
}

/// The prepared stores that orgs were added on, and the ones left unused.
fn split_stores(prepared: &[Uuid], added: &[Org]) -> (Vec<Uuid>, Vec<Uuid>) {
    let used: HashSet<Uuid> = added.iter().filter_map(Org::store_id).collect();
    prepared.iter().copied().partition(|store| used.contains(store))
}

// ----------------------------------------------------------------------------- stores left by a run that quit
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn pending_path(app: &App) -> Option<PathBuf> {
    app.path().app_data_dir().ok().map(|dir| dir.join(PENDING_FILE))
}

#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
static PENDING: Mutex<()> = Mutex::new(());

#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
/// The store ids in the pending file; lines that are not one are ignored.
fn read_pending(path: &Path) -> Vec<Uuid> {
    std::fs::read_to_string(path).unwrap_or_default().lines().filter_map(|line| Uuid::parse_str(line.trim()).ok()).collect()
}

#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
/// Adds and removes store ids in the pending file; the file goes when none is left.
fn update_pending(path: &Path, add: &[Uuid], remove: &[Uuid]) -> std::io::Result<()> {
    let _lock = PENDING.lock().unwrap();
    let mut stores = read_pending(path);
    stores.retain(|store| !remove.contains(store));
    for store in add {
        if !stores.contains(store) && !remove.contains(store) {
            stores.push(*store);
        }
    }
    if stores.is_empty() {
        return match std::fs::remove_file(path) {
            Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(e),
            _ => Ok(()),
        };
    }
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let text: String = stores.iter().map(|store| format!("{store}\n")).collect();
    let temporary = path.with_extension("tmp");
    std::fs::write(&temporary, text).and_then(|_| std::fs::rename(&temporary, path))
}

#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn note_pending(app: &App, add: &[Uuid], remove: &[Uuid]) {
    let Some(path) = pending_path(app) else { return };
    if let Err(e) = update_pending(&path, add, remove) {
        log::warn!("could not note the sign-in's data stores: {e}");
    }
}

#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
/// The pending stores no org uses: those are safe to remove. A store an org was saved on is never one of them, even
/// if the run quit before forgetting it.
fn stale_stores(pending: &[Uuid], orgs: &[Org]) -> Vec<Uuid> {
    let owned: HashSet<Uuid> = orgs.iter().filter_map(Org::store_id).collect();
    pending.iter().copied().filter(|store| !owned.contains(store)).collect()
}

/// At launch: removes the stores a run that quit left behind (a sign-in window open at Cmd-Q, or orgs being added).
#[cfg(target_os = "macos")]
pub(crate) fn clean_up_last_run(app: &App) {
    let Some(path) = pending_path(app) else { return };
    let pending = read_pending(&path);
    if pending.is_empty() {
        return;
    }
    let stale = {
        let state = app.state::<AppState>();
        let list = state.orgs.lock().unwrap();
        // Only against every saved org: with orgs.json unread (HUB_URL) or unreadable, a store a saved org uses could
        // look unused, so the stores and the file are left for a launch that reads the list.
        if !list.is_complete() {
            return;
        }
        stale_stores(&pending, list.orgs())
    };
    let owned: Vec<Uuid> = pending.iter().copied().filter(|store| !stale.contains(store)).collect();
    note_pending(app, &[], &owned);
    let app = app.clone();
    tauri::async_runtime::spawn(async move { discard(&app, &stale).await });
}

// The stores cookies were copied into, held on the main thread until each org's page has opened on its store: WebKit
// keeps a store's session cookies only while the store is alive, and an issuer's session cookie may be one.
#[cfg(target_os = "macos")]
thread_local! {
    static KEPT: std::cell::RefCell<std::collections::HashMap<Uuid, objc2::rc::Retained<objc2_web_kit::WKWebsiteDataStore>>> =
        Default::default();
}

/// Lets go of a store held since its cookies were copied: its org's page has opened on it, or the org is gone.
#[cfg(target_os = "macos")]
pub(crate) fn release_store(app: &App, store: Uuid) {
    let _ = app.run_on_main_thread(move || {
        KEPT.with(|kept| kept.borrow_mut().remove(&store));
    });
}

/// Copies the sign-in window's cookies for `host` into each of `stores` (`None` is the default store, which only the
/// org moved over from server.txt uses), as the same NSHTTPCookie objects. A store given with its org's id is written
/// only if that org still has it when the cookies come (`owns_store`). (they are
/// HttpOnly, so they are not rebuilt). The count of cookies copied, or None when WebKit did not finish in time; a
/// WebKit answer that comes after that does nothing. Everything WebKit runs on the main thread, which hands each call
/// to a callback and returns at once; only the answer is awaited here (see cookies.rs for why the main thread never
/// waits).
#[cfg(target_os = "macos")]
async fn copy_cookies(window: &tauri::WebviewWindow, host: String, stores: Vec<(Option<Uuid>, Option<String>)>) -> Option<usize> {
    use block2::RcBlock;
    use objc2::MainThreadMarker;
    use objc2_foundation::{NSArray, NSHTTPCookie, NSUUID};
    use objc2_web_kit::{WKWebView, WKWebsiteDataStore};
    use std::cell::Cell;
    use std::ptr::NonNull;
    use std::rc::Rc;

    let (sender, mut answer) = tokio::sync::mpsc::unbounded_channel::<usize>();
    let late = Arc::new(AtomicBool::new(false));
    let too_late = late.clone();
    let app = window.app_handle().clone();
    window.with_webview(move |platform| unsafe {
        let Some(mtm) = MainThreadMarker::new() else { return };
        // `inner` is the sign-in page's live WKWebView while this closure runs; its store is the sign-in's own.
        let view = &*platform.inner().cast::<WKWebView>();
        let source = view.configuration().websiteDataStore().httpCookieStore();
        let reply = RcBlock::new(move |cookies: NonNull<NSArray<NSHTTPCookie>>| {
            // After the timeout the orgs are added without the session, and their pages may already have let go of
            // their stores: nothing is copied or held then.
            if too_late.load(Ordering::SeqCst) {
                return;
            }
            let picked: Vec<_> = cookies.as_ref().to_vec().into_iter()
                .filter(|cookie| issuer_cookie(&cookie.domain().to_string(), &host))
                .collect();
            // Checked here, on the main thread, as the cookies are written: an org removed since it was picked gets none.
            let stores: Vec<Option<Uuid>> = {
                let state = app.state::<AppState>();
                let list = state.orgs.lock().unwrap();
                stores.iter().filter(|(store, owner)| owner.as_deref().is_none_or(|id| owns_store(list.orgs(), id, *store)))
                    .map(|(store, _)| *store).collect()
            };
            let left = Rc::new(Cell::new(picked.len() * stores.len()));
            if left.get() == 0 {
                let _ = sender.send(0);
                return;
            }
            for store in &stores {
                let target = match store {
                    // The org's store, by the id its page will open with (macOS 14), held until that page opens.
                    Some(store) => {
                        let target = WKWebsiteDataStore::dataStoreForIdentifier(&NSUUID::from_bytes(*store.as_bytes()), mtm);
                        KEPT.with(|kept| kept.borrow_mut().insert(*store, target.clone()));
                        target
                    }
                    None => WKWebsiteDataStore::defaultDataStore(mtm),
                };
                let jar = target.httpCookieStore();
                for cookie in &picked {
                    let (left, sender, count) = (left.clone(), sender.clone(), picked.len());
                    let stored = RcBlock::new(move || {
                        left.set(left.get() - 1);
                        if left.get() == 0 {
                            let _ = sender.send(count);
                        }
                    });
                    jar.setCookie_completionHandler(cookie, Some(&*stored));
                }
            }
        });
        source.getAllCookies(&reply);
    }).ok()?;
    let copied = tokio::time::timeout(COPY_TIMEOUT, answer.recv()).await.ok().flatten();
    if copied.is_none() {
        // Set on the main thread, where the answer runs, and waited for: once this returns, no answer is still
        // writing, and a later one writes nothing.
        let (set, was_set) = tokio::sync::oneshot::channel();
        let flag = late.clone();
        let queued = window.run_on_main_thread(move || {
            flag.store(true, Ordering::SeqCst);
            let _ = set.send(());
        });
        if queued.is_err() || was_set.await.is_err() {
            late.store(true, Ordering::SeqCst);
        }
    }
    copied
}

#[cfg(test)]
mod tests {
    #[test]
    fn only_a_redirect_into_a_sign_in_sends_a_tico_sign_in_org_to_the_hub_sign_in() {
        let issuer = Url::parse("https://tico.dev/").unwrap();
        let at = |address: &str| Url::parse(address).unwrap();
        assert!(login_answer(302, Some(&at("https://tico.dev/oauth/authorize?client_id=x")), &issuer), "to the issuer");
        assert!(login_answer(307, Some(&at("https://team.example.com/auth/login")), &issuer), "to the hub's public address");
        assert!(!login_answer(404, None, &issuer), "OIDC off: no such route");
        assert!(!login_answer(401, None, &issuer));
        assert!(!login_answer(405, None, &issuer));
        assert!(!login_answer(200, None, &issuer));
        assert!(!login_answer(502, None, &issuer), "the provider could not be reached");
        assert!(!login_answer(302, Some(&at("https://team.cloudflareaccess.com/cdn-cgi/access/login/team.example.com")), &issuer));
        assert!(!login_answer(302, Some(&at("https://accounts.google.com/o/oauth2/auth")), &issuer), "not this sign-in's issuer");
        assert!(!login_answer(302, None, &issuer), "a redirect to nowhere");
        assert!(!login_answer(200, Some(&at("https://tico.dev/")), &issuer), "not a redirect");
    }


    use super::*;

    fn portal() -> Url {
        portal_from(None)
    }

    fn url(address: &str) -> Url {
        Url::parse(address).unwrap()
    }

    const DONE: &str = "https://app.tico.dev/desktop/orgs/done";

    #[test]
    fn the_portal_is_app_tico_dev_unless_the_environment_names_a_usable_one() {
        assert_eq!(portal().as_str(), "https://app.tico.dev/");
        assert_eq!(orgs_page(&portal()).as_str(), "https://app.tico.dev/desktop/orgs");
        assert_eq!(portal_from(Some("http://127.0.0.1:9100")).as_str(), "http://127.0.0.1:9100/");
        assert_eq!(portal_from(Some("https://portal.example.com")).as_str(), "https://portal.example.com/");
        for unusable in ["", "  ", "http://portal.example.com", "not an address", "https://portal.example.com/?x=1"] {
            assert_eq!(portal_from(Some(unusable)).as_str(), "https://app.tico.dev/", "{unusable}");
        }
    }

    #[test]
    fn only_the_portals_own_done_page_is_caught() {
        let portal = portal();
        for caught in [DONE, "https://app.tico.dev/desktop/orgs/done?issuer=https://accounts.tico.dev&org=https://acme.example.com",
                       "https://APP.tico.dev:443/desktop/orgs/done#x"] {
            assert!(is_done(&portal, &url(caught)), "{caught}");
        }
        for other in ["https://app.tico.dev/desktop/orgs", "https://app.tico.dev/desktop/orgs/done/x",
                      "https://app.tico.dev/desktop/orgs/done/", "https://app.tico.dev/desktop/orgs/donex",
                      "https://app.tico.dev/x/desktop/orgs/done", "http://app.tico.dev/desktop/orgs/done",
                      "https://app.tico.dev:8443/desktop/orgs/done", "https://evil.example/desktop/orgs/done",
                      "https://app.tico.dev.evil.example/desktop/orgs/done", "https://accounts.tico.dev/desktop/orgs/done",
                      "https://accounts.google.com/desktop/orgs/done", "about:blank"] {
            assert!(!is_done(&portal, &url(other)), "{other}");
        }
        // A loopback portal (tests) is caught on plain HTTP, on its own port only.
        let local = portal_from(Some("http://127.0.0.1:9100"));
        assert!(is_done(&local, &url("http://127.0.0.1:9100/desktop/orgs/done?issuer=x")));
        assert!(!is_done(&local, &url("http://127.0.0.1:9101/desktop/orgs/done")));
    }

    #[test]
    fn picks_keep_valid_servers_once_each_in_order() {
        let done = url(&format!("{DONE}?issuer=https%3A%2F%2Faccounts.tico.dev&org=https%3A%2F%2Facme.example.com\
            &org=http%3A%2F%2Fplain.example.com&org=https%3A%2F%2Ftidy.example.com%2Ftico&org=not+an+address\
            &org=https%3A%2F%2Facme.example.com%2F&org=https%3A%2F%2FACME.example.com%2Fother%2F&org=ftp%3A%2F%2Ffiles.example.com\
            &org=https%3A%2F%2Fana%3Asecret%40login.example.com&org=https%3A%2F%2Fq.example.com%2F%3Ftoken%3Dx\
            &org=http%3A%2F%2Flocalhost%3A8765&org="));
        let picks = picks(&portal(), &done).unwrap();
        assert_eq!(picks.issuer.as_str(), "https://accounts.tico.dev/");
        let orgs: Vec<&str> = picks.orgs.iter().map(Url::as_str).collect();
        assert_eq!(orgs, ["https://acme.example.com/", "https://tidy.example.com/tico/", "http://localhost:8765/"]);
        // Unencoded values read the same way.
        let plain = url(&format!("{DONE}?issuer=https://accounts.tico.dev&org=https://acme.example.com&org=https://tidy.example.com"));
        assert_eq!(super::picks(&portal(), &plain).unwrap().orgs.len(), 2);
        // No org at all: the picks are empty, and nothing is added.
        let none = url(&format!("{DONE}?issuer=https://accounts.tico.dev"));
        assert_eq!(super::picks(&portal(), &none).unwrap().orgs, Vec::<Url>::new());
    }

    #[test]
    fn without_one_usable_issuer_there_are_no_picks() {
        let portal = portal();
        let with = |query: &str| super::picks(&portal, &url(&format!("{DONE}?{query}&org=https://acme.example.com")));
        assert!(with("issuer=https://accounts.tico.dev").is_some());
        assert_eq!(with("issuer=https://accounts.tico.dev:8443").unwrap().issuer.as_str(), "https://accounts.tico.dev:8443/");
        assert!(with("issuer=http://localhost:9200").is_some(), "loopback HTTP, as tests run it");
        assert!(with("issuer=http://127.0.0.1:9200").is_some());
        for bad in ["x=1", "issuer=", "issuer=accounts.tico.dev", "issuer=http://accounts.tico.dev",
                    "issuer=https://accounts.tico.dev/login", "issuer=https://accounts.tico.dev/?next=1",
                    "issuer=https://ana:secret@accounts.tico.dev", "issuer=ftp://accounts.tico.dev",
                    "issuer=https://accounts.google.com", "issuer=https://google.com", "issuer=https://app.tico.dev",
                    "issuer=https://APP.tico.dev", "issuer=https://accounts.tico.dev&issuer=https://other.example.com",
                    "issuer=https://accounts.tico.dev&issuer=https://accounts.tico.dev"] {
            assert!(with(bad).is_none(), "{bad}");
        }
        // On loopback the portal and the issuer may share a host (cookies ignore ports).
        let local = portal_from(Some("http://127.0.0.1:9100"));
        let picks = super::picks(&local, &url("http://127.0.0.1:9100/desktop/orgs/done?issuer=http://127.0.0.1:9200&org=http://127.0.0.1:8765"));
        assert_eq!(picks.unwrap().orgs[0].as_str(), "http://127.0.0.1:8765/");
    }

    #[test]
    fn only_the_issuers_own_host_has_its_cookies_copied() {
        let host = "accounts.tico.dev";
        assert!(issuer_cookie("accounts.tico.dev", host));
        assert!(issuer_cookie(".accounts.tico.dev", host));
        assert!(issuer_cookie("Accounts.Tico.dev", host));
        for other in ["tico.dev", ".tico.dev", "app.tico.dev", ".app.tico.dev", "x.accounts.tico.dev", ".x.accounts.tico.dev",
                      "accounts.tico.dev.evil.example", "..accounts.tico.dev", "accounts.google.com", ".google.com", ""] {
            assert!(!issuer_cookie(other, host), "{other}");
        }
        assert!(!issuer_cookie("", ""), "no host, no cookies");
    }

    #[test]
    fn no_capability_reaches_the_sign_in_window() {
        let config: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        assert_eq!(config["app"]["security"]["capabilities"], serde_json::json!(["server", "rail"]));
        for text in [include_str!("../capabilities/server.json"), include_str!("../capabilities/rail.json"),
                     include_str!("../capabilities/main.json")] {
            let capability: serde_json::Value = serde_json::from_str(text).unwrap();
            for key in ["windows", "webviews"] {
                let labels = capability[key].as_array().cloned().unwrap_or_default();
                assert!(labels.iter().all(|label| label.as_str().is_some_and(|label| !is_signin(label) && !label.contains('*'))),
                        "{key}: {labels:?}");
            }
        }
        // Org pages' runtime grants name `org-<id>` webviews only, and switching closes only those.
        assert!(!SIGNIN.starts_with("org-"));
        assert!(is_signin("tico-signin-0f3a") && !is_signin("server") && !is_signin("org-o_7f3a0001") && !is_signin("tico-signin"));
    }

    fn org(id: &str, store: Option<Uuid>) -> Org {
        Org { id: id.into(), url: "https://acme.example.com/".into(), store: store.map(|store| store.to_string()),
              name: String::new(), email: String::new(), added_at: String::new(), sign_in: false, last_url: None }
    }

    #[test]
    fn stores_left_by_a_run_that_quit_are_noted_and_forgotten() {
        let dir = std::env::temp_dir().join(format!("tico-signin-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let path = dir.join(PENDING_FILE);
        let (a, b, c) = (Uuid::new_v4(), Uuid::new_v4(), Uuid::new_v4());
        assert!(read_pending(&path).is_empty(), "no file, nothing pending");
        update_pending(&path, &[a, b], &[]).unwrap();
        update_pending(&path, &[b, c], &[]).unwrap();
        assert_eq!(read_pending(&path), [a, b, c], "each store once, in order");
        update_pending(&path, &[], &[b]).unwrap();
        assert_eq!(read_pending(&path), [a, c]);
        std::fs::write(&path, format!("{a}\nnot a store\n\n  {c}  \n")).unwrap();
        assert_eq!(read_pending(&path), [a, c], "other lines are ignored");
        update_pending(&path, &[], &[a, c]).unwrap();
        assert!(!path.exists(), "the file goes with the last store");
        update_pending(&path, &[], &[a]).unwrap();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_store_an_org_was_saved_on_is_never_removed_at_launch() {
        let (signin, saved, prepared) = (Uuid::new_v4(), Uuid::new_v4(), Uuid::new_v4());
        let orgs = [org("o_aaaaaaaa", Some(saved)), org("o_bbbbbbbb", None)];
        assert_eq!(stale_stores(&[signin, saved, prepared], &orgs), [signin, prepared]);
        assert!(stale_stores(&[], &orgs).is_empty());
    }

    #[test]
    fn prepared_stores_no_org_was_added_on_are_the_ones_discarded() {
        let (a, b, c) = (Uuid::new_v4(), Uuid::new_v4(), Uuid::new_v4());
        let (kept, unused) = split_stores(&[a, b, c], &[org("o_aaaaaaaa", Some(c)), org("o_bbbbbbbb", Some(a))]);
        assert_eq!((kept, unused), (vec![a, c], vec![b]));
        assert_eq!(split_stores(&[a], &[]), (vec![], vec![a]));
    }

    #[test]
    fn a_removal_during_a_renewal_waits_for_it_and_a_late_write_finds_the_org_gone() {
        let store = Uuid::new_v4();
        let mut orgs = vec![org("o_aaaaaaaa", Some(store)), org("o_bbbbbbbb", None)];
        assert!(owns_store(&orgs, "o_aaaaaaaa", Some(store)));
        assert!(owns_store(&orgs, "o_bbbbbbbb", None), "the org moved over from server.txt, on the default store");
        assert!(!owns_store(&orgs, "o_aaaaaaaa", None), "never the default store for an org with its own");
        assert!(!owns_store(&orgs, "o_aaaaaaaa", Some(Uuid::new_v4())));
        // Removed while the cookies were being read: the write that comes after finds nothing to write to.
        orgs.retain(|org| org.id != "o_aaaaaaaa");
        assert!(!owns_store(&orgs, "o_aaaaaaaa", Some(store)));
        // And a removal waits for a renewal under way before it closes the page and lets the store go.
        let runtime = tokio::runtime::Builder::new_current_thread().enable_time().build().unwrap();
        runtime.block_on(async {
            let renewing = RENEWING.lock().await;
            let removal = tokio::spawn(async { let _removing = RENEWING.lock().await; });
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
            assert!(!removal.is_finished(), "the removal waits");
            drop(renewing);
            removal.await.unwrap();
        });
    }

    #[test]
    fn a_renewed_session_goes_only_into_each_picked_orgs_own_store() {
        let (acme_store, tidy_store) = (Uuid::new_v4(), Uuid::new_v4());
        let mut tidy = org("o_bbbbbbbb", Some(tidy_store));
        tidy.url = "https://tidy.example.com/".into();
        let mut legacy = org("o_cccccccc", None);
        legacy.url = "https://old.example.com/".into();
        let orgs = [org("o_aaaaaaaa", Some(acme_store)), tidy, legacy];
        let picked = [
            ("o_aaaaaaaa".to_string(), url("https://acme.example.com/"), true),
            ("o_cccccccc".to_string(), url("https://old.example.com/"), false),
            // Removed since it was picked, or now at another server: nothing goes to it.
            ("o_dddddddd".to_string(), url("https://gone.example.com/"), true),
            ("o_bbbbbbbb".to_string(), url("https://acme.example.com/"), true),
        ];
        assert_eq!(renew_targets(&orgs, &picked), [
            ("o_aaaaaaaa".to_string(), Some(acme_store), true),
            ("o_cccccccc".to_string(), None, false),
        ], "TIDY was not picked, so its store gets nothing");
        assert!(renew_targets(&orgs, &[]).is_empty());
    }
}
