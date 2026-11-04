//! Sign in to Tico: one sign-in, then the person picks orgs from their account, instead of typing each address.
//!
//! The sign-in window shows Tico Cloud's portal page (`{PORTAL}/desktop/orgs`) on a data store of its own, made fresh
//! for each sign-in, so it starts signed out and shares nothing with any org. It gets no IPC: no capability names it.
//! When the person submits, the portal navigates to `{PORTAL}/desktop/orgs/done?issuer=<origin>&org=<server>...`. That
//! navigation is cancelled and read here: each picked server becomes an org on a store of its own, and on macOS the
//! sign-in's cookies for the issuer's host (only that host: never Google's or the portal's) are copied into each new
//! store before its page loads. The org's server then redirects to the issuer, which finds that session and sends the
//! page back signed in. The sign-in window and its store go once it is done or closed.

use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, Ordering};
#[cfg(target_os = "macos")]
use std::sync::Mutex;
use std::sync::OnceLock;
#[cfg(target_os = "macos")]
use std::time::Duration;

use tauri::webview::{NewWindowFeatures, NewWindowResponse};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;
use url::Url;
use uuid::Uuid;

use crate::config;
use crate::windows::{show_window, switch_to};
use crate::{orgs, App, AppState, MIN_HEIGHT, MIN_WIDTH, USER_AGENT};

/// The sign-in window's label. No capability names it, so its pages get no IPC.
pub(crate) const SIGNIN: &str = "tico-signin";
const DEFAULT_PORTAL: &str = "https://app.tico.dev";
/// How long the cookie copy may take before the orgs are added anyway (their pages then ask to sign in again).
#[cfg(target_os = "macos")]
const COPY_TIMEOUT: Duration = Duration::from_secs(10);

/// Set once a sign-in's picks are being handled, so a second submit is ignored. Cleared when a sign-in window opens.
static HANDLED: AtomicBool = AtomicBool::new(false);
/// The open sign-in window's data store, removed when the window goes.
#[cfg(target_os = "macos")]
static STORE: Mutex<Option<Uuid>> = Mutex::new(None);

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

