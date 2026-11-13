//! `fill` and `copy`: a login value the hub sent with the command (a username, a password, or a current
//! authenticator code; never the 2FA key) is typed into the page or put on this computer's clipboard. Before a value
//! is typed, the page's top-level host (as Chrome reports it, not as page script says) and the origin of the field's
//! own document must be one of the tool's sites, over https unless the site was written `http://host`; a password
//! goes only into a password field. On any failed check the value is dropped. A reply never carries it.

use std::time::Duration;

use serde_json::{json, Value};

use super::verbs::{call_on, create_world, eval_in, failed, node_of, Done, Session};
use crate::util::text_of;

fn web_url(text: &str) -> Option<url::Url> {
    if text
        .chars()
        .any(|c| c.is_control() || c.is_whitespace() || c == '\\')
    {
        return None;
    }
    let url = url::Url::parse(text).ok()?;
    (matches!(url.scheme(), "http" | "https")
        && url.username().is_empty()
        && url.password().is_none())
    .then_some(url)
}

/// Whether a login for `sites` may be typed into a page at `url`: its host is one of them, and over plain http only
/// where the tool names the site `http://host` (an eavesdropper on the network would see it typed).
pub fn page_allowed(url: &str, sites: &[String]) -> bool {
    let Some(page) = web_url(url) else {
        return false;
    };
    sites.iter().any(|site| {
        let address = if site.contains("://") {
            site.trim().to_string()
        } else {
            format!("https://{}", site.trim().trim_start_matches("*."))
        };
        let Some(own) = web_url(&address) else {
            return false;
        };
        let (Some(host), Some(allowed)) = (page.host_str(), own.host_str()) else {
            return false;
        };
        let host = host.trim_end_matches('.');
        let allowed = allowed.trim_end_matches('.');
        if matches!(own.host(), Some(url::Host::Domain(_))) && !allowed.contains('.') {
            return false;
        }
        let under = host == allowed
            || (matches!(own.host(), Some(url::Host::Domain(_)))
                && host
                    .strip_suffix(allowed)
                    .is_some_and(|head| head.ends_with('.')));
        under && page.port() == own.port() && (page.scheme() == "https" || own.scheme() == "http")
    })
}

fn not_on_site() -> super::verbs::Failed {
    failed(
        "not_on_site",
        "This page is not one of this tool's sites, so nothing was typed.",
    )
}

/// The element holding focus, followed down through shadow roots and frames this document can see into; a frame
/// element whose document it cannot see into is returned as it is. A string is an error.
const FOCUSED: &str = r#"(() => {
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
  return (!e || e === document.body || e === document.documentElement) ? 'no_field' : e;
})()"#;

/// Called on the target element: a text field is focused with its text selected, and described (whether it is a
/// password field, and its document's origin as the browser keeps it); a frame element whose document this one
/// cannot see into says so.
const TAKE: &str = r#"function () {
  const e = this;
  if (e.tagName === 'IFRAME' || e.tagName === 'FRAME') {
    let inner = null;
    try { inner = e.contentDocument; } catch (_) {}
    return { kind: inner ? 'no_field' : 'frame' };
  }
  const t = (e.type || '').toLowerCase();
  const text = e.tagName === 'INPUT'
    ? !['button','submit','checkbox','radio','file','hidden','image','reset','range','color'].includes(t)
    : (e.tagName === 'TEXTAREA' || e.isContentEditable);
  if (!text || e.disabled || e.readOnly) return { kind: 'no_field' };
  e.focus();
  if (typeof e.select === 'function') { try { e.select(); } catch (_) {} }
  const view = e.ownerDocument.defaultView;
  return { kind: 'field', password: e.tagName === 'INPUT' && t === 'password', origin: view ? view.origin : 'null' };
}"#;

