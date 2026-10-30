//! `POST {TICO_BROWSER_URL}/fill`: type a granted login's username, password or current authenticator code into
//! the focused field of the bot's page. The login is read from the hub with the runner's token for the run whose URL
//! made the call, the page's top-level host (as Chrome reports it, not as page script says) must be one of the login's
//! sites, and so must the frame holding the field. A plain-http page is refused (`insecure_page`) unless it is on this
//! machine or the login names that site as `http://host`. The reply is only `ok` or an error code, never a value.

use std::time::Duration;

use serde_json::{json, Value};

use super::Bot;
use crate::util::text_of;

fn no(error: &str) -> Value {
    json!({"ok": false, "error": error})
}

const HTTP: &str = "http://";

/// `host` is one of `sites` or below one (`login.acme.example` matches `acme.example`); a site kept as `http://host`
/// (backend/credentials.py clean_sites) matches by its host.
pub fn site_matches(host: &str, sites: &[String]) -> bool {
    let host = host.trim_end_matches('.').to_ascii_lowercase();
    let under = |s: &str| {
        let s = s.trim();
        let s = s.get(..HTTP.len()).filter(|h| h.eq_ignore_ascii_case(HTTP)).map_or(s, |_| &s[HTTP.len()..]);
        let s = s.trim_start_matches("*.").trim_end_matches('.').to_ascii_lowercase();
        !s.is_empty() && (host == s || host.strip_suffix(&s).is_some_and(|head| head.ends_with('.')))
    };
    !host.is_empty() && sites.iter().any(|s| under(s))
}

/// The scheme and host of an http(s) URL; None for any other scheme.
fn web_origin(url: &str) -> Option<(String, String)> {
    let u = url::Url::parse(url).ok()?;
    let host = u.host_str()?.trim_start_matches('[').trim_end_matches(']').to_ascii_lowercase();
    matches!(u.scheme(), "http" | "https").then(|| (u.scheme().to_string(), host))
}

/// The host of an http(s) URL; None for any other scheme.
pub fn web_host(url: &str) -> Option<String> {
    web_origin(url).map(|(_, h)| h)
}

fn loopback(host: &str) -> bool {
    host == "localhost" || host.ends_with(".localhost") || host.parse::<std::net::IpAddr>().is_ok_and(|ip| ip.is_loopback())
}

/// Whether a login for `sites` may be typed into a page at `url`: its host is one of them, and over plain http only on
/// this machine or where the login names the site `http://host` (an eavesdropper on the network would see it typed).
pub fn page_allowed(url: &str, sites: &[String]) -> Result<(), &'static str> {
    let Some((scheme, host)) = web_origin(url) else { return Err("site_mismatch") };
    if !site_matches(&host, sites) {
        return Err("site_mismatch");
    }
    let plain: Vec<String> = sites.iter().filter(|s| s.trim().get(..HTTP.len()).is_some_and(|h| h.eq_ignore_ascii_case(HTTP))).cloned().collect();
    if scheme == "http" && !loopback(&host) && !site_matches(&host, &plain) {
        return Err("insecure_page");
    }
    Ok(())
}

/// The login `credential` names: by id, else by name, else by name in any case.
pub fn find<'a>(logins: &'a [Value], credential: &str) -> Option<&'a Value> {
    logins
        .iter()
        .find(|l| text_of(&l["id"]) == credential)
        .or_else(|| logins.iter().find(|l| text_of(&l["name"]) == credential))
        .or_else(|| logins.iter().find(|l| text_of(&l["name"]).eq_ignore_ascii_case(credential)))
}

/// What a field gets typed with, or the error code.
pub fn value_for(login: &Value, field: &str) -> Result<String, &'static str> {
    let v = match field {
        "username" => text_of(&login["username"]),
        "password" => text_of(&login["password"]),
        "totp" => {
            if !login["totp"].is_object() {
                return Err("no_totp");
            }
            super::totp::current(&login["totp"]).ok_or("no_totp")?
        }
        _ => return Err("bad_request"),
    };
    if v.is_empty() {
        return Err("no_value");
    }
    Ok(v)
}

/// The field to type into, as an object: the element `sel` names in the page's document, else the one holding focus
/// (through shadow roots and frames this document can see into), else the frame element whose document it cannot.
/// A string is an error code.
const RESOLVE: &str = r#"((sel) => {
  if (sel) {
    try { return document.querySelector(sel) || 'no_element'; } catch (_) { return 'no_element'; }
  }
  let e = document.activeElement;
  for (let depth = 0; e && depth < 20; depth++) {
    if (e.shadowRoot && e.shadowRoot.activeElement) { e = e.shadowRoot.activeElement; continue; }
    if (e.tagName === 'IFRAME' || e.tagName === 'FRAME') {
      let inner = null;
      try { inner = e.contentDocument; } catch (_) {}
      if (!inner) return e;
      e = inner.activeElement; continue;
    }
    break;
  }
  return (!e || e === document.body) ? 'no_field' : e;
})"#;