fn open_window(app: &App) -> tauri::Result<()> {
    if let Some(window) = app.get_webview_window(SIGNIN) {
        let _ = window.unminimize();
        window.show()?;
        window.set_focus()?;
        return Ok(());
    }
    let portal = portal().clone();
    HANDLED.store(false, Ordering::SeqCst);
    let handler = app.clone();
    #[cfg_attr(not(target_os = "macos"), allow(unused_mut))]
    let mut builder = WebviewWindowBuilder::new(app, SIGNIN, WebviewUrl::External(orgs_page(&portal)))
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
            if !HANDLED.swap(true, Ordering::SeqCst) {
                let (app, picks) = (handler.clone(), picks(&portal, url));
                // Off the navigation callback, which runs on the main thread: reading and copying cookies is waited on.
                tauri::async_runtime::spawn(async move { finish(&app, picks).await });
            }
            false
        });
    #[cfg(target_os = "macos")]
    let store = Uuid::new_v4();
    #[cfg(target_os = "macos")]
    {
        builder = builder.data_store_identifier(*store.as_bytes());
    }
    builder.build()?;
    #[cfg(target_os = "macos")]
    {
        *STORE.lock().unwrap() = Some(store);
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

/// The sign-in window closed (done, cancelled or closed by the person): its data store goes. A page that just closed
/// holds its store for a moment, so removal is tried a few times.
pub(crate) fn closed(app: &App, label: &str) {
    if label != SIGNIN {
        return;
    }
    #[cfg(target_os = "macos")]
    {
        let Some(store) = STORE.lock().unwrap().take() else { return };
        let app = app.clone();
        tauri::async_runtime::spawn(async move { remove_store(&app, store).await });
    }
    #[cfg(not(target_os = "macos"))]
    let _ = app;
}

#[cfg(target_os = "macos")]
async fn remove_store(app: &App, store: Uuid) {
    for _ in 0..8 {
        if app.remove_data_store(*store.as_bytes()).await.is_ok() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    log::warn!("could not delete a data store that is no longer used");
}

/// Closes the sign-in window, if it is open.
pub(crate) fn close(app: &App) {
    if let Some(window) = app.get_webview_window(SIGNIN) {
        let _ = window.close();
    }
}

/// Adds the picked servers as orgs, signed in through the issuer's session, and opens the first new one (or, when
/// every pick is already an org, the first of those).
async fn finish(app: &App, picks: Option<Picks>) {
    let Some(picks) = picks.filter(|picks| !picks.orgs.is_empty()) else {
        log::warn!("Tico sign-in finished with no usable organization");
        return close(app);
    };
    // Which picks are new. Each new one gets its store id now, so the session is in the store before the org exists.
    let (fresh, existing): (Vec<(Url, Uuid)>, Option<String>) = {
        let state = app.state::<AppState>();
        let list = state.orgs.lock().unwrap();
        let existing = picks.orgs.iter().find_map(|hub| list.find_hub(hub).map(|org| org.id.clone()));
        let fresh = picks.orgs.iter().filter(|hub| list.find_hub(hub).is_none()).map(|hub| (hub.clone(), Uuid::new_v4())).collect();
        (fresh, existing)
    };
    #[cfg(target_os = "macos")]
    if !fresh.is_empty() {
        let host = picks.issuer.host_str().unwrap_or_default().to_string();
        let stores: Vec<Uuid> = fresh.iter().map(|(_, store)| *store).collect();
        match app.get_webview_window(SIGNIN) {
            Some(window) => match copy_cookies(&window, host, stores).await {
                Some(count) => log::info!("Tico sign-in: {count} session cookie(s) copied to {} new organization(s)", fresh.len()),
                None => log::warn!("Tico sign-in: the session could not be copied; the new organizations ask to sign in"),
            },
            None => log::warn!("Tico sign-in: the window closed before its session was copied"),
        }
    }
    let open = if fresh.is_empty() {
        existing
    } else {
        let added = app.state::<AppState>().orgs.lock().unwrap().add_all(&fresh);
        match added {
            Ok(added) => added.first().map(|org| org.id.clone()),
            Err(e) => {
                #[cfg(target_os = "macos")]
                for (_, store) in &fresh {
                    release_store(app, *store);
                    remove_store(app, *store).await;
                }
                app.dialog().message(e).title("Add organization").show(|_| {});
                return close(app);
            }
        }
    };
    if let Some(id) = open {
        if let Err(e) = switch_to(app, &id) {
            log::warn!("could not open the organization: {e}");
        }
        show_window(app);
        if let Some(setup) = app.get_webview_window("server") {
            let _ = setup.close();
        }
    }
    close(app);
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

/// Copies the sign-in window's cookies for `host` into each of `stores`, as the same NSHTTPCookie objects (they are
/// HttpOnly, so they are not rebuilt). The count of cookies copied, or None when WebKit did not finish in time.
/// Everything WebKit runs on the main thread, which hands each call to a callback and returns at once; only the
/// answer is awaited here (see cookies.rs for why the main thread never waits).
#[cfg(target_os = "macos")]
async fn copy_cookies(window: &tauri::WebviewWindow, host: String, stores: Vec<Uuid>) -> Option<usize> {
    use block2::RcBlock;
    use objc2::MainThreadMarker;
    use objc2_foundation::{NSArray, NSHTTPCookie, NSUUID};
    use objc2_web_kit::{WKWebView, WKWebsiteDataStore};
    use std::cell::Cell;
    use std::ptr::NonNull;
    use std::rc::Rc;

    let (sender, mut answer) = tokio::sync::mpsc::unbounded_channel::<usize>();
    window.with_webview(move |platform| unsafe {
        let Some(mtm) = MainThreadMarker::new() else { return };
        // `inner` is the sign-in page's live WKWebView while this closure runs; its store is the sign-in's own.
        let view = &*platform.inner().cast::<WKWebView>();
        let source = view.configuration().websiteDataStore().httpCookieStore();
        let reply = RcBlock::new(move |cookies: NonNull<NSArray<NSHTTPCookie>>| {
            let picked: Vec<_> = cookies.as_ref().to_vec().into_iter()
                .filter(|cookie| issuer_cookie(&cookie.domain().to_string(), &host))
                .collect();
            let left = Rc::new(Cell::new(picked.len() * stores.len()));
            if left.get() == 0 {
                let _ = sender.send(0);
                return;
            }
            for store in &stores {
                // The org's store, by the id its page will open with (macOS 14).
                let target = WKWebsiteDataStore::dataStoreForIdentifier(&NSUUID::from_bytes(*store.as_bytes()), mtm);
                KEPT.with(|kept| kept.borrow_mut().insert(*store, target.clone()));
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
    tokio::time::timeout(COPY_TIMEOUT, answer.recv()).await.ok()?
}

#[cfg(test)]
mod tests {
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
                assert!(labels.iter().all(|label| label.as_str().is_some_and(|label| label != SIGNIN && !label.contains('*'))),
                        "{key}: {labels:?}");
            }
        }
        // Org pages' runtime grants name `org-<id>` webviews only, and switching closes only those.
        assert!(!SIGNIN.starts_with("org-"));
    }
}
