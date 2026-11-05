//! The Tico desktop app on this Mac (app/, bundle id team.tico.app): the orgs it keeps in orgs.json, read here and
//! never written, and the link that opens Tico's approval page inside it, on the org's own sign-in.
use crate::{
    settings::{self, Destination},
    signin,
};
use reqwest::Url;
use serde::Serialize;
use serde_json::Value;
use std::{
    path::{Path, PathBuf},
    time::SystemTime,
};

/// The Tico desktop app's bundle id; its folders are named after it.
pub const APP_ID: &str = "team.tico.app";
/// The orgs.json versions this reads (app/src/orgs.rs VERSION).
const VERSION: u64 = 1;
/// Most characters kept for an org's name or email, as the desktop app keeps them.
const DETAIL_LIMIT: usize = 200;

/// One org of the desktop app: its name, browser address and the email its page reported, and whether it is the one
/// open there.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct DesktopOrg {
    pub name: String,
    pub url: String,
    pub email: String,
    pub active: bool,
}

/// Where the desktop app keeps its orgs, on a Mac.
pub fn path() -> Option<PathBuf> {
    if !cfg!(target_os = "macos") {
        return None;
    }
    let home = std::env::var_os("HOME")?;
    Some(Path::new(&home).join(format!("Library/Application Support/{APP_ID}/orgs.json")))
}

/// The orgs in an orgs.json text, the open one marked. Nothing from a file that is not JSON or is another version;
/// an entry without an id or with an address the Recorder cannot sign in to is left out, and so is a second entry
/// for the same address. A missing or unknown open org falls back to the first, as in the desktop app.
pub fn parse(text: &str) -> Vec<DesktopOrg> {
    let Ok(doc) = serde_json::from_str::<Value>(text) else {
        return vec![];
    };
    if doc["version"].as_u64() != Some(VERSION) {
        return vec![];
    }
    let clip = |value: &Value| -> String {
        value
            .as_str()
            .unwrap_or_default()
            .trim()
            .chars()
            .take(DETAIL_LIMIT)
            .collect()
    };
    let mut rows: Vec<(String, DesktopOrg)> = vec![];
    for org in doc["orgs"].as_array().into_iter().flatten() {
        let id = org["id"].as_str().unwrap_or_default();
        let Ok(url) = settings::safe_url(org["url"].as_str().unwrap_or_default()) else {
            continue;
        };
        if id.is_empty() || rows.iter().any(|(_, row)| row.url == url) {
            continue;
        }
        let mut name = clip(&org["name"]);
        if name.is_empty() {
            name = host(&url);
        }
        rows.push((
            id.to_owned(),
            DesktopOrg {
                name,
                email: clip(&org["email"]),
                url,
                active: false,
            },
        ));
    }
    let active = doc["active"].as_str().unwrap_or_default();
    let open = rows
        .iter()
        .position(|(id, _)| id == active)
        .unwrap_or_default();
    if let Some((_, row)) = rows.get_mut(open) {
        row.active = true;
    }
    rows.into_iter().map(|(_, row)| row).collect()
}

fn host(url: &str) -> String {
    Url::parse(url)
        .ok()
        .and_then(|url| url.host_str().map(str::to_owned))
        .unwrap_or_default()
}

/// Whether `destination` is a sign-in to the Tico at `url`: its server is that address or its runner, the way
/// signing in matches a connection, or it was signed in on that host.
pub fn matches(url: &str, destination: &Destination) -> bool {
    let Ok(candidates) = signin::candidates(url) else {
        return false;
    };
    candidates.iter().any(|base| *base == destination.base_url)
        || candidates
            .first()
            .is_some_and(|base| !host(base).is_empty() && host(base) == destination.name)
}

/// The desktop orgs the Recorder has no sign-in for, in the order `sign_in_desktop` signs in to them: the one open
/// in the desktop app first, then the rest as the desktop app lists them.
pub fn to_sign_in(orgs: &[DesktopOrg], destinations: &[Destination]) -> Vec<DesktopOrg> {
    let mut rows: Vec<DesktopOrg> = orgs
        .iter()
        .filter(|org| !destinations.iter().any(|row| matches(&org.url, row)))
        .cloned()
        .collect();
    rows.sort_by_key(|org| !org.active);
    rows
}

