//! A navigation allowance, never native IPC authority. The server still authenticates every visual token.
use url::{Host, Url};

#[derive(Clone, Debug)]
pub struct VisualOrigin {
    hub: Url,
    origin: Url,
}

impl VisualOrigin {
    pub fn new(hub: &Url, raw: &str) -> Option<Self> {
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
        Some(Self { hub: hub.clone(), origin })
    }

    pub fn permits(&self, hub: Option<&Url>, url: &Url) -> bool {
        hub == Some(&self.hub) && is_visual_navigation(Some(&self.origin), url)
    }
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
}
