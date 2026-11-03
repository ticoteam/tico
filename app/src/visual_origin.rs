//! A navigation allowance, never native IPC authority. The server still authenticates every visual token.
use url::{Host, Url};

#[derive(Clone, Debug)]
pub struct VisualOrigin {
    hub: Url,
    origin: Option<Url>,
    /// Tico file hosting's address, which shows pages and SVG when the server has no visual origin of its own.
    files: Option<Url>,
}

impl VisualOrigin {
    pub fn new(hub: &Url, raw: &str) -> Option<Self> {
        Self::with_files(hub, raw, "")
    }

    /// The visual origin `raw` and Tico file hosting's origin `files`: either may be empty, not both, and an
    /// invalid one refuses the whole allowance.
    pub fn with_files(hub: &Url, raw: &str, files: &str) -> Option<Self> {
        let origin = if raw.is_empty() { None } else { Some(separate_origin(hub, raw)?) };
        let files = if files.is_empty() { None } else { Some(separate_origin(hub, files)?) };
        if origin.is_none() && files.is_none() {
            return None;
        }
        Some(Self { hub: hub.clone(), origin, files })
    }

    pub fn permits(&self, hub: Option<&Url>, url: &Url) -> bool {
        hub == Some(&self.hub)
            && (is_visual_navigation(self.origin.as_ref(), url) || is_files_visual_navigation(self.files.as_ref(), url))
    }
}

fn separate_origin(hub: &Url, raw: &str) -> Option<Url> {
    // Inspect the origin spelling before URL parsing can remove credentials, dot paths or whitespace.
    let (scheme, authority) = raw.split_once("://")?;
    let authority = authority.strip_suffix('/').unwrap_or(authority);
    if !scheme.eq_ignore_ascii_case("https") || authority.is_empty()
        || authority.chars().any(|c| c.is_whitespace() || c.is_control() || matches!(c, '/' | '\\' | '@' | '%' | '?' | '#')) {
        return None;
    }
    let origin = Url::parse(raw).ok()?;
    if !https_origin(&origin) || origin.origin() == hub.origin() {
        return None;
    }
    Some(origin)
}

fn https_origin(url: &Url) -> bool {
    url.scheme() == "https" && url.username().is_empty() && url.password().is_none()
        && url.query().is_none() && url.fragment().is_none() && url.path() == "/" && url.port() != Some(0)
        && match url.host() {
            Some(Host::Domain(host)) => host.len() <= 253 && host.split('.').all(|label| {
                !label.is_empty() && label.len() <= 63 && !label.starts_with('-') && !label.ends_with('-')
                    && label.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')
            }),
            Some(Host::Ipv4(_) | Host::Ipv6(_)) => true,
            None => false,
        }
}

/// Only the canonical parsed URL is available from WebView navigation callbacks. The token path has
/// no escapes or additional segments. Its spelling matches backend/visuals.py: unpadded base64url
/// payload, a dot, then an unpadded SHA256 MAC. This checks syntax, not the MAC or the reader's access.
pub fn is_visual_navigation(allowed: Option<&Url>, url: &Url) -> bool {
    let Some(allowed) = allowed.filter(|origin| https_origin(origin)) else { return false };
    if url.scheme() != "https" || url.origin() != allowed.origin() || !url.username().is_empty()
        || url.password().is_some() || url.query().is_some() || url.fragment().is_some() {
        return false;
    }
    let Some(token) = url.path().strip_prefix("/visuals/") else { return false };
    let Some((body, mac)) = token.split_once('.') else { return false };
    let base64url = |value: &str| !value.is_empty()
        && value.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_'));
    base64url(body) && mac.len() == 43 && base64url(mac)
}

