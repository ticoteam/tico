//! A run against the real Chrome on this computer (`launch::chrome`) and a page served here: not run by default
//! (`--ignored`).

use std::io::{Read, Write};
use std::time::Duration;

use base64::Engine;

use super::verbs::Session;
use super::{fill, launch};

const LOGIN: &str = r#"<!doctype html><html><head><title>Practice sign-in</title></head><body>
<h1>Sign in</h1><p>Use your practice account.</p>
<form id="f" onsubmit="event.preventDefault(); document.getElementById('out').textContent = 'Signed in as ' + document.getElementById('email').value + ' with ' + document.getElementById('pw').value.length + ' characters';">
<label for="email">Email</label><input id="email" type="email" placeholder="you@example.com" required>
<label>Password <input id="pw" type="password"></label>
<button type="button" onclick="const p = document.getElementById('pw'); p.type = p.type === 'password' ? 'text' : 'password';">Show</button>
<label><input type="checkbox" id="keep"> Keep me signed in</label>
<select id="state" aria-label="State"><option>AL</option><option selected>CA</option></select>
<button type="submit">Sign in</button></form>
<p id="out"></p><a href="/other?token=link-canary-7#top">Forgot?</a>
</body></html>"#;

/// A one-page server on 127.0.0.1 for as long as the test runs.
fn serve() -> u16 {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let mut stream = stream;
            let mut buf = [0u8; 4096];
            let _ = stream.read(&mut buf);
            let reply = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{LOGIN}",
                LOGIN.len()
            );
            let _ = stream.write_all(reply.as_bytes());
        }
    });
    port
}

/// The ref of the element line naming `name`.
fn ref_of(text: &str, name: &str) -> String {
    let line = text
        .lines()
        .find(|l| l.contains(&format!("\"{name}\"")) && l.starts_with("[e"))
        .unwrap_or_else(|| panic!("no {name} in\n{text}"));
    line[1..line.find(']').unwrap()].to_string()
}

/// A JPEG's width and height, from its frame header.
fn jpeg_size(data: &[u8]) -> Option<(u32, u32)> {
    let mut i = 2;
    while i + 9 < data.len() {
        if data[i] != 0xFF {
            return None;
        }
        let marker = data[i + 1];
        let len = u16::from_be_bytes([data[i + 2], data[i + 3]]) as usize;
        if (0xC0..=0xC2).contains(&marker) {
            let h = u16::from_be_bytes([data[i + 5], data[i + 6]]) as u32;
            let w = u16::from_be_bytes([data[i + 7], data[i + 8]]) as u32;
            return Some((w, h));
        }
        i += 2 + len;
    }
    None
}

