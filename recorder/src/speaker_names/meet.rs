//! Google Meet's semantic AX projection. Selected, pinned and spotlight tiles are never
//! treated as speaking; an explicit speaking state must identify a participant.
use super::{MeetingAdapter, name};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// Only AX roles and name/state labels enter this projection. No AXValue, chat, captions,
/// presentation contents, document text or screenshots are collected.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Node {
    pub role: String,
    pub label: String,
    pub description: String,
    /// Chromium exposes these through AXDOMClassList. Only the exact participant-name
    /// container is used; generated visual classes are not proof of speaking.
    pub classes: Vec<String>,
    pub children: Vec<Node>,
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
fn person(text: &str) -> Option<(String, bool)> {
    let text = text.trim();
    let lower = text.to_lowercase();
    if others(text)
        || [
            "you",
            "your presentation",
            "presentation",
            "speaking",
            "not speaking",
        ]
        .contains(&lower.as_str())
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
fn control_person(label: &str) -> Option<(String, bool)> {
    ["More options for ", "Pin ", "Unpin ", "Mute "]
        .iter()
        .find_map(|prefix| label.strip_prefix(prefix).and_then(person))
}
fn speaking(label: &str) -> Option<(String, bool)> {
    label
        .strip_suffix(" is speaking")
        .or_else(|| label.strip_suffix(", speaking"))
        .or_else(|| label.strip_prefix("Speaking: "))
        .and_then(person)
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
/// The walker reads text only after reaching a participant container. Container and control
/// labels also supply names when a browser flattens the participant's static-text child.
fn collect(root: &Node) -> (BTreeSet<String>, BTreeSet<String>, BTreeSet<String>) {
    fn tile_name(node: &Node) -> Option<(String, bool)> {
        if node.classes.iter().any(|class| class == "XEazBc") {
            fn text(node: &Node) -> Option<(String, bool)> {
                if node.role == "AXStaticText" {
                    return person(&node.label);
                }
                node.children.iter().find_map(text)
            }
            return text(node);
        }
        node.children.iter().find_map(tile_name)
    }
    fn visit(
        node: &Node,
        tile: Option<&str>,
        roster: bool,
        people: &mut BTreeSet<String>,
        locals: &mut BTreeSet<String>,
        active: &mut BTreeSet<String>,
    ) {
        if private_branch(&node.role, &node.label) {
            return;
        }
        let roster = roster
            || ["Participants", "People", "In this call", "Contributors"]
                .contains(&node.label.as_str());
        let control = node
            .children
            .iter()
            .filter(|n| n.role == "AXButton")
            .find_map(|n| control_person(&n.label));
        let own = control
            .or_else(|| {
                (node.role == "AXListItem" && roster)
                    .then(|| person(&node.label))
                    .flatten()
            })
            .or_else(|| {
                node.classes
                    .iter()
                    .any(|c| c == "dkjMxf")
                    .then(|| tile_name(node))
                    .flatten()
            });
        if let Some((name, local)) = &own {
            people.insert(name.clone());
            if *local {
                locals.insert(name.clone());
            }
        }
        let tile = own.as_ref().map(|(s, _)| s.as_str()).or(tile);
        for label in [&node.label, &node.description] {
            if let Some((name, local)) = speaking(label) {
                people.insert(name.clone());
                if local {
                    locals.insert(name.clone());
                }
                active.insert(name);
            } else if label.eq_ignore_ascii_case("speaking") {
                if let Some(name) = tile {
                    active.insert(name.into());
                }
            }
        }
        // A self tile can carry its identity separately from its more-options control.
        if tile.is_some()
            && [node.label.as_str(), node.description.as_str()]
                .iter()
                .any(|s| ["You", "Your microphone", "You are speaking"].contains(s))
        {
            locals.insert(tile.unwrap().into());
        }
        for child in &node.children {
            visit(child, tile, roster, people, locals, active);
        }
    }
    let (mut people, mut locals, mut active) = (BTreeSet::new(), BTreeSet::new(), BTreeSet::new());
    visit(root, None, false, &mut people, &mut locals, &mut active);
    active.retain(|name| !locals.contains(name));
    (people, locals, active)
}
impl MeetingAdapter for GoogleMeet {
    fn detect_window(&self, _title: &str, document_url: &str) -> bool {
        let Ok(url) = reqwest::Url::parse(document_url) else {
            return false;
        };
        let path = url.path().trim_matches('/');
        let code = path.strip_prefix("meet/").unwrap_or(path);
        url.scheme() == "https"
            && url.host_str() == Some("meet.google.com")
            && Some(code).is_some_and(|code| {
                let parts: Vec<_> = code.split('-').collect();
                parts.len() == 3
                    && parts.iter().map(|p| p.len()).eq([3, 4, 3])
                    && parts
                        .iter()
                        .all(|part| part.bytes().all(|c| c.is_ascii_lowercase()))
            })
    }
    fn read_participants(&self, root: &Node) -> (Vec<String>, Vec<String>) {
        let (people, local, _) = collect(root);
        (
            people.into_iter().take(100).collect(),
            local.into_iter().collect(),
        )
    }
    fn read_active_speakers(&self, root: &Node) -> Vec<String> {
        collect(root).2.into_iter().take(100).collect()
    }
}
