//! The organizations this app opens: the list in orgs.json, which one is open, and what is kept for each (its
//! address, the UUID of its own web data store, and the name and email its page reported). Data and storage only;
//! the windows that show an org are in windows.rs.
//!
//! The list is kept on macOS in a generic build with no HUB_URL. Elsewhere, and in builds with a fixed server, the
//! one server comes from the environment, the build or server.txt as before, and nothing is written here.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use url::Url;
use uuid::Uuid;

use crate::config;

pub const FILE_NAME: &str = "orgs.json";
const VERSION: u32 = 1;
/// Most characters kept for an org's name or email.
pub const DETAIL_LIMIT: usize = 200;
const SAVE_FAILED: &str =
    "Could not save the server address. Check free disk space and folder permissions.";

/// One organization: its server, the UUID of its own web data store (None is WebKit's default store, which an org
/// migrated from server.txt keeps, so its sign-in carries over), and the name and email its page reported.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Org {
    pub id: String,
    pub url: String,
    #[serde(default)]
    pub store: Option<String>,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub email: String,
    #[serde(default)]
    pub added_at: String,
    /// Added through Tico sign-in and not opened yet: its first page is the hub's own sign-in (`/auth/login`), which
    /// the session copied into its store carries straight through. Cleared once that page opens.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub sign_in: bool,
    /// Where its page last was in the web app (the hub's address and a route, `windows::place_of`), so its next page,
    /// after this run too, opens there. None opens it on its usual first page.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_url: Option<String>,
}

impl Org {
    /// The server address, if this app opens it.
    pub fn hub(&self) -> Option<Url> {
        config::configured_url(&self.url).ok()
    }

    /// The org's own web data store, if it has one.
    pub fn store_id(&self) -> Option<Uuid> {
        self.store
            .as_deref()
            .and_then(|store| Uuid::parse_str(store).ok())
    }

    /// The org's name, or its host when the name is not known.
    pub fn title(&self) -> String {
        if !self.name.is_empty() {
            return self.name.clone();
        }
        self.hub()
            .and_then(|hub| hub.host_str().map(str::to_string))
            .unwrap_or_default()
    }
}

/// What orgs.json holds.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Document {
    pub version: u32,
    #[serde(default)]
    pub active: Option<String>,
    #[serde(default)]
    pub orgs: Vec<Org>,
}

impl Default for Document {
    fn default() -> Self {
        Document {
            version: VERSION,
            active: None,
            orgs: Vec::new(),
        }
    }
}

impl Document {
    /// A list of one org for `hub`, on WebKit's default store.
    pub fn with_one(hub: &Url) -> Document {
        let org = new_org(hub, None, false, &HashSet::new());
        Document {
            version: VERSION,
            active: Some(org.id.clone()),
            orgs: vec![org],
        }
    }
}

fn new_org(hub: &Url, store: Option<Uuid>, sign_in: bool, taken: &HashSet<String>) -> Org {
    Org {
        id: new_id(taken),
        url: hub.to_string(),
        store: store.map(|store| store.to_string()),
        name: String::new(),
        email: String::new(),
        added_at: now(),
        sign_in,
        last_url: None,
    }
}

/// `o_` and eight lowercase hex digits, not in `taken`.
pub fn new_id(taken: &HashSet<String>) -> String {
    loop {
        let hex = Uuid::new_v4().simple().to_string();
        let id = format!("o_{}", &hex[..8]);
        if !taken.contains(&id) {
            return id;
        }
    }
}

fn valid_id(id: &str) -> bool {
    id.strip_prefix("o_").is_some_and(|hex| {
        hex.len() == 8 && hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    })
}

fn now() -> String {
    time::OffsetDateTime::now_utc()
        .format(&time::format_description::well_known::Rfc3339)
        .unwrap_or_default()
}

fn clip(text: &str) -> String {
    text.trim().chars().take(DETAIL_LIMIT).collect()
}

