//! A live check for a Linux computer: the runner installs Chrome and chrome-devtools-mcp, serves
//! TICO_BROWSER_URL, and Playwright, chrome-devtools-mcp and /fill all work through it (tests/fixtures/browser-live.mjs).
//! It downloads Chrome, so it is ignored by default: `cargo test browser::live -- --ignored` with Xvfb, a window
//! manager, node and the repository's node_modules.

use super::*;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

const ATTEMPT: &str = "live-run";
const STALE: &str = "gone";

fn logins() -> Value {
    json!({"logins": [
        {"id": "c1", "name": "Acme", "username": "ann@acme.example", "sites": ["127.0.0.1"], "password": "correct horse 1",
         "totp": {"secret": "JBSWY3DPEHPK3PXP", "digits": 6, "period": 30, "algorithm": "SHA1"}},
        {"id": "c2", "name": "Beta", "username": "ben@acme.example", "sites": ["127.0.0.1"], "password": "battery staple 2", "totp": null},
    ]})
}

/// The one hub route a fill reads: logins for the live attempt, `stale_lease` for the ended one.
async fn fake_hub() -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        while let Ok((mut sock, _)) = listener.accept().await {
            tokio::spawn(async move {
                let mut buf = vec![0u8; 16384];
                let mut n = 0;
                while !buf[..n].windows(4).any(|w| w == b"\r\n\r\n") {
                    match sock.read(&mut buf[n..]).await {
                        Ok(0) | Err(_) => return,
                        Ok(m) => n += m,
                    }
                }
                let line = String::from_utf8_lossy(&buf[..n]).lines().next().unwrap_or("").to_string();
                let (status, body) = if line.starts_with(&format!("GET /api/v2/runner-login-credentials?attempt={ATTEMPT} ")) {
                    (200, logins())
                } else if line.starts_with(&format!("GET /api/v2/runner-login-credentials?attempt={STALE} ")) {
                    (409, json!({"error": "stale_lease", "detail": "the attempt is no longer running"}))
                } else {
                    (404, json!({"error": "not_found"}))
                };
                let text = body.to_string();
                let reply = format!(
                    "HTTP/1.1 {status} X\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{text}",
                    text.len()
                );
                let _ = sock.write_all(reply.as_bytes()).await;
            });
        }
    });
    format!("http://127.0.0.1:{port}")
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "installs Chrome; needs Linux, Xvfb, a window manager, node and the repository's node_modules"]
async fn playwright_devtools_mcp_and_fill_work_through_the_endpoint() {
    let dir = std::env::temp_dir().join(format!("tico-browser-live-{}", uuid::Uuid::new_v4().simple()));
    std::fs::create_dir_all(&dir).unwrap();
    let config = Config { path: dir.join("runner.json"), data: json!({"runner_id": "r-live"}).as_object().unwrap().clone() };
    let client = Client::new(&fake_hub().await, "runner-token", 10, 0).unwrap();
    crate::screen::start(config.path.clone(), client.clone());

    let live = lease(&config, &client, "acme-bot", ATTEMPT).await.expect("lease");
    let stale = lease(&config, &client, "acme-bot", STALE).await.expect("lease");
    let dropped = lease(&config, &client, "acme-bot", ATTEMPT).await.expect("lease");
    let dropped_url = dropped.url.clone();
    drop(dropped);
    let bot = by_secret(&live.secret).expect("the live run's secret names its bot").0;
    let ready = bot.ready(Duration::from_secs(600)).await;
    assert!(ready.is_some(), "Chrome did not start: {}", bot.status());

    let mut env = crate::env::Env::new();
    env.insert(URL_ENV.into(), live.url.clone());
    let mcp = devtools_mcp_server(&env).expect("chrome-devtools-mcp is installed when node is");
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let checked = tokio::process::Command::new("node")
        .arg(root.join("tests/fixtures/browser-live.mjs"))
        .current_dir(root.parent().unwrap())
        .env(URL_ENV, &live.url)
        .env("TICO_BROWSER_URL_STALE", &stale.url)
        .env("TICO_BROWSER_URL_DROPPED", &dropped_url)
        .env("TICO_BROWSER_MCP", mcp.to_string())
        .status()
        .await
        .unwrap();
    let after = bot.status();

    drop(live);
    drop(stale);
    shutdown().await;
    crate::screen::shutdown().await;
    let _ = std::fs::remove_dir_all(&dir);
    assert!(checked.success(), "the live checks failed (see their output above)");
    assert_eq!(after["running"], true, "a client's Browser.close must not close the bot's Chrome: {after}");
}
