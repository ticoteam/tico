use super::*;
use rustls::pki_types::{pem::PemObject, CertificateDer, PrivateKeyDer};
use std::sync::Arc;
use tokio::sync::{mpsc, oneshot};

const SECRET: &str = "SYNTHETIC-ONLY-ON-THE-EXIT";
const ACCOUNT: &str = "synthetic-saved-account";
const TICKET: &str = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
const GRANT: &str = r#"{"tunnel_id":"tunnel","ticket":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}"#;

struct Seen {
    head: String,
    finish: oneshot::Sender<bool>,
    disconnected: oneshot::Receiver<()>,
}

async fn fake_provider() -> (reqwest::Client, mpsc::UnboundedReceiver<Seen>) {
    let ca = include_bytes!("../../tests/fixtures/model-exit-ca.pem");
    let leaf = include_bytes!("../../tests/fixtures/model-exit-server.pem");
    let crypto = Arc::new(rustls::crypto::ring::default_provider());
    let server = rustls::ServerConfig::builder_with_provider(crypto.clone())
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(
            vec![CertificateDer::from_pem_slice(leaf).unwrap()],
            PrivateKeyDer::from_pem_slice(leaf).unwrap(),
        )
        .unwrap();
    let mut roots = rustls::RootCertStore::empty();
    roots
        .add(CertificateDer::from_pem_slice(ca).unwrap())
        .unwrap();
    let tls = rustls::ClientConfig::builder_with_provider(crypto)
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_root_certificates(roots)
        .with_no_client_auth();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let client = http_builder(
        reqwest::Client::builder()
            .tls_backend_preconfigured(tls)
            .resolve(CLAUDE, addr)
            .resolve(CODEX, addr),
    )
    .build()
    .unwrap();
    let (sent, seen) = mpsc::unbounded_channel();
    let acceptor = tokio_rustls::TlsAcceptor::from(Arc::new(server));
    tokio::spawn(async move {
        while let Ok((tcp, _)) = listener.accept().await {
            let (acceptor, sent) = (acceptor.clone(), sent.clone());
            tokio::spawn(async move {
                let mut tls = acceptor.accept(tcp).await.unwrap();
                let (head, rest) = read_head(&mut tls).await.unwrap();
                let mut body = [0; 2];
                body[..rest.len()].copy_from_slice(&rest);
                tls.read_exact(&mut body[rest.len()..]).await.unwrap();
                assert_eq!(&body, b"{}");
                let head = String::from_utf8(head).unwrap();
                let redirect = head.starts_with("POST /v1/messages/count_tokens?beta=true ");
                let (finish, finished) = oneshot::channel();
                let (gone, disconnected) = oneshot::channel();
                sent.send(Seen {
                    head,
                    finish,
                    disconnected,
                })
                .ok()
                .unwrap();
                if redirect {
                    tls.write_all(b"HTTP/1.1 307 Temporary Redirect\r\nLocation: https://chatgpt.com/backend-api/codex/responses\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").await.unwrap();
                    let _ = tls.shutdown().await;
                    return;
                }
                tls.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\nD\r\ndata: first\n\n\r\n").await.unwrap();
                let mut buf = [0];
                tokio::select! {
                    ended = finished => {
                        if ended == Ok(true) {
                            tls.write_all(b"0\r\n\r\n").await.unwrap();
                        }
                        let _ = tls.shutdown().await;
                    }
                    closed = tls.read(&mut buf) => {
                        assert!(!matches!(closed, Ok(n) if n > 0));
                        let _ = gone.send(());
                    }
                }
            });
        }
    });
    (client, seen)
}

type Peer = tokio_tungstenite::WebSocketStream<TcpStream>;

