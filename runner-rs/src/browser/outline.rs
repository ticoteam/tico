//! The page as text for a bot (`read`): `outline.js` walks the DOM in the runner's isolated world and returns the
//! page as items (text, headings, elements, frames); this file writes them as lines. An element line is
//! `[e12] textbox "Email" (email, required) placeholder "you@example.com" = "ops@acme.example"`; the ref stays valid
//! until the next read or navigation.

use serde_json::Value;

use crate::util::text_of;

pub const SCRIPT: &str = include_str!("outline.js");

/// The most a read answers.
pub const LIMIT: usize = 30_000;
const CUT: &str = "… (the page goes on: scroll, or read again after acting)";

fn quoted(s: &str) -> String {
    format!("{s:?}")
}

/// One item as its line, or None for an item this cannot write.
fn line(item: &Value) -> Option<String> {
    match item["k"].as_str()? {
        "t" => Some(text_of(&item["s"])).filter(|s| !s.is_empty()),
        "h" => {
            let level = item["l"].as_u64().unwrap_or(2).clamp(1, 6) as usize;
            Some(format!("{} {}", "#".repeat(level), text_of(&item["s"])))
        }
        "f" => Some(format!("[frame] {}", text_of(&item["host"]))),
        "e" => {
            let n = item["r"].as_u64()?;
            let mut out = format!("[e{n}] {}", text_of(&item["role"]));
            let name = text_of(&item["name"]);
            if !name.is_empty() {
                out.push(' ');
                out.push_str(&quoted(&name));
            }
            let states: Vec<String> = item["st"]
                .as_array()
                .into_iter()
                .flatten()
                .map(text_of)
                .filter(|s| !s.is_empty())
                .collect();
            if !states.is_empty() {
                out.push_str(&format!(" ({})", states.join(", ")));
            }
            let ph = text_of(&item["ph"]);
            if !ph.is_empty() {
                out.push_str(&format!(" placeholder {}", quoted(&ph)));
            }
            if let Some(v) = item.get("v").filter(|v| !v.is_null()) {
                out.push_str(&format!(" = {}", quoted(&text_of(v))));
            }
            if let Some(opts) = item["opts"].as_array().filter(|o| !o.is_empty()) {
                let list: Vec<String> = opts.iter().map(|o| quoted(&text_of(o))).collect();
                out.push_str(&format!(" options: {}", list.join(", ")));
                if let Some(more) = item["more"].as_u64().filter(|m| *m > 0) {
                    out.push_str(&format!(" (+{more} more)"));
                }
            }
            Some(out)
        }
        _ => None,
    }
}

/// The read's text: `<title> — <url>`, then the page in reading order, at most `LIMIT` characters.
pub fn format(title: &str, url: &str, outline: &Value) -> String {
    let title = if title.trim().is_empty() {
        "(no title)"
    } else {
        title.trim()
    };
    let mut out = format!("{title} — {url}");
    // The words of the last line, and where it starts in `out`, when it is a text line.
    let mut last = String::new();
    let mut last_text: Option<usize> = None;
    let mut cut = outline["cut"] == true;
    let room = LIMIT - CUT.len() - 1;
    for item in outline["items"].as_array().into_iter().flatten() {
        let Some(l) = line(item) else { continue };
        let text = item["k"] == "t";
        let name = text_of(&item["name"]);
        // A field's label shown just before or after it is already its name; the same words twice say nothing new.
        if text && l == last {
            continue;
        }
        if !text && !name.is_empty() && name == last {
            if let Some(at) = last_text.take() {
                out.truncate(at);
            }
        }
        if out.len() + 1 + l.len() > room {
            cut = true;
            break;
        }
        last_text = text.then_some(out.len());
        out.push('\n');
        out.push_str(&l);
        last = if text { l } else { name };
    }
    if outline["items"].as_array().is_none_or(|i| i.is_empty()) {
        out.push_str("\n(the page shows nothing yet)");
    }
    if cut {
        out.push('\n');
        out.push_str(CUT);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// A sign-in page as outline.js returns it.
    fn sign_in() -> Value {
        json!({"cut": false, "items": [
            {"k": "h", "l": 1, "s": "Sign in to Acme"},
            {"k": "t", "s": "Welcome back."},
            {"k": "t", "s": "Email"},
            {"k": "e", "r": 1, "role": "textbox", "name": "Email", "ph": "you@example.com", "st": ["email", "required"], "v": "ops@acme.example"},
            {"k": "e", "r": 2, "role": "textbox", "name": "Password", "st": ["password", "filled"]},
            {"k": "t", "s": "Password"},
            {"k": "e", "r": 3, "role": "checkbox", "name": "Keep me signed in", "st": ["unchecked"]},
            {"k": "t", "s": "Keep me signed in"},
            {"k": "e", "r": 4, "role": "select", "name": "State", "v": "CA", "opts": ["AL", "CA"], "more": 48},
            {"k": "e", "r": 5, "role": "button", "name": "Sign in", "st": ["disabled"]},
            {"k": "t", "s": "Welcome back."},
            {"k": "e", "r": 6, "role": "link", "name": "Forgot \"password\"?"},
            {"k": "f", "host": "challenges.cloudflare.com"},
            {"k": "x", "s": "an item from a newer script"}
        ]})
    }

    #[test]
    fn a_sign_in_page_reads_as_numbered_elements_in_order() {
        let text = format("Acme", "https://acme.example/in", &sign_in());
        assert_eq!(
            text,
            [
                "Acme — https://acme.example/in",
                "# Sign in to Acme",
                "Welcome back.",
                "[e1] textbox \"Email\" (email, required) placeholder \"you@example.com\" = \"ops@acme.example\"",
                "[e2] textbox \"Password\" (password, filled)",
                "[e3] checkbox \"Keep me signed in\" (unchecked)",
                "[e4] select \"State\" = \"CA\" options: \"AL\", \"CA\" (+48 more)",
                "[e5] button \"Sign in\" (disabled)",
                "Welcome back.",
                "[e6] link \"Forgot \\\"password\\\"?\"",
                "[frame] challenges.cloudflare.com",
            ]
            .join("\n")
        );
    }

    #[test]
    fn a_long_page_is_cut_with_a_note_and_an_empty_one_says_so() {
        let items: Vec<Value> = (0..2000)
            .map(|i| json!({"k": "t", "s": format!("row {i} of a long table of numbers")}))
            .collect();
        let text = format(
            "",
            "https://acme.example/",
            &json!({"items": items, "cut": false}),
        );
        assert!(text.len() <= LIMIT, "{}", text.len());
        assert!(text.starts_with("(no title) — https://acme.example/\nrow 0 "));
        assert!(text.ends_with(CUT));
        let empty = format(
            "New Tab",
            "about:blank",
            &json!({"items": [], "cut": false}),
        );
        assert_eq!(empty, "New Tab — about:blank\n(the page shows nothing yet)");
        let script_cut = format(
            "A",
            "https://a.example/",
            &json!({"items": [{"k": "t", "s": "x"}], "cut": true}),
        );
        assert!(script_cut.ends_with(CUT));
    }

    #[test]
    fn the_script_reads_no_stored_state_and_never_a_password_value() {
        for banned in [
            "cookie",
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "caches",
        ] {
            assert!(!SCRIPT.contains(banned), "{banned}");
        }
        // A password field's value is never put in an item: only whether it holds one.
        assert!(SCRIPT.contains("if (c.value) st.push('filled')"));
    }
}