/// Tico file hosting's signed link to the cleaned copy of a page or SVG (backend/tico_storage.py `_signed`, checked
/// by infra/files-edge/request.js): `/visuals/<2 hex>/<64 hex>` whose name starts with its folder, and a query of
/// exactly `e`, `k` and `s`. The server's own `/visuals/<token>` route sends a frame here after checking the reader.
/// This checks syntax, not the signature, which the edge checks.
pub fn is_files_visual_navigation(allowed: Option<&Url>, url: &Url) -> bool {
    let Some(allowed) = allowed.filter(|origin| https_origin(origin)) else { return false };
    if url.scheme() != "https" || url.origin() != allowed.origin() || !url.username().is_empty()
        || url.password().is_some() || url.fragment().is_some() {
        return false;
    }
    let Some((folder, name)) = url.path().strip_prefix("/visuals/").and_then(|rest| rest.split_once('/')) else { return false };
    if folder.len() != 2 || name.len() != 64 || !name.starts_with(folder)
        || !name.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f')) {
        return false;
    }
    let base64url = |c: u8| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_');
    let mut seen = [false; 3];
    for pair in url.query().unwrap_or("").split('&') {
        let Some((key, value)) = pair.split_once('=') else { return false };
        let (slot, valid) = match key {
            "e" => (0, (1..=12).contains(&value.len()) && value.bytes().all(|c| c.is_ascii_digit())),
            "k" => (1, (1..=32).contains(&value.len()) && value.bytes().all(base64url)),
            "s" => (2, value.len() == 43 && value.bytes().all(base64url)),
            _ => return false,
        };
        if !valid || seen[slot] {
            return false;
        }
        seen[slot] = true;
    }
    seen == [true; 3]
}

#[cfg(test)]
mod tests {
    use super::*;

    const HUB: &str = "https://team.example.com/tico/";
    const ASSETS: &str = "https://team.files.example.com";
    // The same URL-safe encoding and SHA256 MAC length used by the server; not an authenticated token.
    pub const TOKEN: &str = "WyJodW1hbjphIiwiZjEiLDEsIiIsMjAwMDAwMDAwMF0.ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn-_0";

    #[test]
    fn origin_updates_accept_only_a_separate_https_origin() {
        let hub = Url::parse(HUB).unwrap();
        for origin in [ASSETS, "https://TEAM.files.example.com:443/", "https://[::1]:8443", "https://127.0.0.1:8443"] {
            assert!(VisualOrigin::new(&hub, origin).is_some(), "{origin}");
        }
        for origin in ["", HUB, "https://team.example.com", "http://team.files.example.com", "https://@team.files.example.com",
                       "https://user:pass@team.files.example.com", "https://team.files.example.com/visuals", "https://team.files.example.com/..",
                       "https://team.files.example.com?", "https://team.files.example.com#", "https://team.files.example.com\\",
                       " https://team.files.example.com", "https://team.files.example.com\n", "https://team%2efiles.example.com",
                       "https://*.files.example.com", "https://bad_host.example.com", "https://team..example.com", "https://team.files.example.com:0"] {
            assert!(VisualOrigin::new(&hub, origin).is_none(), "{origin}");
        }
    }

    #[test]
    fn only_one_exact_origin_and_token_route_stay() {
        let hub = Url::parse(HUB).unwrap();
        let allowance = VisualOrigin::new(&hub, ASSETS).unwrap();
        let valid = Url::parse(&format!("{ASSETS}/visuals/{TOKEN}")).unwrap();
        assert!(allowance.permits(Some(&hub), &valid));
        assert!(!allowance.permits(None, &valid));
        assert!(!allowance.permits(Some(&Url::parse("https://other.example.com/").unwrap()), &valid));
        assert!(!is_visual_navigation(None, &valid));
        for address in [
            format!("https://team.files.example.com.evil.example/visuals/{TOKEN}"),
            format!("https://other.files.example.com/visuals/{TOKEN}"),
            format!("https://team.files.example.com:8443/visuals/{TOKEN}"),
            format!("http://team.files.example.com/visuals/{TOKEN}"),
            format!("https://user@team.files.example.com/visuals/{TOKEN}"),
            format!("{ASSETS}/visuals/{TOKEN}?download=1"),
            format!("{ASSETS}/visuals/{TOKEN}#part"),
            format!("{ASSETS}/other/{TOKEN}"),
            format!("{ASSETS}/visuals/{TOKEN}/other"),
            format!("{ASSETS}/visuals/{TOKEN}.extra"),
            format!("{ASSETS}/visuals/{TOKEN}%2fmore"),
            format!("{ASSETS}/visuals/%2e%2e/private/{TOKEN}"),
            format!("{ASSETS}/visuals/../private/{TOKEN}"),
            format!("{ASSETS}/visuals/%57yJ.{TOKEN}"),
            format!("{ASSETS}/visuals/{TOKEN}="),
            format!("{ASSETS}/visuals/.{TOKEN}"),
            format!("blob:{ASSETS}/visuals/{TOKEN}"),
            "about:blank".into(), "data:text/html,hello".into(),
        ] {
            assert!(!allowance.permits(Some(&hub), &Url::parse(&address).unwrap()), "{address}");
        }
    }