/// Called on the resolved element: "ok" (a text-like input, textarea or editable element, now focused), "frame" (a
/// frame element whose document this one cannot see into; focus stays where it is in there) or "no_field".
const TAKE: &str = r#"function () {
  const e = this;
  if (e.tagName === 'IFRAME' || e.tagName === 'FRAME') {
    let inner = null;
    try { inner = e.contentDocument; } catch (_) {}
    return inner ? 'no_field' : 'frame';
  }
  const t = (e.type || '').toLowerCase();
  const text = e.tagName === 'INPUT'
    ? !['button','submit','checkbox','radio','file','hidden','image','reset','range','color'].includes(t)
    : (e.tagName === 'TEXTAREA' || e.isContentEditable);
  if (!text || e.disabled || e.readOnly) return 'no_field';
  e.focus();
  if (typeof e.select === 'function') { try { e.select(); } catch (_) {} }
  return 'ok';
}"#;

/// Called on the resolved element: whether focus, followed down from the top document as RESOLVE does, still ends on it.
const STILL: &str = r#"function () {
  let e = document.activeElement;
  for (let depth = 0; e && e !== this && depth < 20; depth++) {
    if (e.shadowRoot && e.shadowRoot.activeElement) { e = e.shadowRoot.activeElement; continue; }
    if (e.tagName === 'IFRAME' || e.tagName === 'FRAME') {
      let inner = null;
      try { inner = e.contentDocument; } catch (_) {}
      if (!inner) break;
      e = inner.activeElement; continue;
    }
    break;
  }
  return e === this;
}"#;

pub async fn fill(bot: &Bot, aid: &str, body: &Value) -> Value {
    let credential = text_of(&body["credential"]);
    let field = text_of(&body["field"]);
    let selector = body["selector"].as_str().filter(|s| !s.is_empty()).map(String::from);
    let target = body["target_id"].as_str().filter(|s| !s.is_empty()).map(String::from);
    let bad_shape = !body["selector"].is_null() && !body["selector"].is_string() || !body["target_id"].is_null() && !body["target_id"].is_string();
    if credential.is_empty() || credential.len() > 200 || !matches!(field.as_str(), "username" | "password" | "totp") || bad_shape
        || selector.as_ref().is_some_and(|s| s.len() > 2000) || target.as_ref().is_some_and(|t| t.len() > 200)
    {
        return no("bad_request");
    }
    let Some(runner) = super::runner_client() else { return no("no_run") };
    let logins = match super::fetch_logins(&runner, aid).await {
        Ok(l) => l,
        Err(e) if e.status == 409 || e.status == 403 => return no("no_run"),
        Err(_) => return no("credentials_unavailable"),
    };
    // Every live run of this bot hides these, whether or not it asked for this fill.
    let secrets = super::login_secrets(&logins);
    for turn in bot.turn_ids() {
        crate::redact::add_to(&turn, &secrets);
    }
    let Some(login) = find(&logins, &credential) else { return no("not_granted") };
    let sites: Vec<String> = login["sites"].as_array().into_iter().flatten().filter_map(|s| s.as_str().map(String::from)).collect();
    let value = match value_for(login, &field) {
        Ok(v) => v,
        Err(e) => return no(e),
    };
    // A code is short-lived and so not one of the turn's secrets; masked before it is typed, a code the bot reads
    // back from the page stays out of what is recorded.
    if field == "totp" {
        let mut codes = super::totp::window(&login["totp"]);
        codes.push(value.clone());
        for turn in bot.turn_ids() {
            crate::redact::add_codes_to(&turn, &codes);
        }
    }
    let Some(browser) = bot.ready(Duration::from_secs(30)).await else { return no("browser_unavailable") };
    let session = match page(&browser, target.as_deref()).await {
        Ok(s) => s,
        Err(e) => return no(e),
    };
    let (target_id, session) = session;
    let result = type_into(&browser, &target_id, &session, &sites, selector.as_deref(), &value).await;
    browser.detach(&session).await;
    match result {
        Ok(()) => json!({"ok": true}),
        Err(e) => no(e),
    }
}

async fn target_url(browser: &super::cdp::Browser, target: &str) -> Option<String> {
    let info = browser.call("Target.getTargetInfo", json!({"targetId": target}), None).await.ok()?;
    info["targetInfo"]["url"].as_str().map(String::from)
}

async fn eval(browser: &super::cdp::Browser, session: &str, expression: &str) -> Result<Value, &'static str> {
    let r = browser
        .call_within("Runtime.evaluate", json!({"expression": expression, "returnByValue": true}), Some(session), Duration::from_secs(10))
        .await
        .map_err(|_| "no_page")?;
    if r.get("exceptionDetails").is_some() {
        return Err("no_field");
    }
    Ok(r["result"]["value"].clone())
}