/// Called on the target element: whether focus in its document, followed down as FOCUSED does, still ends on it. If
/// it does, the element is remembered as holding a login value (`__ticoFilled`, in the runner's world of the highest
/// window this one can reach, where `read` and `screenshot` run): it is shown as `filled`, never its value, and is
/// covered in a screenshot, whatever its type later becomes.
const STILL: &str = r#"function () {
  let e = this.ownerDocument.activeElement;
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
  if (e !== this) return false;
  let w = this.ownerDocument.defaultView || globalThis;
  for (let depth = 0; depth < 20; depth++) {
    try {
      const up = w.parent;
      if (!up || up === w || !up.document) break;
      w = up;
    } catch (_) { break; }
  }
  const list = (w.__ticoFilled || []).filter((r) => r.deref());
  if (!list.some((r) => r.deref() === this)) list.push(new WeakRef(this));
  w.__ticoFilled = list;
  return true;
}"#;

/// Where a script runs: a CDP session (the page's, or an out-of-process frame's) and a context in it.
struct Place {
    session: String,
    context: i64,
}

/// The checks a field passed, as plain facts: what `checked` decides on.
#[derive(Debug, PartialEq)]
pub struct Field<'a> {
    pub top_url: &'a str,
    pub origin: &'a str,
    pub password: bool,
}

/// Whether `value` for `field` may go into a field with these facts.
pub fn checked(field: &str, f: &Field, sites: &[String]) -> Done<()> {
    if !page_allowed(f.top_url, sites) || !page_allowed(f.origin, sites) {
        return Err(not_on_site());
    }
    if field == "password" && !f.password {
        return Err(failed(
            "not_a_password_field",
            "That is not a password field, so the password was not typed.",
        ));
    }
    Ok(())
}

/// Type `value` into the element `r` names, or the focused one.
pub async fn fill(
    s: &mut Session,
    sites: &[String],
    field: &str,
    r: Option<&str>,
    value: &str,
) -> Done<()> {
    if !matches!(field, "username" | "password" | "code") {
        return Err(failed(
            "bad_request",
            "field is username, password or code.",
        ));
    }
    if value.is_empty() {
        return Err(failed("no_value", "This account has no such value."));
    }
    if !page_allowed(&s.url().await, sites) {
        return Err(not_on_site());
    }
    let mut attached = vec![];
    let done = fill_in(s, sites, field, r, value, &mut attached).await;
    let browser = s.browser();
    for a in attached {
        browser.detach(&a).await;
    }
    done
}

async fn fill_in(
    s: &mut Session,
    sites: &[String],
    field: &str,
    r: Option<&str>,
    value: &str,
    attached: &mut Vec<String>,
) -> Done<()> {
    let browser = s.browser();
    let mut place = Place {
        session: s.page_session().to_string(),
        context: s.world().await?,
    };
    let mut object = match r {
        Some(r) => s.element(r).await?,
        None => focused(&browser, &place).await?,
    };
    let mut depth = 0;
    let took = loop {
        let took = call_on(&browser, &place.session, &object, TAKE).await?;
        match took["kind"].as_str() {
            Some("field") => break took,
            Some("frame") if depth < 3 => {
                depth += 1;
                place = enter_frame(&browser, &place, &object, attached).await?;
                object = focused(&browser, &place).await?;
            }
            _ => return Err(no_field()),
        }
    };
    let top = s.url().await;
    checked(
        field,
        &Field {
            top_url: &top,
            origin: &text_of(&took["origin"]),
            password: took["password"] == true,
        },
        sites,
    )?;
    // The page may have navigated, or its script moved focus, since the field was found.
    if !page_allowed(&s.url().await, sites) {
        return Err(not_on_site());
    }
    if call_on(&browser, &place.session, &object, STILL).await? != true {
        return Err(failed(
            "focus_changed",
            "The page moved focus away from the field, so nothing was typed.",
        ));
    }
    // What remains is the moment between that answer and the text arriving: a page task scheduled in between can
    // still move focus, and only Chrome could close that.
    browser
        .call(
            "Input.insertText",
            json!({"text": value}),
            Some(&place.session),
        )
        .await
        .map(|_| ())
        .map_err(|_| failed("page_error", "The page stopped answering."))
}

fn no_field() -> super::verbs::Failed {
    failed(
        "no_field",
        "No text field is focused there: click the field first or name its ref.",
    )
}

async fn focused(browser: &super::cdp::Browser, place: &Place) -> Done<String> {
    let r = eval_in(browser, &place.session, place.context, FOCUSED, false)
        .await
        .map_err(|_| no_field())?;
    node_of(&r).ok_or_else(no_field)
}

