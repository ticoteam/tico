//! Tico Recorder (recorder/, bundle id team.tico.recorder.native) and this app.
//!
//! The page's Record opens the Recorder on the page's own org (`open_recorder`), when it is installed (`installed`).
//!
//! The Recorder signs in to an org with Tico's agent sign-in (backend/mcp_oauth.py). The approval page needs the
//! person's Tico session, which this app already has for each of its orgs, so the Recorder asks this app to show it:
//! `tico://open?org=<org address>&path=<page, relative to that address>`. Only an org this app has, and only its
//! approval page (`oauth/authorize`), opens; nothing is approved without the person choosing Allow there. The app
//! shows the page in a small window of its own on that org's data store, above the main window, and tells the
//! Recorder it did (`/opened` on the Recorder's loopback, so the Recorder does not open the browser). The main window
//! is left as it is. Tico's answer, a redirect to the Recorder's loopback
//! (`http://127.0.0.1:<port>/callback?code=…&state=…`), is handed to the Recorder from here rather than loaded in the
//! window or a browser tab (`deliver`), and the window closes.
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::Manager;
use url::Url;

use crate::orgs::{Org, Orgs};
use crate::windows::{open_approval_window, show_window, Home};
use crate::{App, AppState};

pub(crate) const RECORDER_ID: &str = "team.tico.recorder.native";
/// The approval page, relative to an org's address.
const APPROVAL_PATH: &str = "oauth/authorize";
/// How long an approval opened here waits for its answer (the Recorder waits five minutes).
const ANSWER_WAIT: Duration = Duration::from_secs(600);

/// Whether Tico Recorder is installed on this Mac.
pub(crate) fn installed() -> bool {
    #[cfg(target_os = "macos")]
    {
        use objc2::rc::Retained;
        use objc2::runtime::AnyObject;
        use objc2::{class, msg_send};
        let id = objc2_foundation::NSString::from_str(RECORDER_ID);
        // SAFETY: NSWorkspace's shared instance and its bundle-identifier lookup, which may be called from any thread.
        unsafe {
            let workspace: Option<Retained<AnyObject>> = msg_send![class!(NSWorkspace), sharedWorkspace];
            let Some(workspace) = workspace else { return false };
            let app: Option<Retained<AnyObject>> = msg_send![&*workspace, URLForApplicationWithBundleIdentifier: &*id];
            app.is_some()
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        false
    }
}

/// The link that opens the Recorder on an org: `tico-recorder://open?server=<org address>`. It starts nothing.
pub(crate) fn recorder_link(hub: &Url) -> String {
    let mut link = Url::parse("tico-recorder://open").expect("a fixed link");
    link.query_pairs_mut().append_pair("server", hub.as_str());
    link.to_string()
}

/// Opens the Recorder on `hub`, in Tico Recorder itself rather than whatever else took its scheme.
pub(crate) fn open_recorder(hub: &Url) {
    #[cfg(target_os = "macos")]
    {
        let link = recorder_link(hub);
        if let Err(e) = std::process::Command::new("/usr/bin/open").args(["-b", RECORDER_ID, &link]).spawn() {
            log::warn!("could not open Tico Recorder: {e}");
        }
    }
    #[cfg(not(target_os = "macos"))]
    let _ = hub;
}

/// The org and approval page a `tico://open` link names, when it is an org this app has and the page is that org's
/// approval page. Anything else (another org, another page, another site) is refused.
pub(crate) fn approval(orgs: &Orgs, link: &Url) -> Option<(Org, Url)> {
    if link.scheme() != "tico" || link.host_str() != Some("open") {
        return None;
    }
    let param = |name: &str| link.query_pairs().find(|(key, _)| key == name).map(|(_, value)| value.into_owned());
    let named = Url::parse(&param("org")?).ok()?;
    let org = orgs.find_hub(&named)?.clone();
    let hub = org.hub()?;
    let path = param("path")?;
    if path.starts_with(['/', '\\']) || path.contains('#') {
        return None;
    }
    let target = hub.join(&path).ok()?;
    let page = hub.join(APPROVAL_PATH).ok()?;
    let fits = target.origin() == hub.origin() && target.path() == page.path() && target.username().is_empty()
        && target.password().is_none() && target.fragment().is_none();
    fits.then_some((org, target))
}

/// Where the approval page sends its answer, when that is a loopback address on this Mac, and the request's state.
pub(crate) fn loopback(target: &Url) -> Option<(Url, String)> {
    let param = |name: &str| target.query_pairs().find(|(key, _)| key == name).map(|(_, value)| value.into_owned());
    let redirect = Url::parse(&param("redirect_uri")?).ok()?;
    let state = param("state").filter(|state| !state.is_empty())?;
    let fits = redirect.scheme() == "http" && redirect.host_str() == Some("127.0.0.1") && redirect.port().is_some()
        && redirect.username().is_empty() && redirect.password().is_none() && redirect.query().is_none()
        && redirect.fragment().is_none();
    fits.then_some((redirect, state))
}

/// An approval page this app opened, waiting for Tico's answer.
pub(crate) struct Pending {
    /// The label of the window showing it.
    pub window: String,
    pub redirect: Url,
    pub at: Instant,
}

static PENDING: Mutex<Option<Pending>> = Mutex::new(None);

/// Whether a navigation in window `window` is Tico's answer to the approval waiting: the loopback address it was
/// told to answer to, from the window showing the approval page, in time.
pub(crate) fn answers(pending: Option<&Pending>, window: &str, url: &Url) -> bool {
    pending.is_some_and(|pending| {
        pending.window == window && pending.at.elapsed() < ANSWER_WAIT && url.scheme() == "http"
            && url.host_str() == Some("127.0.0.1") && url.port() == pending.redirect.port()
            && url.path() == pending.redirect.path() && url.username().is_empty() && url.fragment().is_none()
    })
}

/// A `tico://open` link: shows the org's approval page in a window of its own on that org's data store
/// (`open_approval_window`), above the main window; the org's page and the org open in the main window are left as
/// they are. A newer link replaces a window still waiting. A link this app refuses only brings the window forward.
pub(crate) fn open_link(app: &App, link: &Url) {
    let found = approval(&app.state::<AppState>().orgs.lock().unwrap(), link);
    let Some((home, target)) = found.and_then(|(org, target)| Some((Home::of(&org)?, target))) else {
        log::warn!("a Tico Recorder sign-in link named no org or page this app opens");
        show_window(app);
        return;
    };
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        static NEXT: AtomicU32 = AtomicU32::new(1);
        let label = format!("approval-{}", NEXT.fetch_add(1, Ordering::Relaxed));
        let answer = loopback(&target);
        let replaced = std::mem::replace(
            &mut *PENDING.lock().unwrap(),
            answer.as_ref().map(|(redirect, _)| Pending { window: label.clone(), redirect: redirect.clone(), at: Instant::now() }),
        );
        if let Some(window) = replaced.and_then(|old| app.get_webview_window(&old.window)) {
            let _ = window.close();
        }
        show_window(&app);
        if !open_approval_window(&app, &home, &label, target) {
            return;
        }
        if let Some((redirect, state)) = answer {
            let mut opened = redirect;
            opened.set_path("/opened");
            opened.query_pairs_mut().append_pair("state", &state);
            if let Err(e) = loopback_get(&opened).await {
                log::warn!("could not tell Tico Recorder its approval page is open: {e}");
            }
        }
    });
}

