//! `POST {TICO_BROWSER_URL}/fill`: type a granted login's username, password or current authenticator code into
//! the focused field of the bot's page. The login is read from the hub with the runner's token for the run whose URL
//! made the call, the page's top-level host (as Chrome reports it, not as page script says) must be one of the login's
//! sites, and so must the frame holding the focused field. The reply is only `ok` or an error code, never a value.

use std::time::Duration;

use serde_json::{json, Value};

use super::Bot;
use crate::util::text_of;

fn no(error: &str) -> Value {
    json!({"ok": false, "error": error})
}

/// `host` is one of `sites` or below one (`login.acme.example` matches `acme.example`).
pub fn site_matches(host: &str, sites: &[String]) -> bool {
    let host = host.trim_end_matches('.').to_ascii_lowercase();
    !host.is_empty()
        && sites.iter().map(|s| s.trim().trim_start_matches("*.").trim_end_matches('.').to_ascii_lowercase()).filter(|s| !s.is_empty()).any(|s| {
            host == s || host.strip_suffix(&s).is_some_and(|head| head.ends_with('.'))
        })
}

/// The host of an http(s) URL; None for any other scheme.
pub fn web_host(url: &str) -> Option<String> {
    let u = url::Url::parse(url).ok()?;
    matches!(u.scheme(), "http" | "https").then(|| u.host_str().map(|h| h.trim_start_matches('[').trim_end_matches(']').to_ascii_lowercase()))?
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

/// Which field a script found focused: a text-like input, textarea or editable element ("ok"), none ("no_field"),
/// or one inside a frame this document cannot see into ("frame").
const FOCUSED: &str = r#"(() => {
  let e = document.activeElement;
  for (let depth = 0; e && depth < 20; depth++) {
    if (e.shadowRoot && e.shadowRoot.activeElement) { e = e.shadowRoot.activeElement; continue; }
    if (e.tagName === 'IFRAME' || e.tagName === 'FRAME') {
      let inner = null;
      try { inner = e.contentDocument; } catch (_) {}
      if (!inner) return 'frame';
      e = inner.activeElement; continue;
    }
    break;
  }
  if (!e || e === document.body) return 'no_field';
  const t = (e.type || '').toLowerCase();
  if (e.tagName === 'INPUT') return ['button','submit','checkbox','radio','file','hidden','image','reset','range','color'].includes(t) ? 'no_field' : 'ok';
  return (e.tagName === 'TEXTAREA' || e.isContentEditable) ? 'ok' : 'no_field';
})()"#;

/// The frame element that holds focus beyond this document's reach.
const FOCUSED_FRAME: &str = r#"(() => {
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
  return null;
})()"#;

const FOCUS: &str = r#"((sel) => {
  let e = null;
  try { e = document.querySelector(sel); } catch (_) { return 'bad_selector'; }
  if (!e) return 'no_element';
  e.focus();
  if (typeof e.select === 'function') { try { e.select(); } catch (_) {} }
  return document.activeElement === e || (e.shadowRoot && e.shadowRoot.activeElement) ? 'ok' : 'no_field';
})"#;

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
    let mut best: Option<(u8, String, String)> = None;
    for t in pages.iter().take(20) {
        let Ok(session) = browser.attach(t).await else { continue };
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
    let top = || async { target_url(browser, target).await.and_then(|u| web_host(&u)) };
    if !top().await.is_some_and(|h| site_matches(&h, sites)) {
        return Err("site_mismatch");
    }
    if let Some(sel) = selector {
        let script = format!("{FOCUS}({})", serde_json::to_string(sel).unwrap_or_default());
        match eval(browser, session, &script).await?.as_str() {
            Some("ok") => {}
            Some("bad_selector") | Some("no_element") => return Err("no_element"),
            _ => return Err("no_field"),
        }
    }
    match eval(browser, session, FOCUSED).await?.as_str() {
        Some("ok") => {}
        Some("frame") => {
            if !frame_host(browser, session).await.is_some_and(|h| site_matches(&h, sites)) {
                return Err("site_mismatch");
            }
        }
        _ => return Err("no_field"),
    }
    // The page may have navigated while the field was found.
    if !top().await.is_some_and(|h| site_matches(&h, sites)) {
        return Err("site_mismatch");
    }
    browser.call("Input.insertText", json!({"text": value}), Some(session)).await.map(|_| ()).map_err(|_| "no_page")
}

/// The host of the cross-origin frame that holds focus, from Chrome's frame tree (or its own target, out of process).
async fn frame_host(browser: &super::cdp::Browser, session: &str) -> Option<String> {
    let r = browser.call("Runtime.evaluate", json!({"expression": FOCUSED_FRAME}), Some(session)).await.ok()?;
    let object = r["result"]["objectId"].as_str()?.to_string();
    let node = browser.call("DOM.describeNode", json!({"objectId": object}), Some(session)).await.ok()?;
    let frame = node["node"]["frameId"].as_str()?.to_string();
    let tree = browser.call("Page.getFrameTree", json!({}), Some(session)).await.ok();
    let mut url = tree.and_then(|t| frame_url(&t["frameTree"], &frame));
    if url.is_none() {
        let targets = browser.call("Target.getTargets", json!({}), None).await.ok()?;
        url = targets["targetInfos"].as_array()?.iter().find(|t| text_of(&t["targetId"]) == frame).map(|t| text_of(&t["url"]));
    }
    web_host(&url?)
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
        for e in ["not_granted", "site_mismatch", "no_totp", "no_page", "no_field", "no_element", "no_run", "browser_unavailable"] {
            let v = no(e);
            assert_eq!(v.as_object().unwrap().len(), 2);
            assert_eq!(v["ok"], false);
        }
    }
}