/// The frame the element `object` holds, entered: a world in it when it is part of this page's process, else a
/// session on its own target (an out-of-process frame's target id is its frame id).
async fn enter_frame(
    browser: &super::cdp::Browser,
    place: &Place,
    object: &str,
    attached: &mut Vec<String>,
) -> Done<Place> {
    let node = browser
        .call(
            "DOM.describeNode",
            json!({"objectId": object}),
            Some(&place.session),
        )
        .await
        .map_err(|_| no_field())?;
    let frame = node["node"]["frameId"]
        .as_str()
        .filter(|f| !f.is_empty())
        .ok_or_else(no_field)?
        .to_string();
    let tree = browser
        .call("Page.getFrameTree", json!({}), Some(&place.session))
        .await
        .unwrap_or_default();
    if has_frame(&tree["frameTree"], &frame) {
        let context = create_world(browser, &place.session, &frame).await?;
        return Ok(Place {
            session: place.session.clone(),
            context,
        });
    }
    let session = browser.attach(&frame).await.map_err(|_| no_field())?;
    attached.push(session.clone());
    let tree = browser
        .call_within(
            "Page.getFrameTree",
            json!({}),
            Some(&session),
            Duration::from_secs(10),
        )
        .await
        .map_err(|_| no_field())?;
    let main = text_of(&tree["frameTree"]["frame"]["id"]);
    let context = create_world(browser, &session, &main).await?;
    Ok(Place { session, context })
}

fn has_frame(tree: &Value, id: &str) -> bool {
    tree["frame"]["id"].as_str() == Some(id)
        || tree["childFrames"]
            .as_array()
            .is_some_and(|c| c.iter().any(|t| has_frame(t, id)))
}

// ---- copy ----

/// How long a copied value may stay on the clipboard.
const CLIPBOARD_FOR: Duration = Duration::from_secs(30);

/// The commands that write and read this computer's clipboard.
fn clipboard() -> Option<(Vec<String>, Vec<String>)> {
    let path = std::env::var("PATH").unwrap_or_default();
    let has = |n: &str| crate::env::which(n, &path).is_some();
    let v = |a: &[&str]| a.iter().map(|s| s.to_string()).collect::<Vec<_>>();
    match std::env::consts::OS {
        "macos" => Some((v(&["pbcopy"]), v(&["pbpaste"]))),
        "linux" if std::env::var_os("WAYLAND_DISPLAY").is_some() && has("wl-copy") => {
            Some((v(&["wl-copy"]), v(&["wl-paste", "-n"])))
        }
        "linux" if has("xclip") => Some((
            v(&["xclip", "-selection", "clipboard"]),
            v(&["xclip", "-o", "-selection", "clipboard"]),
        )),
        _ => None,
    }
}

async fn run_with_input(argv: &[String], input: &[u8]) -> bool {
    use tokio::io::AsyncWriteExt;
    let mut cmd = tokio::process::Command::new(&argv[0]);
    cmd.args(&argv[1..])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    if let Some(d) = super::launch::display() {
        cmd.env("DISPLAY", d);
    }
    let Ok(mut child) = cmd.spawn() else {
        return false;
    };
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(input).await;
    }
    matches!(
        tokio::time::timeout(Duration::from_secs(5), child.wait()).await,
        Ok(Ok(s)) if s.success()
    )
}

async fn read_clipboard(argv: &[String]) -> Option<Vec<u8>> {
    let mut cmd = tokio::process::Command::new(&argv[0]);
    cmd.args(&argv[1..])
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true);
    if let Some(d) = super::launch::display() {
        cmd.env("DISPLAY", d);
    }
    let out = tokio::time::timeout(Duration::from_secs(5), cmd.output())
        .await
        .ok()?
        .ok()?;
    out.status.success().then_some(out.stdout)
}