    const FILES: &str = "https://acme-files.ticofiles.com";
    const NAME: &str = "ab0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcd";
    const SIG: &str = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn-_0";

    #[test]
    fn file_hosting_allows_only_its_signed_visual_links() {
        let hub = Url::parse(HUB).unwrap();
        let link = format!("{FILES}/visuals/ab/{NAME}?e=1793250000&k=k1&s={SIG}");
        let hosted = VisualOrigin::with_files(&hub, "", FILES).unwrap();
        assert!(hosted.permits(Some(&hub), &Url::parse(&link).unwrap()));
        assert!(hosted.permits(Some(&hub), &Url::parse(&format!("{FILES}/visuals/ab/{NAME}?s={SIG}&k=k1&e=1")).unwrap()));
        assert!(!hosted.permits(Some(&Url::parse("https://other.example.com/").unwrap()), &Url::parse(&link).unwrap()));
        // The visual origin's token route is not the files address's, nor the other way round.
        assert!(!hosted.permits(Some(&hub), &Url::parse(&format!("{FILES}/visuals/{TOKEN}")).unwrap()));
        let both = VisualOrigin::with_files(&hub, ASSETS, FILES).unwrap();
        assert!(both.permits(Some(&hub), &Url::parse(&format!("{ASSETS}/visuals/{TOKEN}")).unwrap()));
        assert!(both.permits(Some(&hub), &Url::parse(&link).unwrap()));
        assert!(!VisualOrigin::new(&hub, ASSETS).unwrap().permits(Some(&hub), &Url::parse(&link).unwrap()));
        for address in [
            format!("{FILES}/blobs/ab/{NAME}?e=1793250000&k=k1&s={SIG}"),
            format!("{FILES}/visuals/ac/{NAME}?e=1793250000&k=k1&s={SIG}"),
            format!("{FILES}/visuals/AB/{}?e=1793250000&k=k1&s={SIG}", NAME.to_uppercase()),
            format!("{FILES}/visuals/ab/{NAME}0?e=1793250000&k=k1&s={SIG}"),
            format!("{FILES}/visuals/ab/{NAME}/x?e=1793250000&k=k1&s={SIG}"),
            format!("{FILES}/visuals/ab/{NAME}"),
            format!("{FILES}/visuals/ab/{NAME}?e=1793250000&k=k1"),
            format!("{FILES}/visuals/ab/{NAME}?e=1793250000&k=k1&s={SIG}&download=1"),
            format!("{FILES}/visuals/ab/{NAME}?e=1793250000&e=1&k=k1&s={SIG}"),
            format!("{FILES}/visuals/ab/{NAME}?e=soon&k=k1&s={SIG}"),
            format!("{FILES}/visuals/ab/{NAME}?e=1793250000&k=k1&s={SIG}#x"),
            format!("{FILES}/visuals/ab/%61b{}?e=1793250000&k=k1&s={SIG}", &NAME[2..]),
            format!("https://user@acme-files.ticofiles.com/visuals/ab/{NAME}?e=1793250000&k=k1&s={SIG}"),
            format!("https://other-files.ticofiles.com/visuals/ab/{NAME}?e=1793250000&k=k1&s={SIG}"),
            format!("http://acme-files.ticofiles.com/visuals/ab/{NAME}?e=1793250000&k=k1&s={SIG}"),
            format!("https://acme-files.ticofiles.com:8443/visuals/ab/{NAME}?e=1793250000&k=k1&s={SIG}"),
        ] {
            assert!(!hosted.permits(Some(&hub), &Url::parse(&address).unwrap()), "{address}");
        }
        assert!(VisualOrigin::with_files(&hub, "", "").is_none());
        assert!(VisualOrigin::with_files(&hub, "", "http://acme-files.ticofiles.com").is_none());
        assert!(VisualOrigin::with_files(&hub, ASSETS, "https://acme-files.ticofiles.com/x").is_none(), "one bad value refuses both");
        assert!(VisualOrigin::with_files(&hub, "", "https://team.example.com").is_none(), "never the hub itself");
    }
}
