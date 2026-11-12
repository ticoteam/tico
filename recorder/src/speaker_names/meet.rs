//! Google Meet's AX projection. The only speaking signal is the level indicator Meet draws in
//! a People-panel row (captured live in Chrome, Nov 2026). Pinned, spotlight and layout state
//! are never treated as speech, and an unknown indicator state is no evidence either way.
use super::{MeetingAdapter, Observation, name};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// Only AX roles, DOM classes from [`CLASSES`] and name labels enter this projection. AXValue is
/// read only for static text inside a participant row or video-tile name container.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Node {
    pub role: String,
    pub label: String,
    pub description: String,
    /// Chromium exposes these through AXDOMClassList.
    pub classes: Vec<String>,
    pub children: Vec<Node>,
}

/// Meet's generated class names change with its releases; every token the adapter relies on
/// lives here. A missing structure yields no evidence, so labels stay "Speaker N".
pub struct Classes {
    /// People-panel row; its AXDescription is the participant's name.
    pub row: &'static [&'static str],
    /// The row's audio level indicator.
    pub indicator: &'static str,
    pub silent: &'static str,
    /// Level steps Meet cycles through (~250 ms) while that participant is heard.
    pub levels: &'static [&'static str],
    /// Video tile and its name container (names only; tiles carry no speaking state in AX).
    pub tile: &'static str,
    pub tile_name: &'static str,
}
pub const CLASSES: Classes = Classes {
    row: &["KV1GEc", "cxdMu"],
    indicator: "IisKdb",
    silent: "gjg47c",
    levels: &["OgVli", "wEsLMd", "HX2H7", "Oaajhc"],
    tile: "dkjMxf",
    tile_name: "XEazBc",
};
/// The AX walker keeps only these classes.
pub fn kept_class(class: &str) -> bool {
    let c = &CLASSES;
    c.row.contains(&class)
        || c.levels.contains(&class)
        || [c.indicator, c.silent, c.tile, c.tile_name].contains(&class)
}
fn has(node: &Node, class: &str) -> bool {
    node.classes.iter().any(|c| c == class)
}
pub fn is_row(node: &Node) -> bool {
    CLASSES.row.iter().all(|class| has(node, class))
}
/// Static text under these nodes is a participant name or roster marker, never chat.
pub fn name_container(node: &Node) -> bool {
    is_row(node) || has(node, CLASSES.tile_name)
}

pub struct GoogleMeet;
pub fn window_title(title: &str) -> bool {
    let title = title.to_lowercase();
    title.starts_with("meet – ")
        || title.starts_with("meet - ")
        || title.starts_with("meet — ")
        || title.contains("google meet")
        || title.contains("meet.google.com/")
}
fn others(text: &str) -> bool {
    let text = text.trim().to_lowercase();
    text.split_once(' ').is_some_and(|(n, rest)| {
        n.parse::<usize>().is_ok() && ["others", "other", "more"].contains(&rest)
    })
}
/// A participant name and whether it marks the local person.
fn person(text: &str) -> Option<(String, bool)> {
    let text = text.trim();
    let lower = text.to_lowercase();
    if others(text)
        || ["you", "(you)", "your presentation", "presentation"].contains(&lower.as_str())
        || lower.ends_with("'s presentation")
        || lower.ends_with(" is presenting")
    {
        return None;
    }
    let (text, local) = text
        .strip_suffix(" (You)")
        .or_else(|| text.strip_suffix(" (you)"))
        .map_or((text, false), |s| (s, true));
    name(text).map(|name| (name, local))
}
fn self_marker(text: &str) -> bool {
    ["you", "(you)"].contains(&text.trim().to_lowercase().as_str())
}
pub fn private_branch(role: &str, label: &str) -> bool {
    matches!(
        role,
        "AXTextArea" | "AXTextField" | "AXWebArea" | "AXDocument"
    ) || [
        "In-call messages",
        "Chat",
        "Messages",
        "Captions",
        "Shared screen",
        "Presentation content",
    ]
    .iter()
    .any(|s| label.eq_ignore_ascii_case(s))
}