/// Reads orgs.json. None when it is not one this app understands: unreadable JSON or another version. An entry
/// that cannot be opened (a bad address, id or store) is dropped and the rest are kept; a missing or unknown active
/// org falls back to the first one.
pub fn parse(text: &str) -> Option<Document> {
    let mut doc: Document = serde_json::from_str(text).ok()?;
    if doc.version != VERSION {
        return None;
    }
    let mut seen = HashSet::new();
    doc.orgs.retain(|org| {
        valid_id(&org.id)
            && org.hub().is_some()
            && org
                .store
                .as_deref()
                .is_none_or(|store| Uuid::parse_str(store).is_ok())
            && seen.insert(org.id.clone())
    });
    for org in &mut doc.orgs {
        org.name = clip(&org.name);
        org.email = clip(&org.email);
    }
    if !doc
        .active
        .as_ref()
        .is_some_and(|id| doc.orgs.iter().any(|org| &org.id == id))
    {
        doc.active = doc.orgs.first().map(|org| org.id.clone());
    }
    Some(doc)
}

fn write(path: &Path, doc: &Document) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let text = serde_json::to_string_pretty(doc).map_err(std::io::Error::other)?;
    let temporary = path.with_file_name(format!("{FILE_NAME}.tmp"));
    std::fs::write(&temporary, text).and_then(|_| std::fs::rename(&temporary, path))
}

/// Whether this run keeps a list of orgs: macOS, a generic build, and no HUB_URL fixing the one server.
pub fn multi_org_build() -> bool {
    cfg!(target_os = "macos") && config::is_generic() && !env_hub()
}

pub fn env_hub() -> bool {
    std::env::var("HUB_URL").is_ok_and(|value| !value.trim().is_empty())
}

/// The orgs a run starts with. With `file` (orgs.json, when this run keeps the list), that file is read. A missing
/// one is made from `legacy`, the server the app used before (server.txt), and written; an unreadable one is set
/// aside as orgs.json.corrupt. Without `file`, `legacy` is the one server and nothing is written.
pub fn startup(
    file: Option<PathBuf>,
    legacy: impl FnOnce() -> Result<Option<Url>, String>,
) -> Result<Orgs, String> {
    let Some(path) = file else {
        let doc = legacy()?
            .map(|hub| Document::with_one(&hub))
            .unwrap_or_default();
        return Ok(Orgs::new(doc, None));
    };
    match std::fs::read_to_string(&path) {
        Ok(text) => match parse(&text) {
            Some(doc) => {
                return Ok(Orgs {
                    complete: true,
                    ..Orgs::new(doc, Some(path))
                })
            }
            None => {
                log::warn!("{FILE_NAME} is not readable; it is kept as {FILE_NAME}.corrupt");
                let _ = std::fs::rename(&path, path.with_file_name(format!("{FILE_NAME}.corrupt")));
            }
        },
        // A first run: no org was ever saved, so the list made below is the whole of it.
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let doc = legacy()?
                .map(|hub| Document::with_one(&hub))
                .unwrap_or_default();
            let orgs = Orgs {
                complete: true,
                ..Orgs::new(doc, Some(path))
            };
            if !orgs.orgs().is_empty() {
                let _ = orgs.write_to(&orgs.doc);
            }
            return Ok(orgs);
        }
        Err(error) => {
            // Not overwritten: the file may be there and only unreadable for now.
            log::warn!("{FILE_NAME} could not be read: {error}");
            let doc = legacy()?
                .map(|hub| Document::with_one(&hub))
                .unwrap_or_default();
            return Ok(Orgs::new(doc, None));
        }
    }
    let doc = legacy()?
        .map(|hub| Document::with_one(&hub))
        .unwrap_or_default();
    let orgs = Orgs::new(doc, Some(path));
    if !orgs.orgs().is_empty() {
        let _ = orgs.write_to(&orgs.doc);
    }
    Ok(orgs)
}