/// The link that opens Tico's approval page (`authorize`, on the org's own address) in the desktop app, on that
/// org: `tico://open?org=<org address>&path=<the page, from the org's address>`. None when the page is not on the
/// org's address, so only the browser can show it.
pub fn approval_link(org: &str, authorize: &str) -> Option<String> {
    let org = settings::safe_url(org).ok()?;
    let path = authorize.strip_prefix(&format!("{org}/"))?;
    if path.is_empty() || path.starts_with('/') {
        return None;
    }
    Url::parse_with_params("tico://open", [("org", org.as_str()), ("path", path)])
        .ok()
        .map(String::from)
}

/// Opens `link` in the Tico desktop app itself (not another app that took the `tico` scheme). False when that app
/// is not installed or the link could not be handed to it.
pub fn open_in_app(link: &str) -> bool {
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("/usr/bin/open")
            .args(["-b", APP_ID, link])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .is_ok_and(|status| status.success())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = link;
        false
    }
}

/// The desktop app's orgs, read again only when orgs.json changes. `None` when there is no orgs.json.
#[derive(Default)]
pub struct Watch {
    path: Option<PathBuf>,
    seen: Option<(SystemTime, u64)>,
    orgs: Option<Vec<DesktopOrg>>,
}

impl Watch {
    pub fn new(path: Option<PathBuf>) -> Self {
        Self {
            path,
            seen: None,
            orgs: None,
        }
    }
    pub fn orgs(&mut self) -> Option<&[DesktopOrg]> {
        let path = self.path.as_ref()?;
        let Ok(meta) = std::fs::metadata(path) else {
            self.seen = None;
            self.orgs = None;
            return None;
        };
        let stamp = (
            meta.modified().unwrap_or(SystemTime::UNIX_EPOCH),
            meta.len(),
        );
        if self.seen != Some(stamp) {
            self.seen = Some(stamp);
            self.orgs = Some(
                std::fs::read_to_string(path)
                    .map(|text| parse(&text))
                    .unwrap_or_default(),
            );
        }
        self.orgs.as_deref()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn org(name: &str, url: &str, active: bool) -> DesktopOrg {
        DesktopOrg {
            name: name.into(),
            url: url.into(),
            email: String::new(),
            active,
        }
    }
    fn destination(name: &str, base_url: &str) -> Destination {
        Destination {
            id: name.into(),
            name: name.into(),
            base_url: base_url.into(),
            actor: String::new(),
        }
    }

    #[test]
    fn orgs_json_is_read_with_the_open_org_and_anything_unreadable_left_out() {
        let text = r#"{"version":1,"active":"o_bbbbbbbb","orgs":[
            {"id":"o_aaaaaaaa","url":"https://acme.example.com/","store":"6a0c1c1e-0000-4000-8000-000000000001",
             "name":"Acme Lights","email":"ana@acme.example","added_at":"2026-11-03T18:00:00Z"},
            {"id":"o_bbbbbbbb","url":"https://tidy.example.com/","name":"","email":" ben@tidy.example ","sign_in":true},
            {"id":"o_cccccccc","url":"http://plain.example.com/"},
            {"id":"o_dddddddd","url":"https://acme.example.com"},
            {"url":"https://noid.example.com/"},
            {"id":"o_eeeeeeee","url":"http://localhost:8000/","name":"Local"}]}"#;
        assert_eq!(
            parse(text),
            [
                DesktopOrg {
                    name: "Acme Lights".into(),
                    url: "https://acme.example.com".into(),
                    email: "ana@acme.example".into(),
                    active: false
                },
                DesktopOrg {
                    name: "tidy.example.com".into(),
                    url: "https://tidy.example.com".into(),
                    email: "ben@tidy.example".into(),
                    active: true
                },
                org("Local", "http://localhost:8000", false),
            ]
        );
        // An unknown open org falls back to the first.
        let text = r#"{"version":1,"active":"o_gone0000","orgs":[{"id":"o_aaaaaaaa","url":"https://a.example.com/"},
            {"id":"o_bbbbbbbb","url":"https://b.example.com/"}]}"#;
        assert_eq!(
            parse(text).iter().map(|org| org.active).collect::<Vec<_>>(),
            [true, false]
        );
        for text in [
            "",
            "{not json",
            "[]",
            r#"{"version":2,"orgs":[{"id":"o_aaaaaaaa","url":"https://a.example.com/"}]}"#,
            r#"{"orgs":[{"id":"o_aaaaaaaa","url":"https://a.example.com/"}]}"#,
            r#"{"version":1,"orgs":{}}"#,
        ] {
            assert!(parse(text).is_empty(), "{text}");
        }
        let long = format!(
            r#"{{"version":1,"orgs":[{{"id":"o_aaaaaaaa","url":"https://a.example.com/","name":"{}"}}]}}"#,
            "x".repeat(300)
        );
        assert_eq!(parse(&long)[0].name.chars().count(), DETAIL_LIMIT);
    }

    #[test]
    fn a_missing_file_reads_as_none_and_a_change_is_read_again() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("orgs.json");
        let mut watch = Watch::new(Some(file.clone()));
        assert!(watch.orgs().is_none());
        std::fs::write(&file, "{not json").unwrap();
        assert_eq!(watch.orgs(), Some(&[][..]), "present but unreadable");
        std::fs::write(
            &file,
            r#"{"version":1,"orgs":[{"id":"o_aaaaaaaa","url":"https://acme.example.com/","name":"Acme"}]}"#,
        )
        .unwrap();
        assert_eq!(watch.orgs().unwrap()[0].name, "Acme");
        std::fs::remove_file(&file).unwrap();
        assert!(watch.orgs().is_none());
        assert!(Watch::new(None).orgs().is_none());
    }

    #[test]
    fn an_org_is_connected_by_its_address_its_runner_or_its_host() {
        let url = "https://acme.example.com";
        assert!(matches(url, &destination("x", "https://acme.example.com")));
        assert!(matches(
            url,
            &destination("x", "https://runner.acme.example.com")
        ));
        assert!(matches(
            url,
            &destination("acme.example.com", "https://elsewhere.example.net")
        ));
        assert!(!matches(
            url,
            &destination("tidy.example.com", "https://runner.tidy.example.com")
        ));
        assert!(!matches(
            url,
            &destination("x", "https://runner.acme.example.com.evil.test")
        ));
        assert!(matches(
            "http://localhost:8000",
            &destination("localhost", "http://localhost:8000")
        ));
        assert!(!matches("not an address", &destination("", "")));
    }

    #[test]
    fn desktop_sign_in_skips_connected_orgs_and_starts_with_the_open_one() {
        let orgs = [
            org("Acme", "https://acme.example.com", false),
            org("Tidy", "https://tidy.example.com", false),
            org("Lights", "https://lights.example.com", true),
            org("Other", "https://other.example.com", false),
        ];
        let names =
            |rows: Vec<DesktopOrg>| rows.into_iter().map(|row| row.name).collect::<Vec<_>>();
        assert_eq!(
            names(to_sign_in(&orgs, &[])),
            ["Lights", "Acme", "Tidy", "Other"]
        );
        let signed = [destination(
            "tidy.example.com",
            "https://runner.tidy.example.com",
        )];
        assert_eq!(
            names(to_sign_in(&orgs, &signed)),
            ["Lights", "Acme", "Other"]
        );
        let signed = [destination(
            "lights.example.com",
            "https://runner.lights.example.com",
        )];
        assert_eq!(
            names(to_sign_in(&orgs, &signed)),
            ["Acme", "Tidy", "Other"],
            "a connected open org is skipped"
        );
        assert!(to_sign_in(&[], &[]).is_empty());
    }

    #[test]
    fn the_approval_link_names_the_org_and_its_page_and_nothing_off_its_address() {
        let authorize = "https://acme.example.com/oauth/authorize?response_type=code&client_id=c1&redirect_uri=http%3A%2F%2F127.0.0.1%3A5123%2Fcallback&state=s1";
        let link = approval_link("https://acme.example.com/", authorize).unwrap();
        let parsed = Url::parse(&link).unwrap();
        assert_eq!((parsed.scheme(), parsed.host_str()), ("tico", Some("open")));
        let query: Vec<(String, String)> = parsed.query_pairs().into_owned().collect();
        assert_eq!(
            query,
            [
                ("org".into(), "https://acme.example.com".into()),
                (
                    "path".into(),
                    "oauth/authorize?response_type=code&client_id=c1&redirect_uri=http%3A%2F%2F127.0.0.1%3A5123%2Fcallback&state=s1".into()
                )
            ]
        );
        // A Tico whose approval page is on another address, or a look-alike host, goes to the browser.
        assert!(
            approval_link(
                "https://acme.example.com",
                "https://runner.acme.example.com/oauth/authorize?x=1"
            )
            .is_none()
        );
        assert!(
            approval_link(
                "https://acme.example.com",
                "https://acme.example.com.evil.test/oauth/authorize"
            )
            .is_none()
        );
        assert!(
            approval_link(
                "https://acme.example.com",
                "https://acme.example.com//evil.test/x"
            )
            .is_none()
        );
        assert!(approval_link("ftp://acme.example.com", authorize).is_none());
    }
}