#[tokio::test(flavor = "multi_thread")]
#[ignore]
async fn a_real_chrome_opens_reads_types_fills_and_refuses_the_wrong_site() {
    let Some(exe) = launch::chrome() else {
        eprintln!("no Google Chrome here");
        return;
    };
    let port = serve();
    let dir = std::env::temp_dir().join(format!("tico-live-{}", uuid::Uuid::new_v4().simple()));
    let launched = launch::launch(&exe, &dir.join("profile"), None)
        .await
        .unwrap();
    let mut s = Session::start(launched, None, dir.join("files"))
        .await
        .unwrap();
    let here = vec![format!("http://127.0.0.1:{port}")];

    s.open(&format!("http://127.0.0.1:{port}/login"), &here, false)
        .await
        .unwrap();
    assert_eq!(
        s.open("https://example.com/", &here, false)
            .await
            .err()
            .map(|f| f.code),
        Some("not_on_site")
    );
    let ua = s.eval("navigator.userAgent", true).await.unwrap();
    let ua = ua["value"].as_str().unwrap_or_default();
    assert!(ua.contains(" Chrome/") && !ua.contains("Headless"), "{ua}");
    let text = s.read().await.unwrap().text.unwrap();
    eprintln!("{text}");
    assert!(text.starts_with(&format!("Practice sign-in — http://127.0.0.1:{port}/login")));
    assert!(text.contains("# Sign in"));
    assert!(text.contains("placeholder \"you@example.com\""));
    assert!(text.contains("checkbox \"Keep me signed in\" (unchecked)"));
    assert!(text.contains("select \"State\" = \"CA\" options: \"AL\", \"CA\""));
    assert!(
        text.contains("link \"Forgot?\" -> http://127.0.0.1:"),
        "{text}"
    );
    assert!(
        text.contains("/other\n") || text.ends_with("/other"),
        "{text}"
    );
    assert!(!text.contains("link-canary-7"), "{text}");
    let (email, pw, button) = (
        ref_of(&text, "Email"),
        ref_of(&text, "Password"),
        ref_of(&text, "Sign in"),
    );

    s.type_text(&email, "ops@acme.example", false)
        .await
        .unwrap();
    let wrong = fill::fill(&mut s, &here, "password", Some(&email), "pw-canary-9").await;
    assert_eq!(wrong.err().map(|f| f.code), Some("not_a_password_field"));
    fill::fill(&mut s, &here, "password", Some(&pw), "pw-canary-9")
        .await
        .unwrap();
    let after = s.read().await.unwrap().text.unwrap();
    assert!(
        after.contains("textbox \"Password\" (password, focused, filled)"),
        "{after}"
    );
    assert!(
        !after.contains("\nPassword\n"),
        "a label is not repeated: {after}"
    );
    assert!(!after.contains("pw-canary-9"));

    // The page's "show" eye makes the password field a text field: it still reads as filled, never its value.
    s.click(&ref_of(&after, "Show")).await.unwrap();
    let shown = s
        .eval("document.getElementById('pw').type", true)
        .await
        .unwrap();
    assert_eq!(shown["value"], "text");
    let flipped = s.read().await.unwrap().text.unwrap();
    let line = flipped
        .lines()
        .find(|l| l.contains("textbox \"Password\""))
        .unwrap_or_else(|| panic!("no password line in\n{flipped}"));
    assert!(
        line.contains("filled") && !line.contains("(password"),
        "{line}"
    );
    assert!(!flipped.contains("pw-canary-9"), "{flipped}");
    // A screenshot covers the shown value (it differs from one taken without covering), and puts the field back.
    let style = "document.getElementById('pw').getAttribute('style')";
    let before = s.eval(style, true).await.unwrap();
    let raw = s.capture(false).await.unwrap().image.unwrap();
    let covered = s.screenshot().await.unwrap().image.unwrap();
    assert_ne!(raw, covered, "the filled field is covered in the picture");
    let after_shot = s.eval(style, true).await.unwrap();
    assert_eq!(
        before["value"], after_shot["value"],
        "the field's style is put back"
    );
    s.click(&button).await.unwrap();
    let signed = s.read().await.unwrap().text.unwrap();
    assert!(
        signed.contains("Signed in as ops@acme.example with 11 characters"),
        "{signed}"
    );

    // The same page under another host name is not the tool's site: nothing is typed. (A name without a dot is
    // never a site; Chrome resolves `*.localhost` to this computer.)
    let both = vec![
        format!("http://127.0.0.1:{port}"),
        format!("http://practice.localhost:{port}"),
    ];
    s.open(
        &format!("http://practice.localhost:{port}/login"),
        &both,
        false,
    )
    .await
    .unwrap();
    let text = s.read().await.unwrap().text.unwrap();
    let pw = ref_of(&text, "Password");
    let refused = fill::fill(&mut s, &here, "password", Some(&pw), "pw-canary-9").await;
    assert_eq!(refused.err().map(|f| f.code), Some("not_on_site"));
    assert_eq!(
        s.click("e999").await.err().map(|f| f.code),
        Some("stale_ref")
    );

    let shot = s.screenshot().await.unwrap().image.unwrap();
    let jpeg = base64::engine::general_purpose::STANDARD
        .decode(&shot)
        .unwrap();
    assert!(jpeg.len() <= 1_000_000);
    let width = s.eval("innerWidth", true).await.unwrap()["value"]
        .as_u64()
        .unwrap() as u32;
    let (w, _) = jpeg_size(&jpeg).unwrap();
    assert_eq!(w, width, "one image pixel per page pixel");

    s.close().await;
    tokio::time::sleep(Duration::from_millis(200)).await;
    let _ = std::fs::remove_dir_all(&dir);
}