/// The orgs this run shows. `path` is orgs.json when this run keeps the list (see `startup`); None when the one server
/// is fixed and nothing is written.
pub struct Orgs {
    doc: Document,
    path: Option<PathBuf>,
    /// Every id handed out this run, removed orgs included: an id is never given to a second org.
    issued: HashSet<String>,
    /// Whether this is every org saved on this Mac: orgs.json was read, or there was none yet. Not so when the list
    /// was not read (a fixed server, HUB_URL) or could not be (unreadable, set aside as corrupt).
    // Read only by is_complete, which the macOS sign-in and the tests use; other builds keep the flag all the same.
    #[cfg_attr(not(any(target_os = "macos", test)), allow(dead_code))]
    complete: bool,
}

impl Orgs {
    pub fn new(doc: Document, path: Option<PathBuf>) -> Orgs {
        let issued = doc.orgs.iter().map(|org| org.id.clone()).collect();
        Orgs {
            doc,
            path,
            issued,
            complete: false,
        }
    }

    /// Whether this is every org saved on this Mac, so a data store none of them uses belongs to no org.
    #[cfg(any(target_os = "macos", test))]
    pub fn is_complete(&self) -> bool {
        self.complete
    }

    pub fn is_multi(&self) -> bool {
        self.path.is_some()
    }

    pub fn orgs(&self) -> &[Org] {
        &self.doc.orgs
    }

    pub fn active(&self) -> Option<&Org> {
        self.get(self.doc.active.as_deref()?)
    }

    pub fn get(&self, id: &str) -> Option<&Org> {
        self.doc.orgs.iter().find(|org| org.id == id)
    }

    /// The org whose server has this origin, if there is one.
    pub fn find_hub(&self, hub: &Url) -> Option<&Org> {
        self.doc.orgs.iter().find(|org| {
            org.hub()
                .is_some_and(|other| other.origin() == hub.origin())
        })
    }

    /// Adds an org for `hub`, on a store of its own, and makes it the open one. Nothing changes if the file cannot
    /// be written, so the caller can show the error. Only a list that is kept can add.
    pub fn add(&mut self, hub: &Url) -> Result<Org, String> {
        match self.add_all(&[(hub.clone(), Uuid::new_v4(), false)])?.pop() {
            Some(org) => Ok(org),
            None => self
                .find_hub(hub)
                .cloned()
                .ok_or_else(|| SAVE_FAILED.to_string()),
        }
    }

    /// Adds an org for each `(server, store, sign_in)`, on the store given (Tico sign-in puts the session in it first),
    /// in one write, and makes the first one added the open one. A server whose origin is already an org (or earlier in
    /// `hubs`) is left out. Returns the orgs added; nothing changes if none is, or if the file cannot be written.
    pub fn add_all(&mut self, hubs: &[(Url, Uuid, bool)]) -> Result<Vec<Org>, String> {
        if !self.is_multi() {
            return Err(SAVE_FAILED.to_string());
        }
        let (mut next, mut issued, mut added) = (self.doc.clone(), self.issued.clone(), Vec::new());
        let mut origins: HashSet<String> = self
            .doc
            .orgs
            .iter()
            .filter_map(Org::hub)
            .map(|hub| hub.origin().ascii_serialization())
            .collect();
        for (hub, store, sign_in) in hubs {
            if !origins.insert(hub.origin().ascii_serialization()) {
                continue;
            }
            let org = new_org(hub, Some(*store), *sign_in, &issued);
            issued.insert(org.id.clone());
            next.orgs.push(org.clone());
            added.push(org);
        }
        let Some(first) = added.first() else {
            return Ok(added);
        };
        next.active = Some(first.id.clone());
        self.write_to(&next)?;
        self.issued = issued;
        self.doc = next;
        Ok(added)
    }

    /// Makes `id` the open org. Applied even if the file cannot be written: the org is already open on screen.
    pub fn set_active(&mut self, id: &str) {
        if self.get(id).is_none() || self.doc.active.as_deref() == Some(id) {
            return;
        }
        self.doc.active = Some(id.to_string());
        let _ = self.write_to(&self.doc);
    }

