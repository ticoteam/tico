//! Which roots a server certificate must chain to. The operating system's trust store comes first (the macOS
//! keychain, the Windows certificate store, the Linux CA bundle), so a root the OS vendor removes or revokes stops
//! working here with the next OS update, and a root the company installed (a TLS-inspection proxy) is trusted as a
//! browser trusts it. A computer with no usable store (a minimal Linux, a container without ca-certificates) uses
//! the bundled webpki-roots list rather than failing every connection.

use rustls::client::danger::ServerCertVerifier;
use rustls::client::WebPkiServerVerifier;
use rustls::crypto::CryptoProvider;
use rustls::{ClientConfig, RootCertStore};
use std::sync::{Arc, Once, OnceLock};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Source {
    System,
    Bundled,
}

impl Source {
    pub fn label(self) -> &'static str {
        match self {
            Source::System => "system trust store",
            Source::Bundled => "bundled trust list",
        }
    }
}

struct Trust {
    source: Source,
    /// reqwest takes a config as it is, ALPN included, so this one offers what reqwest's own would.
    http: ClientConfig,
    /// The screen websocket upgrades over HTTP/1.1, so it offers no ALPN.
    websocket: Arc<ClientConfig>,
}

fn provider() -> Arc<CryptoProvider> {
    Arc::new(rustls::crypto::ring::default_provider())
}

/// The platform verifier when the OS has a usable store, else WebPKI over the bundled list. On Linux the platform
/// verifier refuses to start with no system certificates; macOS and Windows always have a store.
fn choose(provider: &Arc<CryptoProvider>, system: Result<rustls_platform_verifier::Verifier, rustls::Error>) -> (Source, Arc<dyn ServerCertVerifier>) {
    match system {
        Ok(verifier) => (Source::System, Arc::new(verifier)),
        Err(_) => {
            let roots = RootCertStore { roots: webpki_roots::TLS_SERVER_ROOTS.to_vec() };
            let verifier = WebPkiServerVerifier::builder_with_provider(Arc::new(roots), provider.clone())
                .build()
                .expect("the bundled trust list is not empty");
            (Source::Bundled, verifier)
        }
    }
}

fn config(provider: &Arc<CryptoProvider>, verifier: Arc<dyn ServerCertVerifier>, alpn: &[&[u8]]) -> ClientConfig {
    let mut config = ClientConfig::builder_with_provider(provider.clone())
        .with_safe_default_protocol_versions()
        .expect("ring supports the default TLS versions")
        .dangerous()
        .with_custom_certificate_verifier(verifier)
        .with_no_client_auth();
    config.alpn_protocols = alpn.iter().map(|p| p.to_vec()).collect();
    config
}

fn trust() -> &'static Trust {
    static TRUST: OnceLock<Trust> = OnceLock::new();
    TRUST.get_or_init(|| {
        let provider = provider();
        let (source, verifier) = choose(&provider, rustls_platform_verifier::Verifier::new(provider.clone()));
        Trust {
            source,
            http: config(&provider, verifier.clone(), &[b"h2", b"http/1.1"]),
            websocket: Arc::new(config(&provider, verifier, &[])),
        }
    })
}

/// A reqwest builder that verifies with the trust chosen above. Every runner HTTP client starts here.
pub fn http_builder() -> reqwest::ClientBuilder {
    reqwest::Client::builder().use_preconfigured_tls(trust().http.clone())
}

pub fn websocket_connector() -> tokio_tungstenite::Connector {
    tokio_tungstenite::Connector::Rustls(trust().websocket.clone())
}

/// Says once per process which roots verify certificates, so support can tell from the runner log.
pub fn announce(log: impl FnOnce(&str)) {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| log(&format!("Tico runner: TLS certificates verified with the {}", trust().source.label())));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_system_store_falls_back_to_the_bundled_list() {
        let provider = provider();
        let (source, verifier) = choose(&provider, Err(rustls::Error::General("No CA certificates were loaded from the system".into())));
        assert_eq!(source, Source::Bundled);
        // A usable verifier, not a refusal of everything: it builds a client config and a client.
        let built = config(&provider, verifier, &[b"http/1.1"]);
        assert!(reqwest::Client::builder().use_preconfigured_tls(built).build().is_ok());
    }

    #[test]
    fn the_source_is_logged_once() {
        let mut lines = Vec::new();
        announce(|l| lines.push(l.to_string()));
        announce(|l| lines.push(l.to_string()));
        assert_eq!(lines.len(), 1);
        assert!(lines[0].ends_with("system trust store") || lines[0].ends_with("bundled trust list"), "{}", lines[0]);
    }
}