async fn exit_peer(http: &reqwest::Client, provider: Provider) -> (Peer, JoinHandle<()>) {
    let credential = match provider {
        Provider::Claude => r#"{"provider":"claude","access_token":"SYNTHETIC-ONLY-ON-THE-EXIT"}"#,
        Provider::Codex => {
            r#"{"provider":"codex","access_token":"SYNTHETIC-ONLY-ON-THE-EXIT","account_id":"synthetic-saved-account"}"#
        }
    };
    let (hub, seen) = crate::client::tests::serve(vec![(200, credential)]).await;
    let client = Client::new(&hub, "exit-runner-token", 3, 0).unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}", listener.local_addr().unwrap());
    let http = http.clone();
    let exit = tokio::spawn(async move {
        let (ws, _) = tokio_tungstenite::connect_async(url).await.unwrap();
        serve_exit_with(&client, ws, "tunnel", TICKET, true, Ok(http)).await;
        let requests = seen.lock().unwrap();
        assert_eq!(requests.len(), 1);
        let req = requests[0].to_ascii_lowercase();
        assert!(req.starts_with("post /api/v2/runners/me/browser-exit/tunnels/redeem "));
        assert!(req.contains("authorization: bearer exit-runner-token\r\n"));
        assert!(req.contains(TICKET));
    });
    let (tcp, _) = listener.accept().await.unwrap();
    (tokio_tungstenite::accept_async(tcp).await.unwrap(), exit)
}

async fn body(tcp: &mut TcpStream, prefix: Vec<u8>, length: usize) -> Vec<u8> {
    assert!(prefix.len() <= length);
    let mut result = vec![0; length];
    result[..prefix.len()].copy_from_slice(&prefix);
    tcp.read_exact(&mut result[prefix.len()..]).await.unwrap();
    result
}

async fn route(http: &reqwest::Client, provider: Provider) -> (Local, JoinHandle<Vec<u8>>) {
    let (mut exit, exit_task) = exit_peer(http, provider).await;
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let hub = format!("http://{}", listener.local_addr().unwrap());
    let local = start(
        Client::new(&hub, "turn-runner-token", 3, 0).unwrap(),
        "attempt".into(),
        7,
        provider,
        None,
    )
    .await
    .unwrap();
    let relay = tokio::spawn(async move {
        let (mut tcp, _) = listener.accept().await.unwrap();
        let (head, prefix) = read_head(&mut tcp).await.unwrap();
        let head = String::from_utf8(head).unwrap().to_ascii_lowercase();
        assert!(head.starts_with("post /api/v2/runners/me/browser-exit/tunnels "));
        assert!(head.contains("authorization: bearer turn-runner-token\r\n"));
        let length = head
            .lines()
            .find_map(|l| l.strip_prefix("content-length: "))
            .unwrap()
            .parse()
            .unwrap();
        let request: Value = serde_json::from_slice(&body(&mut tcp, prefix, length).await).unwrap();
        assert_eq!(
            request,
            json!({"kind": "model", "attempt_id": "attempt", "generation": 7})
        );
        tcp.write_all(
            format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{GRANT}",
                GRANT.len()
            )
            .as_bytes(),
        )
        .await
        .unwrap();
        drop(tcp);
        let (tcp, _) = listener.accept().await.unwrap();
        let mut bot = tokio_tungstenite::accept_async(tcp).await.unwrap();
        let mut captured = Vec::new();
        loop {
            let (frame, to) = tokio::select! {
                frame = bot.next() => (frame, &mut exit),
                frame = exit.next() => (frame, &mut bot),
            };
            match frame {
                Some(Ok(frame @ (Message::Binary(_) | Message::Text(_)))) => {
                    captured.extend_from_slice(&frame.clone().into_data());
                    if to.send(frame).await.is_err() {
                        break;
                    }
                }
                _ => break,
            }
        }
        drop(exit);
        drop(bot);
        exit_task.await.unwrap();
        captured
    });
    (local, relay)
}

async fn request(
    local: &Local,
    method: &str,
    path: &str,
    host: Option<&str>,
    extra: &str,
) -> TcpStream {
    let authority = local.url.strip_prefix("http://").unwrap();
    let mut tcp = TcpStream::connect(authority).await.unwrap();
    tcp.write_all(format!(
        "{method} {path} HTTP/1.1\r\nHost: {}\r\nAuthorization: Bearer {}\r\nContent-Length: 2\r\n{extra}\r\n{{}}",
        host.unwrap_or(authority), local.token,
    ).as_bytes()).await.unwrap();
    tcp
}

async fn reply(tcp: &mut TcpStream) -> String {
    let mut answer = String::new();
    tcp.read_to_string(&mut answer).await.unwrap();
    answer
}