    /// Whether the org's next page opens on the hub's sign-in, clearing it: only its first page after Tico sign-in
    /// does. Cleared even if the file cannot be written, so it opens there at most once this run.
    pub fn take_sign_in(&mut self, id: &str) -> bool {
        let Some(org) = self
            .doc
            .orgs
            .iter_mut()
            .find(|org| org.id == id && org.sign_in)
        else {
            return false;
        };
        org.sign_in = false;
        let _ = self.write_to(&self.doc);
        true
    }

    /// The org's next page opens on the hub's sign-in: Tico sign-in renewed its session.
    #[cfg(any(target_os = "macos", test))]
    pub fn mark_sign_in(&mut self, id: &str) {
        let Some(org) = self
            .doc
            .orgs
            .iter_mut()
            .find(|org| org.id == id && !org.sign_in)
        else {
            return;
        };
        org.sign_in = true;
        let _ = self.write_to(&self.doc);
    }

    /// Keeps where the org's page last was in the web app (None: its usual first page). Written only when it changed.
    pub fn set_last_url(&mut self, id: &str, place: Option<String>) {
        let Some(org) = self.doc.orgs.iter_mut().find(|org| org.id == id) else {
            return;
        };
        if org.last_url == place {
            return;
        }
        org.last_url = place;
        let _ = self.write_to(&self.doc);
    }

    /// Keeps the name and email an org's page reported, trimmed and capped.
    pub fn set_details(&mut self, id: &str, name: &str, email: &str) {
        let (name, email) = (clip(name), clip(email));
        let Some(org) = self.doc.orgs.iter_mut().find(|org| org.id == id) else {
            return;
        };
        if org.name == name && org.email == email {
            return;
        }
        org.name = name;
        org.email = email;
        let _ = self.write_to(&self.doc);
    }

    /// Removes an org; when it was the open one, the first one left becomes the open one. Applied even if the file
    /// cannot be written: the org's data is already gone. Only a list that is kept can remove.
    pub fn remove(&mut self, id: &str) -> Option<Org> {
        if !self.is_multi() {
            return None;
        }
        let index = self.doc.orgs.iter().position(|org| org.id == id)?;
        let org = self.doc.orgs.remove(index);
        if self.doc.active.as_deref() == Some(id) {
            self.doc.active = self.doc.orgs.first().map(|org| org.id.clone());
        }
        let _ = self.write_to(&self.doc);
        Some(org)
    }