/// Tico's answer to an approval this app opened (see `answers`), in window `window`: handed to the Recorder from
/// here, and the window closes. Answers whether it was one, so the window does not load it.
pub(crate) fn deliver(app: &App, window: &str, url: &Url) -> bool {
    {
        let mut held = PENDING.lock().unwrap();
        if !answers(held.as_ref(), window, url) {
            return false;
        }
        held.take();
    }
    let (app, url, window) = (app.clone(), url.clone(), window.to_string());
    tauri::async_runtime::spawn(async move {
        if let Err(e) = loopback_get(&url).await {
            log::warn!("could not hand Tico's answer to Tico Recorder: {e}");
        }
        if let Some(window) = app.get_webview_window(&window) {
            let _ = window.close();
        }
    });
    true
}

/// A GET of the Recorder's loopback, without the system proxy and without following anything.
async fn loopback_get(url: &Url) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(5))
        .build()
        .map_err(|e| e.to_string())?;
    client.get(url.as_str()).send().await.map(|_| ()).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::orgs::Document;

    fn orgs() -> Orgs {
        let org = |id: &str, url: &str| Org {
            id: id.into(),
            url: url.into(),
            store: None,
            name: String::new(),
            email: String::new(),
            added_at: String::new(),
            sign_in: false,
            last_url: None,
        };
        Orgs::new(
            Document {
                version: 1,
                active: Some("o_aaaaaaaa".into()),
                orgs: vec![org("o_aaaaaaaa", "https://acme.example.com/"), org("o_bbbbbbbb", "https://team.example.com/tico/")],
            },
            None,
        )
    }
    const QUERY: &str = "response_type=code&client_id=c1&redirect_uri=http%3A%2F%2F127.0.0.1%3A5123%2Fcallback&state=s1";

    fn link(org: &str, path: &str) -> Url {
        let mut link = Url::parse("tico://open").unwrap();
        link.query_pairs_mut().append_pair("org", org).append_pair("path", path);
        link
    }

    #[test]
    fn only_an_orgs_own_approval_page_opens_from_a_link() {
        let orgs = orgs();
        let (org, target) = approval(&orgs, &link("https://acme.example.com", &format!("oauth/authorize?{QUERY}"))).unwrap();
        assert_eq!(org.id, "o_aaaaaaaa");
        assert_eq!(target.as_str(), format!("https://acme.example.com/oauth/authorize?{QUERY}"));
        // An org on a path keeps it.
        let (org, target) = approval(&orgs, &link("https://team.example.com/tico", "oauth/authorize?state=x")).unwrap();
        assert_eq!((org.id.as_str(), target.as_str()), ("o_bbbbbbbb", "https://team.example.com/tico/oauth/authorize?state=x"));
        for (org, path) in [
            // An org this app does not have.
            ("https://evil.example.com", "oauth/authorize?state=x"),
            ("https://acme.example.com.evil.test", "oauth/authorize?state=x"),
            ("http://acme.example.com", "oauth/authorize?state=x"),
            ("not an address", "oauth/authorize"),
            // Any other page, or another site, of an org this app has.
            ("https://acme.example.com", "api/v2/logout"),
            ("https://acme.example.com", ""),
            ("https://acme.example.com", "oauth/authorize/../../api/v2/logout"),
            ("https://acme.example.com", "/oauth/authorize"),
            ("https://acme.example.com", "//evil.example.com/oauth/authorize"),
            ("https://acme.example.com", "\\\\evil.example.com/oauth/authorize"),
            ("https://acme.example.com", "https://evil.example.com/oauth/authorize"),
            ("https://acme.example.com", "oauth/authorize#x"),
            ("https://acme.example.com", "OAUTH/authorize"),
            ("https://team.example.com/tico", "../oauth/authorize"),
        ] {
            assert!(approval(&orgs, &link(org, path)).is_none(), "{org} {path}");
        }
        assert!(approval(&orgs, &Url::parse("tico://meetings/abc").unwrap()).is_none());
        assert!(approval(&orgs, &Url::parse(&link("https://acme.example.com", "oauth/authorize").as_str().replace("tico://", "other://")).unwrap()).is_none());
        let mut no_path = Url::parse("tico://open").unwrap();
        no_path.query_pairs_mut().append_pair("org", "https://acme.example.com");
        assert!(approval(&orgs, &no_path).is_none());
    }

    #[test]
    fn only_a_loopback_answer_is_handed_over() {
        let target = Url::parse(&format!("https://acme.example.com/oauth/authorize?{QUERY}")).unwrap();
        let (redirect, state) = loopback(&target).unwrap();
        assert_eq!((redirect.as_str(), state.as_str()), ("http://127.0.0.1:5123/callback", "s1"));
        for redirect in ["https://agent.example.com/callback", "http://localhost:5123/callback", "http://127.0.0.1/callback",
                         "http://user@127.0.0.1:5123/callback", "http://127.0.0.1:5123/callback?x=1", "nonsense"] {
            let mut target = Url::parse("https://acme.example.com/oauth/authorize").unwrap();
            target.query_pairs_mut().append_pair("redirect_uri", redirect).append_pair("state", "s1");
            assert!(loopback(&target).is_none(), "{redirect}");
        }
        let mut stateless = Url::parse("https://acme.example.com/oauth/authorize").unwrap();
        stateless.query_pairs_mut().append_pair("redirect_uri", "http://127.0.0.1:5123/callback");
        assert!(loopback(&stateless).is_none());

        let pending = Pending { window: "approval-1".into(), redirect, at: Instant::now() };
        let answer = Url::parse("http://127.0.0.1:5123/callback?code=c&state=s1").unwrap();
        assert!(answers(Some(&pending), "approval-1", &answer));
        assert!(!answers(Some(&pending), "approval-2", &answer), "an older approval window");
        assert!(!answers(Some(&pending), "o_aaaaaaaa", &answer), "the org's own page");
        assert!(!answers(Some(&pending), "main", &answer));
        assert!(!answers(None, "approval-1", &answer), "no approval waiting");
        for other in ["http://127.0.0.1:5124/callback?code=c", "http://127.0.0.1:5123/other?code=c", "https://127.0.0.1:5123/callback",
                      "http://localhost:5123/callback", "http://evil.example.com:5123/callback"] {
            assert!(!answers(Some(&pending), "approval-1", &Url::parse(other).unwrap()), "{other}");
        }
        // A machine up for less than the wait has no instant that long ago.
        if let Some(at) = Instant::now().checked_sub(ANSWER_WAIT + Duration::from_secs(1)) {
            let stale = Pending { at, ..pending };
            assert!(!answers(Some(&stale), "approval-1", &answer), "too late");
        }
    }

    #[test]
    fn record_opens_the_recorder_on_the_pages_own_org() {
        let hub = Url::parse("https://acme.example.com/").unwrap();
        let link = Url::parse(&recorder_link(&hub)).unwrap();
        assert_eq!((link.scheme(), link.host_str()), ("tico-recorder", Some("open")));
        let query: Vec<(String, String)> = link.query_pairs().into_owned().collect();
        assert_eq!(query, [("server".to_string(), "https://acme.example.com/".to_string())]);
    }
}