#[derive(Clone, Debug, PartialEq)]
pub struct Row {
    pub name: String,
    /// Marked "(You)" (English Meet only).
    pub local: bool,
    /// Has a button, such as the mute control Meet gives every row but the local person's.
    pub control: bool,
    /// None when the row has no indicator or its state is not one of the known classes.
    pub speaking: Option<bool>,
}
fn indicator(node: &Node) -> Option<bool> {
    if has(node, CLASSES.indicator) {
        let level = CLASSES.levels.iter().any(|class| has(node, class));
        return match (has(node, CLASSES.silent), level) {
            (true, false) => Some(false),
            (false, true) => Some(true),
            _ => None,
        };
    }
    // Several indicators in one row: speaking if any is, unknown if none is readable.
    node.children
        .iter()
        .filter_map(indicator)
        .reduce(|a, b| a || b)
}
fn row(node: &Node) -> Option<Row> {
    let (name, mut local) = person(&node.description).or_else(|| person(&node.label))?;
    fn marked(node: &Node) -> bool {
        (node.role == "AXStaticText" && self_marker(&node.label))
            || node.children.iter().any(marked)
    }
    local |= node.children.iter().any(marked);
    fn button(node: &Node) -> bool {
        node.role == "AXButton" || node.children.iter().any(button)
    }
    Some(Row {
        name,
        local,
        control: node.children.iter().any(button),
        speaking: indicator(node),
    })
}
/// People-panel rows, in tree order. Paths move on layout changes, so rows are keyed by name.
pub fn rows(root: &Node) -> Vec<Row> {
    fn visit(node: &Node, out: &mut Vec<Row>) {
        if private_branch(&node.role, &node.label) || out.len() >= 200 {
            return;
        }
        if is_row(node) {
            out.extend(row(node));
            return;
        }
        for child in &node.children {
            visit(child, out);
        }
    }
    let mut out = vec![];
    visit(root, &mut out);
    out
}
/// Names in video tiles. The same person can appear in several tiles.
fn tile_names(root: &Node, out: &mut BTreeSet<(String, bool)>) {
    if private_branch(&root.role, &root.label) {
        return;
    }
    if has(root, CLASSES.tile_name) {
        fn text(node: &Node) -> Option<(String, bool)> {
            if node.role == "AXStaticText" {
                return person(&node.label);
            }
            node.children.iter().find_map(text)
        }
        out.extend(text(root));
        return;
    }
    for child in &root.children {
        tile_names(child, out);
    }
}
/// Which rows are the local person's: the "(You)" marker, or else the one row without a mute
/// control when every other row has one (the captured layout, in any language). Empty when
/// neither identifies anyone.
fn local_rows(rows: &[Row]) -> (Vec<bool>, &'static str) {
    if rows.iter().any(|r| r.local) {
        return (rows.iter().map(|r| r.local).collect(), "marker");
    }
    let bare = rows.iter().filter(|r| !r.control).count();
    if rows.len() >= 2 && bare == 1 {
        return (rows.iter().map(|r| !r.control).collect(), "structure");
    }
    (vec![], if rows.is_empty() { "" } else { "none" })
}
impl MeetingAdapter for GoogleMeet {
    fn app(&self) -> &'static str {
        "Meet"
    }
    fn window_title(&self, title: &str) -> bool {
        window_title(title)
    }
    fn kept_class(&self, class: &str) -> bool {
        kept_class(class)
    }
    fn name_container(&self, node: &Node) -> bool {
        name_container(node)
    }
    fn private_branch(&self, role: &str, label: &str) -> bool {
        private_branch(role, label)
    }
    fn detect_window(&self, _title: &str, document_url: &str) -> bool {
        let Ok(url) = reqwest::Url::parse(document_url) else {
            return false;
        };
        let path = url.path().trim_matches('/');
        let code = path.strip_prefix("meet/").unwrap_or(path);
        let parts: Vec<_> = code.split('-').collect();
        url.scheme() == "https"
            && url.host_str() == Some("meet.google.com")
            && parts.len() == 3
            && parts.iter().map(|p| p.len()).eq([3, 4, 3])
            && parts
                .iter()
                .all(|part| part.bytes().all(|c| c.is_ascii_lowercase()))
    }
    fn observe(&self, root: &Node) -> Observation {
        let rows = rows(root);
        let (local, local_by) = local_rows(&rows);
        let mut names = BTreeSet::new();
        tile_names(root, &mut names);
        names.extend(
            rows.iter()
                .zip(local.iter().chain(std::iter::repeat(&false)))
                .map(|(r, local)| (r.name.clone(), *local)),
        );
        let locals: BTreeSet<_> = names
            .iter()
            .filter(|(_, local)| *local)
            .map(|(n, _)| n.clone())
            .collect();
        let participants: BTreeSet<_> = names.into_iter().map(|(n, _)| n).collect();
        // Without knowing which row is the local person's, any speaker could be the mic: rows
        // still give names, but no speaking state.
        let known = !local.is_empty();
        // A name with several rows (one account on two devices) speaks if any of its rows does.
        let active: BTreeSet<_> = rows
            .iter()
            .filter(|r| known && r.speaking == Some(true) && !locals.contains(&r.name))
            .map(|r| r.name.clone())
            .collect();
        Observation {
            participants: participants.into_iter().take(100).collect(),
            self_names: locals.into_iter().collect(),
            active: active.into_iter().take(100).collect(),
            indicators: known && rows.iter().any(|r| r.speaking.is_some()),
            rows: rows.len(),
            local_by,
        }
    }
}
