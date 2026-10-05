//! Server selection: process environment, built-in URL, saved address, then local setup.
//! The company identity is read once at start: build-time values (scripts/app.sh and the Windows and Linux company
//! builds) win, then a bundled `company.json` that CI writes into the generic build to rebrand it, then generic Tico.
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use url::Url;

pub const GENERIC_UPDATER: &str = "https://github.com/ticoteam/tico/releases/latest/download/latest.json";
pub const COMPANY_FILE: &str = "company.json";

#[derive(Clone, Debug)]
pub struct Config {
    pub hub: Url,
    pub local_token_file: Option<String>,
}

/// Company identity. Empty fields mean generic Tico.
#[derive(Clone, Debug, Default, serde::Deserialize)]
#[serde(default)]
pub struct Company {
    pub name: String,
    pub hub_url: String,
    pub slug: String,
    pub tray_label: String,
    /// Bundle identifier: app data folders and single-instance locking follow it.
    pub identifier: String,
    /// Updater feed; empty means the hub's /download/latest.json.
    pub updater: String,
}

impl Company {
    fn baked() -> Company {
        let get = |v: Option<&'static str>| v.unwrap_or("").trim().to_string();
        Company { name: get(option_env!("TICO_APP_NAME")), hub_url: get(option_env!("TICO_HUB_URL")),
                  slug: get(option_env!("TICO_ENV_SLUG")), tray_label: get(option_env!("TICO_TRAY_LABEL")),
                  ..Company::default() }
    }

    /// All or nothing: an invalid file leaves the generic app rather than half a company.
    pub fn parse(raw: &str) -> Result<Company, String> {
        let company: Company = serde_json::from_str(raw).map_err(|e| format!("company.json: {e}"))?;
        if company.name.trim().is_empty() || company.slug.is_empty() {
            return Err("company.json needs name and slug".into());
        }
        if !company.slug.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-') {
            return Err("company.json slug must be lowercase letters, digits and dashes".into());
        }
        if company.tray_label.len() > 4 || !company.tray_label.bytes().all(|c| c.is_ascii_alphanumeric()) {
            return Err("company.json tray_label must be empty or 1-4 ASCII letters/digits".into());
        }
        configured_url(&company.hub_url)?;
        if !company.updater.is_empty() { configured_url(&company.updater)?; }
        Ok(company)
    }

    fn load() -> Company {
        let baked = Company::baked();
        if !baked.hub_url.is_empty() || !baked.slug.is_empty() { return baked; }
        let Ok(exe) = std::env::current_exe() else { return baked };
        for path in company_files(&exe) {
            let Ok(raw) = std::fs::read_to_string(&path) else { continue };
            return Company::parse(&raw).unwrap_or_else(|e| { log::warn!("{e}"); baked });
        }
        baked
    }
}

/// Where a rebrand puts company.json, relative to the running executable: macOS Contents/Resources, beside the
/// Windows executable, and usr/lib/<binary>/ on Linux (Tauri's resource folder for deb and AppImage).
pub fn company_files(exe: &Path) -> Vec<PathBuf> {
    let Some(dir) = exe.parent() else { return vec![] };
    let mut paths = vec![dir.join("../Resources").join(COMPANY_FILE), dir.join(COMPANY_FILE)];
    if let Some(stem) = exe.file_stem() {
        paths.push(dir.join("../lib").join(stem).join(COMPANY_FILE));
    }
    paths
}

pub fn company() -> &'static Company {
    static COMPANY: OnceLock<Company> = OnceLock::new();
    COMPANY.get_or_init(Company::load)
}

/// A rebranded build still carries the generic tauri.conf.json: give the runtime the company's identifier
/// (app data folder, single-instance lock), name and updater feed.
pub fn apply_company(config: &mut tauri::Config) {
    let company = company();
    if company.hub_url.is_empty() { return; }
    if !company.identifier.is_empty() { config.identifier = company.identifier.clone(); }
    config.product_name = Some(company.name.clone());
    if !company.updater.is_empty() {
        let updater = config.plugins.0.entry("updater".to_string()).or_insert_with(|| serde_json::json!({}));
        if let Some(updater) = updater.as_object_mut() {
            updater.insert("endpoints".into(), serde_json::json!([company.updater]));
        }
    }
}

pub fn app_name() -> &'static str {
    Some(company().name.as_str()).filter(|s| !s.is_empty()).unwrap_or("Tico")
}

pub fn is_generic() -> bool {
    company().hub_url.is_empty()
}

pub fn env_slug() -> &'static str {
    &company().slug
}