/// Put `value` on this computer's clipboard, and clear it after 30 s if it still holds it.
pub async fn copy(value: &str) -> Done<()> {
    if value.is_empty() {
        return Err(failed("no_value", "This account has no such value."));
    }
    let (write, read) = clipboard().ok_or_else(|| {
        failed(
            "no_clipboard",
            "This computer has no clipboard the runner can use.",
        )
    })?;
    if !run_with_input(&write, value.as_bytes()).await {
        return Err(failed("no_clipboard", "The clipboard did not take it."));
    }
    let value = value.to_string();
    tokio::spawn(async move {
        tokio::time::sleep(CLIPBOARD_FOR).await;
        // Something copied since is the person's own: only the value itself is cleared.
        if read_clipboard(&read).await.as_deref() == Some(value.as_bytes()) {
            run_with_input(&write, b"").await;
        }
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_runner_and_server_share_the_site_contract() {
        let cases: Value =
            serde_json::from_str(include_str!("../../../contracts/website-sites.json")).unwrap();
        for case in cases["cases"].as_array().unwrap() {
            let sites = case["sites"]
                .as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_str().unwrap().to_string())
                .collect::<Vec<_>>();
            assert_eq!(
                page_allowed(case["url"].as_str().unwrap(), &sites),
                case["matches"].as_bool().unwrap(),
                "{}",
                case["name"]
            );
        }
    }

    #[test]
    fn a_plain_http_page_takes_a_login_only_where_the_tool_says_http() {
        let https = vec!["acme.example".to_string()];
        let plain = vec![
            "acme.example".to_string(),
            "http://login.practice.test".to_string(),
        ];
        assert!(page_allowed("https://login.acme.example/in", &https));
        assert!(!page_allowed("http://login.acme.example/in", &https));
        assert!(
            !page_allowed("http://login.acme.example/in", &plain),
            "only the site written http://"
        );
        assert!(!page_allowed("http://login.practice.test:8080/in", &plain));
        assert!(page_allowed(
            "http://login.practice.test:8080/in",
            &["http://login.practice.test:8080".into()]
        ));
        assert!(!page_allowed(
            "http://login.practice.test:8081/in",
            &["http://login.practice.test:8080".into()]
        ));
        assert!(page_allowed("http://a.login.practice.test/in", &plain));
        assert!(page_allowed("https://login.practice.test/in", &plain));
        assert!(!page_allowed("http://other.practice.test/in", &plain));
        // No exception for this machine: a local page is plain http like any other.
        assert!(!page_allowed(
            "http://localhost:3000/",
            &["localhost".to_string()]
        ));
        assert!(page_allowed(
            "http://127.0.0.1:8080/",
            &["http://127.0.0.1:8080".to_string()]
        ));
        assert!(!page_allowed("file:///etc/passwd", &https));
        assert!(!page_allowed("", &https));
        // A field's document is checked by its origin, as the browser keeps it.
        assert!(page_allowed("https://acme.example", &https));
        assert!(!page_allowed("null", &https), "a sandboxed frame");
    }

    #[test]
    fn a_value_goes_only_into_a_field_on_the_sites_and_a_password_only_into_a_password_field() {
        let sites = vec!["acme.example".to_string()];
        let field = |top, origin, password| Field {
            top_url: top,
            origin,
            password,
        };
        let ok = field(
            "https://acme.example/in",
            "https://login.acme.example",
            true,
        );
        assert_eq!(checked("password", &ok, &sites), Ok(()));
        assert_eq!(checked("username", &ok, &sites), Ok(()));
        let code = |f: &Field, kind| checked(kind, f, &sites).unwrap_err().code;
        assert_eq!(
            code(
                &field("https://evil.example/", "https://acme.example", true),
                "password"
            ),
            "not_on_site",
            "the top page decides too"
        );
        assert_eq!(
            code(
                &field("https://acme.example/", "https://evil.example", true),
                "password"
            ),
            "not_on_site",
            "a frame from another site on the right page"
        );
        assert_eq!(
            code(
                &field("http://acme.example/", "http://acme.example", true),
                "password"
            ),
            "not_on_site"
        );
        assert_eq!(
            code(
                &field("https://acme.example/", "https://acme.example", false),
                "password"
            ),
            "not_a_password_field"
        );
        assert_eq!(
            checked(
                "code",
                &field("https://acme.example/", "https://acme.example", false),
                &sites
            ),
            Ok(()),
            "a code is typed as text"
        );
    }
}