    fn write_to(&self, doc: &Document) -> Result<(), String> {
        let Some(path) = &self.path else {
            return Ok(());
        };
        write(path, doc).map_err(|error| {
            log::warn!("{FILE_NAME} could not be saved: {error}");
            SAVE_FAILED.to_string()
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hub(address: &str) -> Url {
        config::validate_url(address).unwrap()
    }

    fn server(address: &str) -> impl FnOnce() -> Result<Option<Url>, String> + '_ {
        move || Ok(Some(hub(address)))
    }

    fn sample(id: &str, address: &str) -> Org {
        Org {
            id: id.into(),
            url: hub(address).to_string(),
            store: Some(Uuid::new_v4().to_string()),
            name: format!("Org {id}"),
            email: format!("{id}@acme.example"),
            added_at: "2026-11-03T18:00:00Z".into(),
            sign_in: false,
            last_url: None,
        }
    }

    /// A fresh folder for one test's orgs.json.
    fn folder(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("tico-orgs-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir.join(FILE_NAME)
    }

    #[test]
    fn an_orgs_last_place_is_saved_read_back_and_goes_with_the_org() {
        let path = folder("last-place");
        let doc = Document {
            version: VERSION,
            active: Some("o_aaaaaaaa".into()),
            orgs: vec![
                sample("o_aaaaaaaa", "https://acme.example.com"),
                sample("o_bbbbbbbb", "https://tidy.example.com"),
            ],
        };
        let mut orgs = Orgs::new(doc, Some(path.clone()));
        orgs.set_last_url(
            "o_bbbbbbbb",
            Some("https://tidy.example.com/#/threads/t_1".into()),
        );
        let saved = std::fs::read_to_string(&path).unwrap();
        let read = parse(&saved).unwrap();
        assert_eq!(
            read.orgs[1].last_url.as_deref(),
            Some("https://tidy.example.com/#/threads/t_1")
        );
        assert_eq!(read.orgs[0].last_url, None);
        assert_eq!(
            saved.matches("last_url").count(),
            1,
            "an org with no last place writes no field"
        );
        // A list written before the field existed still reads, with no last place.
        let older = r#"{"version": 1, "active": "o_aaaaaaaa", "orgs": [{"id": "o_aaaaaaaa", "url": "https://acme.example.com/"}]}"#;
        assert_eq!(parse(older).unwrap().orgs[0].last_url, None);
        // Removing the org drops its last place with it.
        orgs.remove("o_bbbbbbbb");
        assert!(!std::fs::read_to_string(&path)
            .unwrap()
            .contains("tidy.example.com"));
        orgs.set_last_url("o_bbbbbbbb", Some("https://tidy.example.com/#/x".into()));
        assert!(
            orgs.get("o_bbbbbbbb").is_none(),
            "a removed org gets no place back"
        );
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn a_first_run_migrates_server_txt_on_the_default_store_and_then_ignores_it() {
        let path = folder("migrate");
        let orgs = startup(Some(path.clone()), server("https://acme.example.com")).unwrap();
        let org = orgs.active().unwrap();
        assert_eq!(orgs.orgs().len(), 1);
        assert_eq!(
            org.store, None,
            "the migrated org keeps WebKit's default store and its sign-in"
        );
        assert_eq!(org.hub().unwrap().host_str(), Some("acme.example.com"));
        assert!(path.exists(), "the migrated list is written");
        let again = startup(Some(path.clone()), server("https://other.example.com")).unwrap();
        assert_eq!(
            again.active().unwrap().hub().unwrap().host_str(),
            Some("acme.example.com")
        );
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn a_corrupt_list_is_set_aside_and_the_old_server_still_opens() {
        let path = folder("corrupt");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, "{not json").unwrap();
        let orgs = startup(Some(path.clone()), server("https://acme.example.com")).unwrap();
        assert_eq!(orgs.orgs().len(), 1);
        let kept = path.with_file_name("orgs.json.corrupt");
        assert_eq!(std::fs::read_to_string(&kept).unwrap(), "{not json");
        assert!(
            parse(r#"{"version":2,"orgs":[]}"#).is_none(),
            "another version is not read"
        );
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn the_list_is_complete_only_when_orgs_json_was_read_or_never_written() {
        let path = folder("complete");
        let first = startup(Some(path.clone()), server("https://acme.example.com")).unwrap();
        assert!(
            first.is_complete(),
            "a first run: no orgs.json, so no org was saved"
        );
        assert!(
            startup(Some(path.clone()), server("https://other.example.com"))
                .unwrap()
                .is_complete(),
            "orgs.json read"
        );
        // HUB_URL or a built-in server: orgs.json is not read, so its orgs are unknown.
        assert!(!startup(None, server("https://override.example.com"))
            .unwrap()
            .is_complete());
        // An unreadable orgs.json is set aside: its orgs are unknown too.
        std::fs::write(&path, "{not json").unwrap();
        assert!(
            !startup(Some(path.clone()), server("https://acme.example.com"))
                .unwrap()
                .is_complete()
        );
        // A path that cannot be read as a file (here a folder) leaves the list unread.
        let _ = std::fs::remove_file(&path);
        std::fs::create_dir_all(&path).unwrap();
        assert!(
            !startup(Some(path.clone()), server("https://acme.example.com"))
                .unwrap()
                .is_complete()
        );
        assert!(!Orgs::new(Document::default(), Some(path.clone())).is_complete());
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn entries_that_cannot_be_opened_are_dropped_and_the_open_one_falls_back() {
        let text = r#"{"version":1,"active":"o_gone0000","orgs":[
            {"id":"o_aaaaaaaa","url":"ftp://acme.example.com/"},
            {"id":"o_bbbbbbbb","url":"https://tidy.example.com/","store":"not-a-uuid"},
            {"id":"nope","url":"https://other.example.com/"},
            {"id":"o_cccccccc","url":"https://tidy.example.com/"},
            {"id":"o_cccccccc","url":"https://dupe.example.com/"}]}"#;
        let doc = parse(text).unwrap();
        let ids: Vec<&str> = doc.orgs.iter().map(|org| org.id.as_str()).collect();
        assert_eq!(ids, ["o_cccccccc"]);
        assert_eq!(doc.active.as_deref(), Some("o_cccccccc"));
    }

    #[test]
    fn removing_the_open_org_opens_the_first_one_left() {
        let path = folder("remove");
        let doc = Document {
            version: VERSION,
            active: Some("o_bbbbbbbb".into()),
            orgs: vec![
                sample("o_aaaaaaaa", "https://acme.example.com"),
                sample("o_bbbbbbbb", "https://tidy.example.com"),
                sample("o_cccccccc", "https://third.example.com"),
            ],
        };
        let mut orgs = Orgs::new(doc, Some(path.clone()));
        assert_eq!(
            orgs.remove("o_bbbbbbbb").map(|org| org.id),
            Some("o_bbbbbbbb".to_string())
        );
        assert_eq!(orgs.active().map(|org| org.id.as_str()), Some("o_aaaaaaaa"));
        assert!(orgs.remove("o_cccccccc").is_some());
        assert_eq!(
            orgs.active().map(|org| org.id.as_str()),
            Some("o_aaaaaaaa"),
            "removing another org keeps the open one"
        );
        assert!(orgs.remove("o_aaaaaaaa").is_some());
        assert!(orgs.active().is_none() && orgs.orgs().is_empty());
        assert!(parse(&std::fs::read_to_string(&path).unwrap())
            .unwrap()
            .orgs
            .is_empty());
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn an_added_org_gets_its_own_store_and_is_written_before_it_opens() {
        let path = folder("add");
        let mut orgs = Orgs::new(Document::default(), Some(path.clone()));
        let first = orgs.add(&hub("https://acme.example.com")).unwrap();
        let second = orgs.add(&hub("https://tidy.example.com")).unwrap();
        assert_ne!(first.id, second.id);
        assert!(first.store_id().is_some() && second.store_id().is_some());
        assert_ne!(first.store, second.store);
        assert_eq!(
            orgs.active().map(|org| org.id.clone()),
            Some(second.id.clone())
        );
        let saved = parse(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(saved.orgs.len(), 2);
        assert_eq!(saved.active.as_deref(), Some(second.id.as_str()));
        assert!(
            orgs.find_hub(&hub("https://acme.example.com/tico/"))
                .is_some(),
            "the same origin is the same org"
        );
        assert!(
            Orgs::new(Document::default(), None)
                .add(&hub("https://acme.example.com"))
                .is_err(),
            "a fixed server cannot add"
        );
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn a_tico_sign_in_org_opens_on_the_hub_sign_in_once_even_after_a_restart() {
        let path = folder("sign-in-flag");
        let mut orgs = Orgs::new(Document::default(), Some(path.clone()));
        let by_address = orgs.add(&hub("https://kept.example.com")).unwrap();
        assert!(!by_address.sign_in);
        let added = orgs
            .add_all(&[(hub("https://acme.example.com"), Uuid::new_v4(), true)])
            .unwrap();
        assert!(added[0].sign_in);
        let text = std::fs::read_to_string(&path).unwrap();
        assert_eq!(
            text.matches("\"sign_in\"").count(),
            1,
            "only the flagged org writes it: {text}"
        );
        // A restart before the first open keeps the flag.
        let mut reread = Orgs::new(parse(&text).unwrap(), Some(path.clone()));
        assert!(!reread.take_sign_in(&by_address.id));
        assert!(reread.take_sign_in(&added[0].id));
        assert!(!reread.take_sign_in(&added[0].id), "only the first page");
        assert!(!std::fs::read_to_string(&path).unwrap().contains("sign_in"));
        // A renewed session flags an org already added, once more.
        reread.mark_sign_in(&by_address.id);
        assert!(std::fs::read_to_string(&path).unwrap().contains("sign_in"));
        assert!(reread.take_sign_in(&by_address.id) && !reread.take_sign_in(&by_address.id));
        // Files written before the flag existed read as not flagged.
        assert!(
            !parse(
                r#"{"version":1,"orgs":[{"id":"o_0000000a","url":"https://acme.example.com/"}]}"#
            )
            .unwrap()
            .orgs[0]
                .sign_in
        );
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn signed_in_picks_are_added_together_on_their_given_stores_and_the_first_opens() {
        let path = folder("add-all");
        let mut orgs = Orgs::new(Document::default(), Some(path.clone()));
        let kept = orgs.add(&hub("https://kept.example.com")).unwrap();
        let (a, b) = (Uuid::new_v4(), Uuid::new_v4());
        let added = orgs
            .add_all(&[
                (hub("https://acme.example.com"), a, false),
                (hub("https://tidy.example.com"), b, false),
            ])
            .unwrap();
        assert_eq!(
            added
                .iter()
                .map(|org| org.store_id().unwrap())
                .collect::<Vec<_>>(),
            [a, b]
        );
        assert_ne!(added[0].id, added[1].id);
        assert!(added.iter().all(|org| org.id != kept.id));
        assert_eq!(
            orgs.active().map(|org| org.id.clone()),
            Some(added[0].id.clone())
        );
        let saved = parse(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(saved.orgs.len(), 3);
        assert_eq!(saved.active.as_deref(), Some(added[0].id.as_str()));
        assert!(orgs.add_all(&[]).unwrap().is_empty());
        // A server that became an org meanwhile (added by address) is left out, and so is a repeat within the picks.
        let (c, d, e) = (Uuid::new_v4(), Uuid::new_v4(), Uuid::new_v4());
        let late = orgs
            .add_all(&[
                (hub("https://acme.example.com/other/"), c, false),
                (hub("https://new.example.com"), d, false),
                (hub("https://NEW.example.com/x/"), e, false),
            ])
            .unwrap();
        assert_eq!(late.len(), 1);
        assert_eq!(
            (late[0].store_id(), late[0].hub().unwrap().host_str()),
            (Some(d), Some("new.example.com"))
        );
        assert_eq!(orgs.orgs().len(), 4);
        assert!(
            orgs.add_all(&[(hub("https://tidy.example.com"), c, false)])
                .unwrap()
                .is_empty(),
            "nothing new, nothing written"
        );
        assert_eq!(
            orgs.active().map(|org| org.id.clone()),
            Some(late[0].id.clone())
        );
        assert_eq!(
            orgs.add(&hub("https://kept.example.com/")).unwrap().id,
            kept.id,
            "adding a known server gives that org"
        );
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn names_and_emails_are_trimmed_and_capped() {
        let doc = Document {
            version: VERSION,
            active: Some("o_aaaaaaaa".into()),
            orgs: vec![sample("o_aaaaaaaa", "https://acme.example.com")],
        };
        let mut orgs = Orgs::new(doc, None);
        orgs.set_details(
            "o_aaaaaaaa",
            &format!("  {}  ", "x".repeat(300)),
            " a@acme.example ",
        );
        let org = orgs.active().unwrap();
        assert_eq!(org.name.chars().count(), DETAIL_LIMIT);
        assert_eq!(org.email, "a@acme.example");
        assert_eq!(
            orgs.get("o_aaaaaaaa").unwrap().title().chars().count(),
            DETAIL_LIMIT
        );
    }
}