/// `function` called on the element `object`, its answer by value.
async fn call_on(browser: &super::cdp::Browser, session: &str, object: &str, function: &str) -> Result<Value, &'static str> {
    let params = json!({"objectId": object, "functionDeclaration": function, "returnByValue": true});
    let r = browser.call_within("Runtime.callFunctionOn", params, Some(session), Duration::from_secs(10)).await.map_err(|_| "no_page")?;
    if r.get("exceptionDetails").is_some() {
        return Err("no_field");
    }
    Ok(r["result"]["value"].clone())
}

/// The page to type into, attached: the one named, else the focused one, else the visible one, else the first.
async fn page(browser: &super::cdp::Browser, target: Option<&str>) -> Result<(String, String), &'static str> {
    let targets = browser.call("Target.getTargets", json!({}), None).await.map_err(|_| "no_page")?;
    let pages: Vec<String> = targets["targetInfos"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|t| t["type"] == "page" && !text_of(&t["url"]).starts_with("devtools://"))
        .map(|t| text_of(&t["targetId"]))
        .collect();
    if let Some(t) = target {
        if !pages.iter().any(|p| p == t) {
            return Err("no_page");
        }
        return browser.attach(t).await.map(|s| (t.to_string(), s)).map_err(|_| "no_page");
    }
    // Unnamed, only a web page can be the one: a new tab or a chrome:// page takes no login, and on a display with no
    // window stacking more than one tab can say it is visible.
    let web = targets["targetInfos"].as_array().into_iter().flatten().filter(|t| t["type"] == "page" && web_host(&text_of(&t["url"])).is_some());
    let mut best: Option<(u8, String, String)> = None;
    for t in web.map(|t| text_of(&t["targetId"])).take(20) {
        let Ok(session) = browser.attach(&t).await else { continue };
        let score = match eval(browser, &session, "[document.hasFocus(), document.visibilityState === 'visible']").await {
            Ok(v) => (v[0] == true) as u8 * 2 + (v[1] == true) as u8,
            Err(_) => 0,
        };
        if best.as_ref().is_none_or(|(s, _, _)| score > *s) {
            if let Some((_, _, old)) = best.take() {
                browser.detach(&old).await;
            }
            best = Some((score, t.clone(), session));
        } else {
            browser.detach(&session).await;
        }
    }
    best.map(|(_, t, s)| (t, s)).ok_or("no_page")
}

async fn type_into(
    browser: &super::cdp::Browser,
    target: &str,
    session: &str,
    sites: &[String],
    selector: Option<&str>,
    value: &str,
) -> Result<(), &'static str> {
    let top = || async { page_allowed(&target_url(browser, target).await.unwrap_or_default(), sites) };
    top().await?;
    let script = format!("{RESOLVE}({})", serde_json::to_string(&selector).unwrap_or_default());
    let found = browser
        .call_within("Runtime.evaluate", json!({"expression": script}), Some(session), Duration::from_secs(10))
        .await
        .map_err(|_| "no_page")?;
    if found.get("exceptionDetails").is_some() {
        return Err("no_field");
    }
    let Some(object) = found["result"]["objectId"].as_str().filter(|_| found["result"]["subtype"] == "node") else {
        return Err(if found["result"]["value"] == "no_element" { "no_element" } else { "no_field" });
    };
    match call_on(browser, session, object, TAKE).await?.as_str() {
        Some("ok") => {}
        Some("frame") => frame_allowed(browser, session, object, sites).await?,
        _ => return Err("no_field"),
    }
    // The page may have navigated, or its script moved focus, since the field was found. A field in a frame this
    // document cannot see into is checked as far as that frame: where focus is inside it is the frame's own.
    top().await?;
    if call_on(browser, session, object, STILL).await? != true {
        return Err("focus_changed");
    }
    // What remains is the time between that answer and the text arriving: a page task scheduled to run in between
    // can still move focus, and only Chrome could close that.
    browser.call("Input.insertText", json!({"text": value}), Some(session)).await.map(|_| ()).map_err(|_| "no_page")
}

/// Whether the cross-origin frame element `object` holds a page the login may be typed into, from Chrome's frame tree
/// (or the frame's own target, out of process), not from what its script says.
async fn frame_allowed(browser: &super::cdp::Browser, session: &str, object: &str, sites: &[String]) -> Result<(), &'static str> {
    let url = frame_url_of(browser, session, object).await.unwrap_or_default();
    page_allowed(&url, sites)
}

