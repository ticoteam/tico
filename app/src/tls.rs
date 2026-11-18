//! TLS for the app's own HTTP clients (the hub badge, the server check, the sign-in probe): ring, with server
//! certificates verified against the bundled webpki-roots list, as reqwest 0.12's `rustls-tls` did. reqwest is built
//! without a crypto provider (`rustls-no-provider`), so aws-lc-rs is never compiled in. The updater plugin's client is
//! the one exception: it verifies with the OS trust store (rustls-platform-verifier) under the process-wide ring
//! provider installed here, which is what the plugin installs itself when nothing has.

use rustls::crypto::CryptoProvider;
use rustls::{ClientConfig, RootCertStore};
use std::sync::{Arc, OnceLock};

fn provider() -> Arc<CryptoProvider> {
    Arc::new(rustls::crypto::ring::default_provider())
}

/// Makes ring the process default, so a reqwest client built without [`http_builder`] (the updater's) has a provider
/// rather than panicking. Harmless when one is already installed.
pub fn install_default_provider() {
    let _ = rustls::crypto::ring::default_provider().install_default();
}

fn config() -> &'static ClientConfig {
    static CONFIG: OnceLock<ClientConfig> = OnceLock::new();
    CONFIG.get_or_init(|| {
        let roots = RootCertStore {
            roots: webpki_roots::TLS_SERVER_ROOTS.to_vec(),
        };
        let mut config = ClientConfig::builder_with_provider(provider())
            .with_safe_default_protocol_versions()
            .expect("ring supports the default TLS versions")
            .with_root_certificates(roots)
            .with_no_client_auth();
        // The app's reqwest is built without HTTP/2, so offer what reqwest's own config would: HTTP/1.1 only.
        config.alpn_protocols = vec![b"http/1.1".to_vec()];
        config
    })
}

/// A reqwest builder that verifies with the bundled roots above. Every app HTTP client starts here.
pub fn http_builder() -> reqwest::ClientBuilder {
    reqwest::Client::builder().tls_backend_preconfigured(config().clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_a_client_without_a_process_provider() {
        assert!(http_builder().build().is_ok());
        assert_eq!(config().alpn_protocols, vec![b"http/1.1".to_vec()]);
    }
}