pub fn tray_label() -> &'static str {
    &company().tray_label
}

pub fn resolve(env: Option<&str>, baked: Option<&str>, saved: Option<&str>) -> Result<Option<Url>, String> {
    env.into_iter().chain(baked).chain(saved).find(|s| !s.trim().is_empty()).map(configured_url).transpose()
}

pub fn validate_url(raw: &str) -> Result<Url, String> {
    let hub = configured_url(raw)?;
    let local = matches!(hub.host_str(), Some("localhost") | Some("127.0.0.1"));
    if hub.scheme() == "http" && !local {
        return Err("Use HTTPS (HTTP is allowed only for localhost or 127.0.0.1).".into());
    }
    Ok(hub)
}

fn configured_url(raw: &str) -> Result<Url, String> {
    let mut hub = Url::parse(raw.trim()).map_err(|_| "Enter a full server address, such as https://tico.example.com.")?;
    if hub.host_str().is_none() || !matches!(hub.scheme(), "https" | "http") {
        return Err("Use an HTTP or HTTPS server address.".into());
    }
    if !hub.username().is_empty() || hub.password().is_some() || hub.query().is_some() || hub.fragment().is_some() {
        return Err("Use a server address without a login, query or fragment.".into());
    }
    if !hub.path().ends_with('/') {
        hub.set_path(&format!("{}/", hub.path()));
    }
    Ok(hub)
}

pub async fn validate_server(raw: &str) -> Result<Url, String> {
    let hub = validate_url(raw)?;
    // No credentials, redirects or cookies: a health check must not leave the chosen server.
    let http = reqwest::Client::builder().timeout(std::time::Duration::from_secs(8))
        .redirect(reqwest::redirect::Policy::none()).no_proxy().build().map_err(|_| "Could not connect. Try again.")?;
    let response = http.get(hub.join("healthz").unwrap()).send().await
        .map_err(|_| "Could not reach the server. Check the address and try again.")?;
    if !response.status().is_success() {
        return Err("The server did not answer its health check. Check the address.".into());
    }
    let body: serde_json::Value = response.json().await.map_err(|_| "This address did not answer as a Tico server.")?;
    if body.get("service").and_then(serde_json::Value::as_str) != Some("tico") {
        return Err("This address did not answer as a Tico server.".into());
    }
    Ok(hub)
}

impl Config {
    pub fn load(path: &Path) -> Result<Option<Config>, String> {
        let env = std::env::var("HUB_URL").ok();
        let saved = std::fs::read_to_string(path).ok();
        Self::load_selected(path, env.as_deref(), Some(company().hub_url.as_str()), saved.as_deref())
    }

    fn load_selected(path: &Path, env: Option<&str>, baked: Option<&str>, saved: Option<&str>) -> Result<Option<Config>, String> {
        let hub = resolve(env, baked, saved)?;
        if saved.is_none() && env.filter(|s| !s.trim().is_empty()).is_none()
            && baked.filter(|s| !s.trim().is_empty()).is_some() {
            if let Some(hub) = &hub {
                std::fs::create_dir_all(path.parent().unwrap())
                    .and_then(|_| std::fs::write(path, hub.as_str()))
                    .map_err(|_| "Could not save the server address. Check free disk space and folder permissions.")?;
            }
        }
        Ok(hub.map(Self::new))
    }

    pub fn new(hub: Url) -> Config {
        Config { hub, local_token_file: option_env!("TICO_LOCAL_TOKEN_FILE")
            .map(str::to_string).filter(|s| !s.is_empty()) }
    }

    pub fn is_local(&self) -> bool {
        matches!(self.hub.host_str(), Some("localhost") | Some("127.0.0.1"))
    }

    pub fn host(&self) -> String {
        self.hub.host_str().unwrap_or("").to_string()
    }

    pub fn api(&self, path: &str) -> Url {
        self.hub.join(&format!("api/{}", path.trim_start_matches('/'))).unwrap()
    }