async fn first_event(tcp: &mut TcpStream) -> String {
    let mut read = Vec::new();
    while !read.ends_with(b"data: first\n\n\r\n") {
        read.push(tcp.read_u8().await.unwrap());
    }
    String::from_utf8(read).unwrap()
}

fn only_exit_holds_credential(captured: Vec<u8>) {
    let text = String::from_utf8(captured).unwrap();
    for value in [
        SECRET,
        ACCOUNT,
        "exit-runner-token",
        "tico-relay-",
        "untrusted-account",
        "untrusted-key",
    ] {
        assert!(!text.contains(value));
    }
}

#[tokio::test]
async fn model_exit_swaps_headers_and_streams_only_completed_responses() {
    tokio::time::timeout(Duration::from_secs(20), async {
        let (http, mut seen) = fake_provider().await;
        let (local, relay) = route(&http, Provider::Codex).await;
        let path = "/backend-api/codex/responses";
        for (method, path, host, extra) in [
            ("GET", path, None, ""),
            ("POST", "/backend-api/codex/accounts", None, ""),
            ("POST", path, Some(CODEX), ""),
            ("POST", path, None, "Transfer-Encoding: chunked\r\n"),
            ("POST", path, None, "Content-Encoding: gzip\r\n"),
            ("POST", path, None, "Content-Length: 2\r\n"),
        ] {
            let answer = reply(&mut request(&local, method, path, host, extra).await).await;
            assert!(answer.starts_with("HTTP/1.1 400") && answer.contains(BAD_REQUEST));
        }
        let mut a = request(&local, "POST", path, None,
            "ChatGPT-Account-Id: untrusted-account\r\nX-Api-Key: untrusted-key\r\nContent-Type: application/json\r\n").await;
        let request = seen.recv().await.unwrap();
        assert!(request.head.starts_with("POST /backend-api/codex/responses HTTP/1.1"));
        assert!(request.head.contains(&format!("\r\nauthorization: Bearer {SECRET}\r\n")));
        assert!(request.head.contains(&format!("\r\nchatgpt-account-id: {ACCOUNT}\r\n")));
        assert!(!request.head.to_ascii_lowercase().contains("x-api-key"));
        assert!(first_event(&mut a).await.contains("Content-Type: text/event-stream"));
        assert!(request.head.contains("\r\ncontent-type: application/json"));
        request.finish.send(true).unwrap();
        assert_eq!(reply(&mut a).await, "0\r\n\r\n");
        only_exit_holds_credential(relay.await.unwrap());

        for cancel in [true, false] {
            let (local, relay) = route(&http, Provider::Claude).await;
            let mut a = self::request(&local, "POST", "/v1/messages?beta=true", None,
                "Anthropic-Version: 2023-06-01\r\nAnthropic-Beta: oauth-2025-04-20\r\n").await;
            let request = seen.recv().await.unwrap();
            assert!(request.head.starts_with("POST /v1/messages?beta=true HTTP/1.1"));
            assert!(request.head.contains(&format!("\r\nauthorization: Bearer {SECRET}\r\n")));
            assert!(request.head.contains("\r\nanthropic-version: 2023-06-01\r\n"));
            assert!(request.head.contains("\r\nanthropic-beta: oauth-2025-04-20\r\n"));
            assert!(!request.head.contains("chatgpt-account-id"));
            assert!(first_event(&mut a).await.starts_with("HTTP/1.1 200"));
            if cancel {
                drop(a);
                request.disconnected.await.unwrap();
            } else {
                request.finish.send(false).unwrap();
                assert_eq!(reply(&mut a).await, "");
            }
            only_exit_holds_credential(relay.await.unwrap());
        }

        let (local, relay) = route(&http, Provider::Claude).await;
        let mut a = self::request(&local, "POST", "/v1/messages/count_tokens?beta=true", None, "").await;
        let response = reply(&mut a).await;
        assert!(response.starts_with("HTTP/1.1 502") && response.contains("model_exit_redirect"));
        assert!(seen.recv().await.unwrap().head.starts_with("POST /v1/messages/count_tokens?beta=true "));
        only_exit_holds_credential(relay.await.unwrap());
        assert!(seen.try_recv().is_err(), "the provider's redirect was followed");
    }).await.unwrap();
}

