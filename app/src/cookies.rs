//! A page's cookies for one address, read without blocking the main thread.
//!
//! On macOS WebKit hands cookies to a callback. wry's `cookies_for_url` waits for that callback on the main thread by
//! running the event loop itself, and when a redraw arrives during that wait tao takes a lock its event handler
//! already holds: the window freezes for good. A window with child webviews redraws often enough to hit it at
//! startup. So on macOS the cookie store is asked from the main thread and the answer is awaited here instead.
use tauri::{Runtime, Webview};
use url::Url;

/// One stored cookie, as much of it as choosing what to send needs.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Cookie {
    pub name: String,
    pub value: String,
    pub domain: String,
    pub secure: bool,
}

/// The `(name, value)` pairs the page would send to `url`: wry's own rule, so the badge sends what it sent before.
/// The cookie's domain (without a leading dot) is the address's domain, and a secure cookie goes only to HTTPS or
/// to a loopback address.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub(crate) fn matching(cookies: Vec<Cookie>, url: &Url) -> Vec<(String, String)> {
    let loopback = url.host_str().is_some_and(|host| host == "localhost"
        || host.parse::<std::net::Ipv4Addr>().is_ok_and(|ip| ip.is_loopback()));
    cookies.into_iter()
        .filter(|cookie| Some(cookie.domain.trim_start_matches('.')) == url.domain())
        .filter(|cookie| !cookie.secure || url.scheme() == "https" || loopback)
        .map(|cookie| (cookie.name, cookie.value))
        .collect()
}

/// The cookies `webview` would send to `url`, or None when the store did not answer within five seconds.
#[cfg(target_os = "macos")]
pub(crate) async fn for_url<R: Runtime>(webview: &Webview<R>, url: &Url) -> Option<Vec<(String, String)>> {
    use block2::RcBlock;
    use objc2::rc::Retained;
    use objc2::runtime::AnyObject;
    use objc2_foundation::{NSArray, NSHTTPCookie};
    use objc2_web_kit::WKWebView;
    use std::ptr::NonNull;

    let (sender, mut answer) = tokio::sync::mpsc::unbounded_channel::<Vec<Cookie>>();
    // Runs on the main thread and returns at once; WebKit calls the block later, also on the main thread.
    webview.with_webview(move |platform| unsafe {
        // tauri-runtime-wry 2.11 hands over the page, its controller and its window each retained once more and never
        // releases them (fixed upstream in 2.12.0): taking ownership here releases them when this closure ends, so a
        // poll leaks nothing. `runtime_still_over_retains` fails once the runtime is upgraded past 2.11.
        let _controller = Retained::<AnyObject>::from_raw(platform.controller().cast());
        let _window = Retained::<AnyObject>::from_raw(platform.ns_window().cast());
        // The page's live WKWebView. The store is the page's own: an org with its own data store answers with that
        // store's cookies.
        let Some(view) = Retained::<WKWebView>::from_raw(platform.inner().cast()) else { return };
        let store = view.configuration().websiteDataStore().httpCookieStore();
        let reply = RcBlock::new(move |cookies: NonNull<NSArray<NSHTTPCookie>>| {
            let cookies = cookies.as_ref().to_vec().iter().map(|cookie| Cookie {
                name: cookie.name().to_string(),
                value: cookie.value().to_string(),
                domain: cookie.domain().to_string(),
                secure: cookie.isSecure(),
            }).collect();
            let _ = sender.send(cookies);
        });
        store.getAllCookies(&reply);
    }).ok()?;
    let cookies = tokio::time::timeout(std::time::Duration::from_secs(5), answer.recv()).await.ok()??;
    Some(matching(cookies, url))
}

/// Elsewhere wry's own call, as before.
#[cfg(not(target_os = "macos"))]
pub(crate) async fn for_url<R: Runtime>(webview: &Webview<R>, url: &Url) -> Option<Vec<(String, String)>> {
    let cookies = webview.cookies_for_url(url.clone()).ok()?;
    Some(cookies.iter().map(|cookie| (cookie.name().to_string(), cookie.value().to_string())).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cookie(name: &str, domain: &str, secure: bool) -> Cookie {
        Cookie { name: name.into(), value: format!("{name}-value"), domain: domain.into(), secure }
    }

    #[test]
    fn only_the_hubs_own_cookies_go_with_a_request() {
        let cookies = vec![
            cookie("session", "hub.example.com", true),
            cookie("dotted", ".hub.example.com", false),
            cookie("parent", "example.com", false),
            cookie("other", "evil.example", false),
        ];
        let sent = matching(cookies.clone(), &Url::parse("https://hub.example.com/api/status").unwrap());
        assert_eq!(sent, vec![("session".into(), "session-value".into()), ("dotted".into(), "dotted-value".into())]);
        // A secure cookie never goes over plain HTTP to a remote host.
        let plain = matching(cookies, &Url::parse("http://hub.example.com/api/status").unwrap());
        assert_eq!(plain, vec![("dotted".into(), "dotted-value".into())]);
    }

    /// `for_url` releases the extra retains tauri-runtime-wry 2.11 leaves on `with_webview`'s objects. 2.12.0 stopped
    /// adding them; on that runtime the releases would free objects still in use, so they must go with the upgrade.
    #[test]
    fn runtime_still_over_retains() {
        let lock = include_str!("../Cargo.lock");
        let version = lock.split("[[package]]").find(|entry| entry.contains("\nname = \"tauri-runtime-wry\"\n"))
            .and_then(|entry| entry.lines().find_map(|line| line.strip_prefix("version = \"")))
            .map(|version| version.trim_end_matches('"')).unwrap();
        assert!(version.starts_with("2.11."), "tauri-runtime-wry {version}: remove the releases in cookies::for_url");
    }
}