    /// Read an optional local owner token on each load so rotations take effect without a rebuild.
    pub fn start_url(&self) -> Url {
        if !self.is_local() { return self.hub.clone(); }
        let Some(file) = &self.local_token_file else { return self.hub.clone() };
        let Ok(contents) = std::fs::read_to_string(file) else { return self.hub.clone() };
        let token = contents.trim();
        if token.is_empty() { return self.hub.clone(); }
        let mut url = self.hub.join("api/v2/local-signin").unwrap();
        url.query_pairs_mut().append_pair("token", token).append_pair("next", "/");
        url
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn company_file_rebrands_or_stays_generic() {
        let c = Company::parse(r#"{"name":"Acme","hub_url":"https://acme.example.com","slug":"acme",
            "identifier":"team.tico.env.x","updater":"https://acme.example.com/download/latest.json"}"#).unwrap();
        assert_eq!((c.name.as_str(), c.slug.as_str()), ("Acme", "acme"));
        for bad in [r#"{"name":"Acme","slug":"acme","hub_url":"nope"}"#, r#"{"name":"","slug":"a","hub_url":"https://a.b"}"#,
                    r#"{"name":"A","slug":"A/..","hub_url":"https://a.b"}"#, "not json"] {
            assert!(Company::parse(bad).is_err(), "{bad}");
        }
        assert!(company_files(Path::new("/A.app/Contents/MacOS/tico"))
            .contains(&PathBuf::from("/A.app/Contents/MacOS/../Resources/company.json")));
        assert!(company_files(Path::new("/usr/bin/tico-x")).contains(&PathBuf::from("/usr/bin/../lib/tico-x/company.json")));
    }

    #[test]
    fn resolution_order_and_first_run() {
        let env = "https://env.example.com";
        let baked = "https://baked.example.com";
        let saved = "https://saved.example.com";
        assert_eq!(resolve(Some(env), Some(baked), Some(saved)).unwrap().unwrap().host_str(), Some("env.example.com"));
        assert_eq!(resolve(Some(" "), Some(baked), Some(saved)).unwrap().unwrap().host_str(), Some("baked.example.com"));
        assert_eq!(resolve(None, Some(""), Some(saved)).unwrap().unwrap().host_str(), Some("saved.example.com"));
        assert!(resolve(None, None, None).unwrap().is_none());
        assert!(resolve(Some("invalid"), Some(baked), Some(saved)).is_err());
    }

    #[test]
    fn built_in_server_survives_a_later_generic_build() {
        let directory = std::env::temp_dir().join(format!("tico-config-{}", std::process::id()));
        let path = directory.join("server.txt");
        let _ = std::fs::remove_dir_all(&directory);
        Config::load_selected(&path, None, Some("https://team.example.com"), None).unwrap();
        let saved = std::fs::read_to_string(&path).unwrap();
        assert_eq!(Config::load_selected(&path, None, None, Some(&saved)).unwrap().unwrap().host(), "team.example.com");
        Config::load_selected(&path, None, Some("https://other.example.com"), Some(&saved)).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), saved);
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn configured_http_servers_keep_working() {
        for values in [(Some("http://team.example.com"), None, None),
                       (None, Some("http://team.example.com"), None),
                       (None, None, Some("http://team.example.com"))] {
            assert_eq!(resolve(values.0, values.1, values.2).unwrap().unwrap().as_str(), "http://team.example.com/");
        }
    }

    #[test]
    fn only_secure_or_loopback_addresses() {
        for raw in ["https://tico.example.com", "http://localhost:8765", "http://127.0.0.1:8765/"] {
            assert!(validate_url(raw).is_ok(), "{raw}");
        }
        for raw in ["http://tico.example.com", "http://localhost.example.com", "ftp://localhost", "file:///tmp/a",
                    "https://ana:secret@example.com", "https://example.com?token=x", "https://example.com/#x", "example.com"] {
            assert!(validate_url(raw).is_err(), "{raw}");
        }
        assert_eq!(validate_url(" https://example.com/tico ").unwrap().as_str(), "https://example.com/tico/");
    }
}

#[cfg(test)]
mod health_tests {
    use super::*;
    use std::io::{Read, Write};

    async fn check(status: &str, body: &str) -> Result<Url, String> {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = format!("http://{}", listener.local_addr().unwrap());
        let response = format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(std::time::Duration::from_secs(3))).unwrap();
            let mut request = [0; 4096];
            let count = stream.read(&mut request).unwrap();
            assert!(String::from_utf8_lossy(&request[..count]).starts_with("GET /healthz HTTP/1.1"));
            stream.write_all(response.as_bytes()).unwrap();
        });
        let result = validate_server(&address).await;
        server.join().unwrap();
        result
    }

    #[tokio::test]
    async fn connect_requires_a_successful_tico_health_response() {
        assert!(check("200 OK", r#"{"service":"tico"}"#).await.is_ok());
        assert!(check("200 OK", r#"{"service":"other"}"#).await.is_err());
        assert!(check("200 OK", "invalid").await.is_err());
        assert!(check("503 Unavailable", r#"{"service":"tico"}"#).await.is_err());
        assert!(check("302 Found", "{}").await.is_err());
    }
}