async fn frame_url_of(browser: &super::cdp::Browser, session: &str, object: &str) -> Option<String> {
    let node = browser.call("DOM.describeNode", json!({"objectId": object}), Some(session)).await.ok()?;
    let frame = node["node"]["frameId"].as_str()?.to_string();
    let tree = browser.call("Page.getFrameTree", json!({}), Some(session)).await.ok();
    if let Some(url) = tree.and_then(|t| frame_url(&t["frameTree"], &frame)) {
        return Some(url);
    }
    let targets = browser.call("Target.getTargets", json!({}), None).await.ok()?;
    targets["targetInfos"].as_array()?.iter().find(|t| text_of(&t["targetId"]) == frame).map(|t| text_of(&t["url"]))
}

fn frame_url(tree: &Value, id: &str) -> Option<String> {
    if tree["frame"]["id"].as_str() == Some(id) {
        return tree["frame"]["url"].as_str().map(String::from);
    }
    tree["childFrames"].as_array()?.iter().find_map(|c| frame_url(c, id))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_login_fills_only_on_its_sites_and_their_subdomains() {
        let sites = vec!["acme.example".to_string(), "*.shop.example".to_string()];
        assert!(site_matches("acme.example", &sites));
        assert!(site_matches("login.ACME.example.", &sites));
        assert!(site_matches("a.shop.example", &sites));
        assert!(!site_matches("evilacme.example", &sites));
        assert!(!site_matches("acme.example.evil.example", &sites));
        assert!(!site_matches("example", &sites));
        assert!(!site_matches("acme.example", &[]));
        assert_eq!(web_host("https://Login.Acme.example:8443/a?b").as_deref(), Some("login.acme.example"));
        assert_eq!(web_host("file:///etc/passwd"), None);
        assert_eq!(web_host("javascript:alert(1)"), None);
        assert_eq!(web_host("data:text/html,x"), None);
        assert!(site_matches("login.practice.test", &["HTTP://practice.test".to_string()]));
    }

    #[test]
    fn a_plain_http_page_takes_a_login_only_on_this_machine_or_where_the_login_says_http() {
        let https = vec!["acme.example".to_string()];
        let plain = vec!["acme.example".to_string(), "http://login.practice.test".to_string()];
        assert_eq!(page_allowed("https://login.acme.example/in", &https), Ok(()));
        assert_eq!(page_allowed("http://login.acme.example/in", &https), Err("insecure_page"));
        assert_eq!(page_allowed("http://login.acme.example/in", &plain), Err("insecure_page"), "only the site written http://");
        assert_eq!(page_allowed("http://login.practice.test:8080/in", &plain), Ok(()));
        assert_eq!(page_allowed("http://a.login.practice.test/in", &plain), Ok(()));
        assert_eq!(page_allowed("https://login.practice.test/in", &plain), Ok(()));
        assert_eq!(page_allowed("http://other.practice.test/in", &plain), Err("site_mismatch"));
        let local = vec!["localhost".to_string(), "127.0.0.1".to_string(), "::1".to_string(), "app.localhost".to_string()];
        for url in ["http://localhost:3000/", "http://127.0.0.1:8080/", "http://[::1]/", "http://app.localhost/"] {
            assert_eq!(page_allowed(url, &local), Ok(()), "{url}");
        }
        assert_eq!(page_allowed("file:///etc/passwd", &https), Err("site_mismatch"));
        assert_eq!(page_allowed("", &https), Err("site_mismatch"));
    }

    #[test]
    fn the_named_login_is_found_and_its_field_chosen() {
        let logins = vec![
            json!({"id": "c1", "name": "Acme", "username": "ops@acme.example", "password": "pw-one", "sites": ["acme.example"], "totp": null}),
            json!({"id": "c2", "name": "acme", "username": "", "password": "pw-two", "sites": [], "totp": {"secret": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "digits": 6, "period": 30, "algorithm": "SHA1"}}),
        ];
        assert_eq!(text_of(&find(&logins, "c2").unwrap()["id"]), "c2");
        assert_eq!(text_of(&find(&logins, "Acme").unwrap()["id"]), "c1");
        assert_eq!(text_of(&find(&logins, "ACME").unwrap()["id"]), "c1");
        assert!(find(&logins, "Other").is_none());
        assert_eq!(value_for(&logins[0], "totp"), Err("no_totp"));
        assert_eq!(value_for(&logins[1], "username"), Err("no_value"));
        assert_eq!(value_for(&logins[1], "totp").unwrap().len(), 6);
        assert_eq!(value_for(&logins[0], "secret"), Err("bad_request"));
    }

    #[test]
    fn no_reply_ever_carries_a_value() {
        // Every answer the endpoint can give is built by `no` or is `{"ok": true}`.
        for e in ["not_granted", "site_mismatch", "insecure_page", "focus_changed", "no_totp", "no_page", "no_field", "no_element", "no_run", "browser_unavailable"] {
            let v = no(e);
            assert_eq!(v.as_object().unwrap().len(), 2);
            assert_eq!(v["ok"], false);
        }
    }
}