#[test]
fn claude_message_targets_allow_only_the_exact_beta_query() {
    let client = http_builder(crate::tls::http_builder()).build().unwrap();
    let credential = Credential {
        provider: "claude".into(),
        access_token: SECRET.into(),
        account_id: None,
    };
    for path in ["/v1/messages", "/v1/messages/count_tokens"] {
        for query in [
            "",
            "?beta=true",
            "?beta=false",
            "?beta=true&extra=1",
            "?beta=%74rue",
            "?",
        ] {
            let head =
                format!("POST {path}{query} HTTP/1.1\r\nHost: {CLAUDE}\r\nContent-Length: 2");
            let wire = format!("{head}\r\n\r\n{{}}").into_bytes();
            if query.is_empty() || query == "?beta=true" {
                let local = Request::parse(head.as_bytes(), Provider::Claude, CLAUDE).unwrap();
                let upstream = upstream_request(
                    &client,
                    &credential,
                    local.canonical(Provider::Claude, b"{}".to_vec()),
                )
                .unwrap();
                assert_eq!(
                    upstream.url().as_str(),
                    format!("https://{CLAUDE}{path}{query}")
                );
            } else {
                assert!(Request::parse(head.as_bytes(), Provider::Claude, CLAUDE).is_none());
                assert_eq!(
                    upstream_request(&client, &credential, wire).unwrap_err(),
                    BAD_REQUEST
                );
            }
        }
    }
    assert!(Provider::Codex
        .url("/backend-api/codex/responses?beta=true")
        .is_none());
}

#[test]
fn request_with_content_encoding_is_rejected_before_forwarding() {
    let head =
        format!("POST /backend-api/codex/responses HTTP/1.1\r\nHost: {CODEX}\r\nContent-Length: 2");
    assert!(Request::parse(head.as_bytes(), Provider::Codex, CODEX).is_some());
    let encoded = format!("{head}\r\nCoNtEnT-EnCoDiNg: gzip");
    assert!(Request::parse(encoded.as_bytes(), Provider::Codex, CODEX).is_none());
}

#[tokio::test]
async fn model_exit_caps_the_body_at_32_mib_including_on_the_wire() {
    let limit: usize = 32 * 1024 * 1024;
    for length in [limit, limit + 1] {
        let head =
            format!("POST /v1/messages HTTP/1.1\r\nHost: {CLAUDE}\r\nContent-Length: {length}");
        assert_eq!(
            Request::parse(head.as_bytes(), Provider::Claude, CLAUDE).is_some(),
            length == limit
        );
    }
    let wire_limit = limit + HEAD_BYTES + 4;
    let full = wire_limit / FRAME;
    let tail = wire_limit % FRAME;
    let bytes = Bytes::from(vec![0; FRAME]);
    for extra in [0, 1] {
        let frames = std::iter::repeat_n(bytes.clone(), full).map(|b| Ok(Message::Binary(b)));
        let mut ws = futures_util::stream::iter(frames.chain([
            Ok(Message::Binary(vec![0; tail + extra].into())),
            Ok(Message::Binary(Bytes::new())),
        ]));
        if extra == 0 {
            assert_eq!(read_wire(&mut ws).await.unwrap().len(), wire_limit);
        } else {
            assert_eq!(read_wire(&mut ws).await.unwrap_err(), BAD_REQUEST);
        }
    }
}

#[test]
fn only_allowed_provider_response_headers_are_forwarded() {
    let allowed = [
        ("retry-after", "60"),
        ("x-should-retry", "true"),
        ("request-id", "req-claude"),
        ("anthropic-ratelimit-unified-status", "rejected"),
        ("x-codex-primary-used-percent", "100"),
        ("x-request-id", "req-codex"),
        ("openai-request-id", "req-openai"),
        ("x-openai-request-id", "req-x-openai"),
    ];
    let refused = [
        ("anthropic-ratelimit-requests-limit", "100"),
        ("openai-organization", "org-synthetic"),
        ("set-cookie", "session=private"),
        ("content-length", "17"),
    ];
    let mut headers = HeaderMap::new();
    for (name, value) in allowed.into_iter().chain(refused) {
        headers.insert(name, HeaderValue::from_static(value));
    }
    let head = response_head(reqwest::StatusCode::TOO_MANY_REQUESTS, &headers);
    for (name, value) in allowed {
        assert!(head.contains(&format!("\r\n{name}: {value}\r\n")));
    }
    for (name, _) in refused {
        assert!(!head.contains(&format!("\r\n{name}:")));
    }
}

#[tokio::test]
async fn model_open_skips_ping_and_pong_before_acknowledgment() {
    let mut ws = futures_util::stream::iter([
        Ok(Message::Ping(Bytes::new())),
        Ok(Message::Pong(Bytes::new())),
        Ok(Message::Text(r#"{"ok":true}"#.into())),
        Ok(Message::Binary(Bytes::from_static(b"response"))),
    ]);
    assert_eq!(confirm_open(&mut ws).await, Ok(()));
    assert_eq!(
        ws.next().await.unwrap().unwrap().into_data(),
        b"response"[..]
    );
}

#[tokio::test]
async fn model_exit_rechecks_wire_authority_path_method_and_framing_at_the_exit() {
    tokio::time::timeout(Duration::from_secs(10), async {
        let (http, mut seen) = fake_provider().await;
        for (method, path, host, extra) in [
            ("GET", "/v1/messages", CLAUDE, ""),
            ("POST", "/v1/other", CLAUDE, ""),
            ("POST", "https://api.anthropic.com/v1/messages", CLAUDE, ""),
            ("POST", "/v1/messages", CODEX, ""),
            (
                "POST",
                "/v1/messages",
                CLAUDE,
                "Transfer-Encoding: chunked\r\n",
            ),
            ("POST", "/v1/messages", CLAUDE, "Content-Length: 2\r\n"),
        ] {
            let (mut peer, exit) = exit_peer(&http, Provider::Claude).await;
            assert_eq!(
                peer.next().await.unwrap().unwrap().into_text().unwrap(),
                r#"{"ok":true}"#
            );
            let raw = format!(
                "{method} {path} HTTP/1.1\r\nHost: {host}\r\nContent-Length: 2\r\n{extra}\r\n{{}}"
            );
            peer.send(Message::Binary(raw.into_bytes().into()))
                .await
                .unwrap();
            peer.send(Message::Binary(Vec::new().into())).await.unwrap();
            let status = peer.next().await.unwrap().unwrap().into_text().unwrap();
            assert_eq!(refusal(&status), BAD_REQUEST);
            exit.await.unwrap();
        }
        assert!(seen.try_recv().is_err());
    })
    .await
    .unwrap();
}

#[test]
fn model_exit_replaces_credentials_only_in_the_provider_request() {
    let address = "127.0.0.1:5555";
    let head = format!(
        "POST /backend-api/codex/responses HTTP/1.1\r\nHost: {address}\r\nContent-Length: 2\r\nAuthorization: Bearer placeholder\r\nChatGPT-Account-ID: placeholder\r\nX-Api-Key: placeholder\r\nAccept: text/event-stream"
    );
    let req = Request::parse(head.as_bytes(), Provider::Codex, address).unwrap();
    assert!(Request::parse(head.as_bytes(), Provider::Codex, CODEX).is_none());
    let wire = req.canonical(Provider::Codex, b"{}".to_vec());
    assert!(!String::from_utf8_lossy(&wire).contains("placeholder"));
    let credential = Credential {
        provider: "codex".into(),
        access_token: SECRET.into(),
        account_id: Some(ACCOUNT.into()),
    };
    let client = http_builder(crate::tls::http_builder()).build().unwrap();
    let provider = upstream_request(&client, &credential, wire.clone()).unwrap();
    assert_eq!(
        provider.url().as_str(),
        "https://chatgpt.com/backend-api/codex/responses"
    );
    assert_eq!(
        provider.headers()["authorization"],
        format!("Bearer {SECRET}")
    );
    assert_eq!(provider.headers()["chatgpt-account-id"], ACCOUNT);
    assert_eq!(provider.headers()["accept"], "text/event-stream");
    assert!(!provider.headers().contains_key("x-api-key"));
    assert_eq!(provider.body().unwrap().as_bytes(), Some(b"{}".as_slice()));
    let mut extra = wire;
    extra.extend(b"extra");
    assert_eq!(
        upstream_request(&client, &credential, extra).unwrap_err(),
        BAD_REQUEST
    );
}
